"""Bounded PDF table/OCR reader. Original PDF bytes stay in the price evidence store."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def read_pdf(raw, source):
    if not raw.startswith(b'%PDF-'):
        raise ValueError('Файл не является PDF.')
    with tempfile.TemporaryDirectory(prefix='estimator-pdf-') as directory:
        path=Path(directory)/'source.pdf';path.write_bytes(raw);output=Path(directory)/'result.json'
        command=([sys.executable,'--price-pdf-worker'] if getattr(sys,'frozen',False)
                 else [sys.executable,str(Path(__file__).resolve())])
        args={'sheet':source['sheet'],'pdf_mode':source.get('pdf_mode','text'),
              'ocr_language':source.get('ocr_language','rus+eng')}
        try:
            result=subprocess.run(command+[str(path),json.dumps(args),str(output)],capture_output=True,timeout=65)
        except subprocess.TimeoutExpired:
            raise ValueError('Чтение страницы PDF превысило 65 секунд. Разделите документ или используйте CSV.') from None
        if result.returncode or not output.is_file() or output.stat().st_size>8*1024*1024:
            raise ValueError('Не удалось безопасно прочитать PDF. Проверьте файл или используйте CSV.')
        try: value=json.loads(output.read_bytes())
        except (ValueError,UnicodeError):raise ValueError('Не удалось прочитать результат PDF.') from None
        if value.get('error'):raise ValueError(value['error'])
        return value


def extract(path, source):
    try:import pdfplumber
    except ImportError:raise ValueError('Для PDF установите дополнения: SETUP_FEATURES.command / SETUP_FEATURES.bat.') from None
    page_number=source['sheet'];mode=source.get('pdf_mode','text');warnings=[]
    with pdfplumber.open(path) as pdf:
        if not 1<=len(pdf.pages)<=80:raise ValueError('Поддерживается PDF от 1 до 80 страниц. Разделите большой прайс.')
        if not 0<=page_number<len(pdf.pages):raise ValueError('Страница отсутствует в PDF.')
        sheets=[{'name':f'Страница {i+1}','hidden':False} for i in range(len(pdf.pages))]
        page=pdf.pages[page_number]
        if mode=='ocr':
            text=ocr(path,page_number,source.get('ocr_language','rus+eng'))
            values=[re.split(r'\s{2,}|\t',line.strip()) for line in text.splitlines() if line.strip()]
            warnings.append('OCR может ошибаться в цифрах и разделителях. Сверьте каждую выбранную цену со сканом; при необходимости исправьте её вручную в материалах.')
        else:
            tables=page.extract_tables()
            if tables:
                values=[row for table in tables for row in table]
                if len(tables)>1:warnings.append('На странице несколько таблиц: строки объединены. Проверьте повторные заголовки и условия каждой таблицы.')
            else:
                text=page.extract_text(layout=True) or ''
                values=[re.split(r'\s{2,}|\t',line.strip()) for line in text.splitlines() if line.strip()]
                warnings.append('Сетка таблицы не найдена: столбцы выделены по пробелам. Проверьте сопоставление; для скана выберите OCR.')
        if not values:raise ValueError('Текст не найден. Выберите режим OCR для скана или получите CSV/XLSX у поставщика.')
        if len(values)>5001 or any(len(row)>100 for row in values):raise ValueError('Слишком большая таблица PDF.')
        rows=[]
        for i,row in enumerate(values,1):
            cells=[str(cell or '').replace('\n',' ').strip() for cell in row]
            if any(len(cell)>10000 for cell in cells):raise ValueError('Слишком длинная ячейка PDF.')
            rows.append({'number':f'{page_number+1}:{i}','values':cells,'formulas':[]})
    warnings.append('Обрабатывается только выбранная страница. Дата получения файла не подтверждает актуальность цен.')
    return {'sheets':sheets,'rows':rows,'warnings':warnings}


def ocr(path, page_number, language):
    import shutil
    binary=shutil.which('tesseract')
    if not binary:raise ValueError('Не установлен Tesseract. См. docs/OPTIONAL_FEATURES.md, раздел OCR.')
    try:
        languages=subprocess.run([binary,'--list-langs'],capture_output=True,text=True,timeout=10)
        available=set(languages.stdout.splitlines())
        if any(item not in available for item in language.split('+')):
            raise ValueError('Для OCR отсутствует язык '+language+'. Установите языковой пакет Tesseract или выберите английский.')
        import pypdfium2 as pdfium
        with tempfile.TemporaryDirectory(prefix='estimator-ocr-') as directory:
            image=Path(directory)/'page.png'
            with pdfium.PdfDocument(path) as document:
                page=document[page_number]
                width,height=page.get_size();scale=2.5
                if width*height*scale*scale>25000000:raise ValueError('Страница слишком велика для OCR.')
                bitmap=page.render(scale=scale)
                bitmap.to_pil().save(image);bitmap.close();page.close()
            result=subprocess.run([binary,str(image),'stdout','-l',language,'--psm','3','-c','preserve_interword_spaces=1'],capture_output=True,timeout=40)
        if result.returncode:raise ValueError('Tesseract не смог распознать страницу.')
        if len(result.stdout)>2*1024*1024:raise ValueError('Слишком большой результат OCR.')
        return result.stdout.decode('utf-8')
    except subprocess.TimeoutExpired:raise ValueError('OCR превысил время ожидания. Используйте меньшую страницу.') from None
    except ImportError:raise ValueError('Для OCR нужны дополнения PDF: запустите SETUP_FEATURES.') from None


def worker(argv):
    try:
        value=extract(argv[0],json.loads(argv[1]))
    except ValueError as exc:value={'error':str(exc)}
    except Exception:value={'error':'PDF повреждён, зашифрован или имеет неподдерживаемую структуру.'}
    if len(argv)>2:Path(argv[2]).write_text(json.dumps(value,ensure_ascii=True),encoding='utf-8')
    else:print(json.dumps(value,ensure_ascii=True))
    return 0

if __name__=='__main__':raise SystemExit(worker(sys.argv[1:]))
