"""Bounded XLSX reader/writer and branded PDF reports. No macros or formula execution."""
import base64
from datetime import date
from decimal import Decimal
from html import escape
from io import BytesIO
from pathlib import Path
import posixpath
import re
import threading
import xml.etree.ElementTree as ET
import zipfile

import engine
from fns_registry import customer_lines
from local_store import normalize_entity, normalize_project

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKGREL='http://schemas.openxmlformats.org/package/2006/relationships'
PDF_LOCK=threading.RLock()


def decode_file(value, maximum=8*1024*1024):
    if not isinstance(value,str) or len(value)>maximum*4//3+16:
        raise ValueError('Файл превышает допустимый размер.')
    try:data=base64.b64decode(value,validate=True)
    except (ValueError,TypeError):raise ValueError('Не удалось прочитать файл.') from None
    if len(data)>maximum:raise ValueError('Файл слишком велик.')
    return data


def xlsx_read(body):
    data=decode_file(body.get('content',''))
    def xml(z,name):
        raw=z.read(name)
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('XML с DTD или сущностями не поддерживается.')
        return ET.fromstring(raw)
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            info=z.infolist()
            if len(info)>3000 or sum(i.file_size for i in info)>64000000 or any(i.file_size>20000000 or i.flag_bits&1 for i in info):
                raise ValueError('Книга слишком велика или зашифрована.')
            if len({i.filename for i in info})!=len(info):raise ValueError('В книге повторяются части архива.')
            if any('vbaproject' in i.filename.lower() for i in info):raise ValueError('Книги с макросами не поддерживаются.')
            workbook=xml(z,'xl/workbook.xml')
            relations={i.get('Id'):i for i in xml(z,'xl/_rels/workbook.xml.rels')}
            sheets=[]
            for sheet in workbook.findall(f'{{{NS}}}sheets/{{{NS}}}sheet'):
                rel=relations.get(sheet.get('{'+REL+'}id'))
                if rel is None or rel.get('TargetMode')=='External':continue
                target=rel.get('Target','')
                target=posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/'+target)
                if not target.startswith('xl/') or '..' in target.split('/') or '\\' in target:raise ValueError('Неверный путь листа.')
                sheets.append({'name':sheet.get('name','Лист'),'path':target,'hidden':sheet.get('state','visible')!='visible'})
            if not sheets:raise ValueError('В книге не найдены листы.')
            index=body.get('sheet',0)
            if type(index) is not int or not 0<=index<len(sheets):raise ValueError('Неверный номер листа.')
            shared=[]
            if 'xl/sharedStrings.xml' in z.namelist():
                shared=[''.join(i.itertext()) for i in xml(z,'xl/sharedStrings.xml').findall('{'+NS+'}si')]
            rows=[]; skipped=0
            for item in xml(z,sheets[index]['path']).findall(f'{{{NS}}}sheetData/{{{NS}}}row'):
                if len(rows)>=5001:raise ValueError('Лист содержит более 5000 строк. Разделите прайс.')
                if item.get('hidden')=='1':skipped+=1;continue
                cells={}; formulas=[]
                for cell in item.findall('{'+NS+'}c'):
                    address=cell.get('r','')
                    match=re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]*)',address)
                    if not match:raise ValueError('Некорректный адрес ячейки.')
                    col=0
                    for letter in match[1]:col=col*26+ord(letter)-64
                    if col>100:raise ValueError('Лист содержит более 100 столбцов.')
                    if cell.find('{'+NS+'}f') is not None:formulas.append(col-1)
                    value=cell.findtext('{'+NS+'}v','')
                    if cell.get('t')=='s':
                        if not value.isdigit() or int(value)>=len(shared):raise ValueError('Неверный индекс строки Excel.')
                        value=shared[int(value)]
                    elif cell.get('t')=='inlineStr':
                        inline=cell.find('{'+NS+'}is')
                        value=''.join(inline.itertext()) if inline is not None else ''
                    if len(value)>10000:raise ValueError('Ячейка содержит более 10000 символов.')
                    cells[col-1]=value
                if cells:
                    rows.append({'number':item.get('r',''), 'values':[cells.get(i,'') for i in range(max(cells)+1)],'formulas':formulas})
            return {'sheets':[{'name':s['name'],'hidden':s['hidden']} for s in sheets], 'rows':rows,
                    'skipped_hidden':skipped,'note':'Формулы не вычисляются. Для цен с формулами сохраните отдельный прайс со значениями.'}
    except (zipfile.BadZipFile,KeyError,ET.ParseError,RuntimeError,OverflowError):
        raise ValueError('Не удалось прочитать XLSX. Нужна обычная книга без пароля и макросов.') from None


