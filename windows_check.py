"""Local diagnostic using only synthetic data; does not prove graphical compatibility."""
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import struct
import sys
import tempfile
import threading
import urllib.request
import server
from local_store import LocalStore, data_directory

ROOT = Path(__file__).resolve().parent

def check():
    report = {'version':server.VERSION, 'checked_at':datetime.now(timezone.utc).isoformat(),
              'platform':sys.platform, 'python':platform.python_version(), 'bits':struct.calcsize('P')*8,
              'gui_checked':False, 'external_services_checked':False, 'checks':[]}
    def step(name, action):
        try:action();report['checks'].append({'name':name,'ok':True})
        except Exception as error:report['checks'].append({'name':name,'ok':False,'error':type(error).__name__+': '+str(error)})
    def release():
        sys.path.insert(0,str(ROOT/'scripts'))
        from check_release import check_release
        check_release(ROOT)
    def documents():
        import desktop
        desktop.self_test()
    def storage_http():
        # Exercise Cyrillic/spaces in the actual user's data location, with throwaway records.
        with tempfile.TemporaryDirectory(prefix='Проверка сметчика ',dir=data_directory()) as folder:
            store=LocalStore(folder)
            saved=store.save('order','Проверка Windows',{'rows':[]})
            if store.get('order',saved['id'])['name']!='Проверка Windows':raise ValueError('Заказ не прочитан.')
            app=server.EstimatorServer(('127.0.0.1',0),data_dir=folder)
            thread=threading.Thread(target=app.serve_forever,daemon=True);thread.start()
            try:
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(app.origin+'/guide.json',timeout=10) as response:
                    guide=json.load(response)
                if guide['version']!=server.VERSION:raise ValueError('Версия интерфейса не совпала.')
            finally:app.shutdown();app.server_close();thread.join(timeout=2)
    step('Инструкция и файлы выпуска',release)
    step('Расчёт, Word, Excel, PDF, SVG и раскрой',documents)
    step('Запись заказа, кириллица в пути и локальный HTTP',storage_http)
    report['ok']=all(item['ok'] for item in report['checks'])
    return report

def main():
    folder=data_directory();folder.mkdir(parents=True,exist_ok=True)
    report=check()
    path=folder/('windows-check-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json')
    with path.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    for item in report['checks']:print(('OK: ' if item['ok'] else 'ОШИБКА: ')+item['name']+(' — '+item['error'] if not item['ok'] else ''))
    print('Отчёт: '+str(path))
    print('Окно, загрузку файлов, OCR и реальные сервисы проверьте вручную по docs/WINDOWS_CHECK.md.')
    return 0 if report['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
