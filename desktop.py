"""Native window entry point. Calculation API remains restricted to loopback."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import webbrowser

# Windowed Windows executables have no standard streams; dependencies may flush them.
if sys.stdout is None:sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:sys.stderr = open(os.devnull, 'w', encoding='utf-8')

import server


def self_test():
    import engine
    required=('web/index.html','web/app.js','web/workspace.js','web/price_updates.js','web/monitor.js','data/prices.json','examples/demo.json','fonts/DejaVuSans.ttf')
    for name in required:
        if not (server.RESOURCE_ROOT/name).is_file():raise ValueError('Отсутствует ресурс '+name)
    example=json.loads((server.RESOURCE_ROOT/'examples/demo.json').read_text(encoding='utf-8'))
    result=engine.calculate(example)
    if not result.get('rows'):raise ValueError('Учебный расчёт пуст.')
    import base64
    import advanced_documents
    import file_formats
    import mixed_layout
    import price_pdf
    import vector_geometry
    assert advanced_documents.export_docx(example).startswith(b'PK')
    assert advanced_documents.export_editable_xlsx(example).startswith(b'PK')
    assert mixed_layout.layout({'sheet_width_mm':'100','sheet_height_mm':'100','parts':[{'name':'T','width_mm':'20','height_mm':'20','quantity':2}]})['sheets']==1
    svg=server.RESOURCE_ROOT/'examples/cutting_demo.svg'
    assert vector_geometry.measure({'format':'svg','content':base64.b64encode(svg.read_bytes()).decode()})['length_m']=='1.000000'
    pdf=file_formats.export_pdf(example,root=server.RESOURCE_ROOT)
    assert price_pdf.read_pdf(pdf,{'sheet':0,'pdf_mode':'text','ocr_language':'eng'})['rows']
    return {'version':server.VERSION,'resource_check':True,'calculation_check':True,'word_excel_check':True,'pdf_vector_workers_check':True,'mixed_layout_check':True}


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0]=='--vector-worker':
        from vector_geometry import worker
        return worker(argv[1:])
    if argv and argv[0]=='--price-pdf-worker':
        from price_pdf import worker
        return worker(argv[1:])
    parser=argparse.ArgumentParser(description='Сметчик рекламы: отдельное окно')
    parser.add_argument('--browser',action='store_true',help='Открыть в обычном браузере')
    parser.add_argument('--self-test',action='store_true',help='Проверить ресурсы и расчёт без окна')
    parser.add_argument('--self-test-report',type=Path,help='Записать результат самопроверки в JSON; без окна')
    args=parser.parse_args(argv)
    if args.self_test or args.self_test_report:
        try:
            report={**self_test(),'ok':True};code=0
        except Exception as error:
            report={'version':server.VERSION,'ok':False,'error':type(error).__name__+': '+str(error)};code=1
        output=json.dumps(report,ensure_ascii=False,indent=2)
        if args.self_test_report:args.self_test_report.write_text(output+'\n',encoding='utf-8')
        print(output)
        return code
    if args.browser:return server.main([])
    try:import webview
    except ImportError:
        print('Отдельное окно требует SETUP_FEATURES. Открываем программу в браузере.')
        return server.main([])
    app=server.EstimatorServer();app.start_background()
    thread=threading.Thread(target=app.serve_forever,daemon=True);thread.start()
    try:
        webview.settings['ALLOW_DOWNLOADS']=True
        webview.settings['ALLOW_FILE_URLS']=False
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=True
        webview.create_window('Сметчик рекламы '+server.VERSION,app.origin,width=1360,height=900,min_size=(900,650),text_select=True,confirm_close=True)
        webview.start(gui='edgechromium' if sys.platform=='win32' else None,private_mode=True,debug=False,localization={'global.quitConfirmation':'Сохраните изменённый заказ перед выходом. Закрыть программу?'})
    except Exception:
        # Keep the same local session if the platform's native renderer is absent.
        print('Системный WebView недоступен. Интерфейс: '+app.origin)
        webbrowser.open(app.origin)
        try:
            while thread.is_alive():thread.join(timeout=0.5)
        except KeyboardInterrupt:pass
    finally:
        app.shutdown();app.server_close();thread.join(timeout=2)
    return 0

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