def map_prices(body):
    rows=body.get('rows',[]); mapping=body.get('mapping',{}); start=body.get('start',1)
    if not isinstance(rows,list) or len(rows)>5001 or not isinstance(mapping,dict) or type(start) is not int or not 0<=start<=len(rows):
        raise ValueError('Неверные параметры импорта.')
    if len(rows)-start>500:raise ValueError('За один раз можно импортировать до 500 позиций.')
    for key in ['name','unit','price']:
        if type(mapping.get(key)) is not int or mapping[key]<0:raise ValueError('Выберите столбцы названия, единицы и цены.')
    imported=[]; errors=[]
    for line in rows[start:]:
        if not isinstance(line,dict) or not isinstance(line.get('values'),list):raise ValueError('Неверная строка таблицы.')
        values=line['values']
        if not any(str(v).strip() for v in values):continue
        def val(key):
            col=mapping.get(key,-1)
            if type(col) is not int or col<0:return ''
            return str(values[col]).strip() if col<len(values) else ''
        if not val('name'):continue
        try:
            if mapping['price'] in line.get('formulas',[]):raise ValueError('Цена является формулой. Сохраните значения в исходном прайсе.')
            price=val('price')
            if re.fullmatch(r'\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d{1,6})?',price):price=re.sub(r'[ \u00a0\u202f]','',price)
            source=val('source') or str(body.get('source',''))
            item={'name':val('name'),'unit':val('unit'),'quantity':'1','price':price,'category':'material',
                  'source':source+'; строка '+str(line.get('number','?')),'price_date':str(body.get('date','')),
                  'supplier':val('supplier') or str(body.get('supplier','')),'article':val('article'),
                  'confirmed':False,'note':val('note'),'source_status':'manual','minimum_charge':'0'}
            imported.append(normalize_entity('catalog',item))
        except ValueError as exc:errors.append({'row':line.get('number','?'),'message':str(exc)})
    return {'rows':imported,'errors':errors}


def logo_bytes(profile):
    data=profile.get('logo','')
    if not data:return None
    raw=decode_file(data.split(',',1)[1],1048576)
    from PIL import Image
    try:
        with Image.open(BytesIO(raw)) as im:
            if im.format not in {'PNG','JPEG'} or im.width*im.height>4000000:raise ValueError('Логотип: нужен PNG/JPEG до 4 млн пикселей.')
            im.verify()
        return raw
    except (OSError,Image.DecompressionBombError):raise ValueError('Не удалось прочитать логотип.') from None


def report_content(project, profile, client):
    normalized=normalize_project(project); result=engine.calculate(normalized)
    profile=normalize_entity('profile',profile or {})
    meta=normalized['project']; totals=result['totals']
    heading='Смета для заказчика' if client else 'Внутренняя калькуляция'
    lines=[profile['company'],profile['details'],profile['contacts'],heading,
           meta['title'] or 'Заказ без названия',result['status'],
           'Заказчик: '+(meta['client'] or 'не указан'),'Дата: '+date.today().isoformat()]
    lines.extend(customer_lines(normalized['extensions']['customer']))
    if client:
        columns=['Изделие / заказ','Количество','Средняя цена, ₽','Сумма, ₽']
        table=[[meta['title'],meta['quantity'],totals['unit_total'],totals['total']]]
        footer=[['Цена заказа, ₽',totals['total']],['НДС, ₽',totals['tax']]]
    else:
        columns=['Позиция','Ед.','Нужно','К оплате','Цена, ₽','Сумма, ₽','Источник / примечание']
        table=[[r['name'],r['unit'],r['quantity'],r['billed_quantity'],r['price'],r['amount'],
                '\n'.join(filter(None,[r['source'],r['price_date'],r['note']]))] for r in result['rows']]
        footer=[[label,totals[key]] for key,label in [('direct','Прямые затраты, ₽'),('overhead','Накладные, ₽'),
                 ('reserve','Резерв, ₽'),('cost','Себестоимость, ₽'),('sale_net','Цена до НДС, ₽'),
                 ('tax','НДС, ₽'),('total','Цена заказа, ₽'),('profit','Расчётная прибыль, ₽')]]
    notes=[meta['notes'],profile['terms']]
    if not result['complete']:notes.append('Предварительный расчёт. Состав, исходные данные и условия требуют подтверждения.')
    if any(r['amount'] is None for r in result['rows']) or not result['rows']:
        notes.append('Часть расходов неизвестна. Полная стоимость не определена; указанные затраты являются частичными.')
    if client:
        notes.append('Средняя цена единицы округлена; расчётной суммой является итог заказа.')
    else:
        notes.extend(result['missing']);notes.extend(result['warnings'])
    return profile,lines,columns,table,footer,[s for s in notes if s]


