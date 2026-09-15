"""Consistent local database copies plus immutable original source documents."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile


def create(store,stock=None,monitor=None):
    output=BytesIO();manifest={};total=0
    with store.lock,tempfile.TemporaryDirectory(prefix='estimator-backup-') as directory:
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ('workspace.sqlite','stock.sqlite','price-monitor.sqlite','supply-hub.sqlite'):
                source=store.directory/name
                if not source.is_file():continue
                target=Path(directory)/name
                original=sqlite3.connect(source);copy=sqlite3.connect(target)
                try:original.backup(copy)
                finally:copy.close();original.close()
                if target.stat().st_size+total>50*1024*1024:raise ValueError('Базы превышают 50 МБ: скопируйте папку данных при закрытом приложении.')
                data=target.read_bytes();total+=len(data);archive.writestr(name,data);manifest[name]=hashlib.sha256(data).hexdigest()
            for subdir in ('price-documents','fns-documents'):
                folder=store.directory/subdir
                if not folder.exists():continue
                for path in sorted(folder.iterdir()):
                    if path.is_symlink() or not path.is_file():continue
                    if path.suffix not in ('.bin','.pdf','.json'):continue
                    if path.stat().st_size>8*1024*1024:raise ValueError('Исходный документ слишком велик для резервной копии.')
                    data=path.read_bytes();total+=len(data)
                    if total>50*1024*1024:raise ValueError('Данные превышают 50 МБ. Закройте приложение и скопируйте папку данных целиком.')
                    name=subdir+'/'+path.name;archive.writestr(name,data);manifest[name]=hashlib.sha256(data).hexdigest()
            archive.writestr('manifest.json',json.dumps({'format':1,'files':manifest},ensure_ascii=False,indent=2))
            archive.writestr('RESTORE.txt','Восстановление: закройте сметчик. Сохраните прежнюю папку данных отдельно. Распакуйте архив в НОВУЮ пустую папку. Укажите её через ESTIMATOR_DATA_DIR перед запуском программы. Ключи DaData/Tavily хранятся отдельно в системном хранилище и в архив не входят. Не смешивайте базы от разных копий. Подробности — docs/OPTIONAL_FEATURES.md.')
    return output.getvalue()