def export_pdf(project, profile=None, client=False, root=None):
    if type(client) is not bool:raise ValueError('Неверный вид отчёта.')
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, Image
    except ImportError:
        raise ValueError('Для PDF требуется ReportLab. Повторно запустите START_MAC.command для автоматической подготовки.') from None
    profile,lines,columns,table,footer,notes=report_content(project,profile,client)
    root=Path(root or Path(__file__).parent)
    with PDF_LOCK:
        for name,file in [('Estimator','DejaVuSans.ttf'),('EstimatorBold','DejaVuSans-Bold.ttf')]:
            if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(root/'fonts'/file)))
        out=BytesIO(); size=A4 if client else landscape(A4)
        doc=SimpleDocTemplate(out,pagesize=size,rightMargin=32,leftMargin=32,topMargin=30,bottomMargin=32,title=lines[3],author=profile['company'])
        normal=ParagraphStyle('normal',fontName='Estimator',fontSize=9,leading=13,spaceAfter=6,wordWrap='CJK')
        small=ParagraphStyle('small',parent=normal,fontSize=8,leading=11,spaceAfter=2)
        head=ParagraphStyle('head',parent=normal,fontName='EstimatorBold',fontSize=16,leading=21,spaceAfter=10)
        def para(value,style=normal):return Paragraph(escape('Требует уточнения' if value is None or value=='' else str(value)).replace('\n','<br/>'),style)
        story=[]
        logo=logo_bytes(profile)
        if logo:
            from PIL import Image as PILImage
            with PILImage.open(BytesIO(logo)) as im:w,h=im.size
            factor=min(100/w,55/h)
            story.append(Image(BytesIO(logo),width=w*factor,height=h*factor,hAlign='LEFT'))
        for i,line in enumerate(lines):
            if line:story.append(para(line,head if i==3 else normal))
        story.append(Spacer(1,8))
        widths=([230,65,100,size[0]-64-395] if client else [145,44,66,66,76,79,size[0]-64-476])
        cells=[[para(c,small) for c in columns]]+[[para('—' if v is None or v=='' else v,small) for v in row] for row in table]
        t=LongTable(cells,colWidths=widths,repeatRows=1,splitInRow=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E8F0F2')),
             ('GRID',(0,0),(-1,-1),.4,colors.HexColor('#C5D2D8')),('VALIGN',(0,0),(-1,-1),'TOP'),
             ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
             ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        story.extend([t,Spacer(1,14)])
        if any(v is None or v=='' for row in table for v in row):story.append(para('— означает, что значение не указано или требует уточнения.',small))
        for label,value in footer:story.append(para(label+': '+('Требует уточнения' if value is None else str(value).replace('.',','))))
        if notes:story.extend([Spacer(1,10),para('Комплектация и условия',head)])
        for note in notes:story.append(para(note))
        def page(canvas,doc):
            canvas.setFont('Estimator',8);canvas.setFillColor(colors.HexColor('#657B89'))
            canvas.drawString(32,17,'Сметчик рекламы и полиграфии');canvas.drawRightString(size[0]-32,17,str(doc.page))
        doc.build(story,onFirstPage=page,onLaterPages=page)
        return out.getvalue()


def export_xlsx(project, profile=None, client=False):
    """Portable OOXML snapshot: numeric totals and explicit text cells, no formulas."""
    if type(client) is not bool:raise ValueError('Неверный вид отчёта.')
    profile,lines,columns,table,footer,notes=report_content(project,profile,client)
    output=BytesIO()
    logo=logo_bytes(profile)
    def col(n):
        s=''
        while n:n,r=divmod(n-1,26);s=chr(65+r)+s
        return s
    rows=[['']] if logo else []
    for line in lines:
        if line:rows.append([line])
    rows.extend([[],columns]);header_row=len(rows)
    rows.extend(table);table_end=len(rows);rows.append([]);footer_start=len(rows)+1;rows.extend(footer);footer_end=len(rows)
    rows.extend([[],['Комплектация и условия']])
    for note in notes:
        # Keep long notes readable without exceeding Excel's row height limit.
        rows.extend([[note[i:i+350]] for i in range(0,len(note),350)])
    rows.extend([[],['Снимок расчёта. Изменяйте и пересчитывайте заказ в программе.']])
    width=len(columns)
    sheet=[f'<worksheet xmlns="{NS}" xmlns:r="{REL}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="{header_row}" topLeftCell="A{header_row+1}" state="frozen"/></sheetView></sheetViews><cols>']
    for index in range(width):
        size=48 if index==0 else 56 if index==width-1 and not client else 16
        sheet.append(f'<col min="{index+1}" max="{index+1}" width="{size}" customWidth="1"/>')
    sheet.append('</cols><sheetData>')
    merges=[]
    for i,values in enumerate(rows,1):
        longest=max((len(str(v or '')) for v in values),default=0)
        height=min(400,max(24,14*((longest//(90 if len(values)==1 else 42))+1)))
        if logo and i==1:height=65
        sheet.append(f'<row r="{i}" ht="{height}" customHeight="1">')
        if len(values)==1:merges.append(f'A{i}:{col(width)}{i}')
        for j,value in enumerate(values,1):
            style=1 if i==header_row else 0
            if value is None:value='Требует уточнения'
            value=str(value)
            number_column=(header_row<i<=table_end and j in ({2,3,4} if client else {3,4,5,6})) or (footer_start<=i<=footer_end and j==2)
            numeric=(number_column and re.fullmatch(r'-?\d+(?:\.\d+)?',value)
                     and len(value.replace('.','').lstrip('-').lstrip('0'))<=15)
            if numeric:
                sheet.append(f'<c r="{col(j)}{i}" s="2" t="n"><v>{value}</v></c>')
            else:
                sheet.append(f'<c r="{col(j)}{i}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>')
        sheet.append('</row>')
    sheet.append('</sheetData>')
    if merges:sheet.append('<mergeCells count="'+str(len(merges))+'">'+''.join('<mergeCell ref="'+m+'"/>' for m in merges)+'</mergeCells>')
    sheet.append('<pageMargins left="0.3" right="0.3" top="0.4" bottom="0.4" header="0.2" footer="0.2"/><pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>')
    styles=f'''<styleSheet xmlns="{NS}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE8F0F2"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="3"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="0" fillId="0" borderId="0" numFmtId="4" xfId="0" applyNumberFormat="1"><alignment vertical="top"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    extension='png' if logo and logo.startswith(b'\x89PNG') else 'jpeg'
    if logo:sheet[-1]=sheet[-1].replace('</worksheet>','<drawing r:id="rIdLogo"/></worksheet>')
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'+('<Default Extension="'+extension+'" ContentType="image/'+extension+'"/><Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>' if logo else '')+'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        z.writestr('_rels/.rels',f'<Relationships xmlns="{PKGREL}"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets><sheet name="Смета" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels',f'<Relationships xmlns="{PKGREL}"><Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{REL}/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml',styles);z.writestr('xl/worksheets/sheet1.xml',''.join(sheet))
        if logo:
            from PIL import Image
            with Image.open(BytesIO(logo)) as im:w,h=im.size
            ratio=min(100/w,55/h);cx=int(w*ratio*12700);cy=int(h*ratio*12700)
            z.writestr('xl/media/logo.'+extension,logo)
            z.writestr('xl/worksheets/_rels/sheet1.xml.rels',f'<Relationships xmlns="{PKGREL}"><Relationship Id="rIdLogo" Type="{REL}/drawing" Target="../drawings/drawing1.xml"/></Relationships>')
            z.writestr('xl/drawings/_rels/drawing1.xml.rels',f'<Relationships xmlns="{PKGREL}"><Relationship Id="rIdImage" Type="{REL}/image" Target="../media/logo.{extension}"/></Relationships>')
            z.writestr('xl/drawings/drawing1.xml',f'<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="{REL}"><xdr:oneCellAnchor><xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>0</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:ext cx="{cx}" cy="{cy}"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="1" name="Company logo"/><xdr:cNvPicPr/></xdr:nvPicPr><xdr:blipFill><a:blip r:embed="rIdImage"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill><xdr:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic><xdr:clientData/></xdr:oneCellAnchor></xdr:wsDr>')

    return output.getvalue()
