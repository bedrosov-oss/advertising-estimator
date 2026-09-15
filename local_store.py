"""Local, versioned workspace. No network access or credential persistence."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import uuid
import urllib.parse

import engine
import fns_registry

KINDS = {'catalog', 'order', 'template', 'variant', 'offer', 'profile', 'draft', 'price_source'}


def data_directory():
    override = os.environ.get('ESTIMATOR_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/AdvertisingEstimator'
    if sys.platform=='win32':
        return Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'AdvertisingEstimator'
    return Path.home() / '.local/share/AdvertisingEstimator'


def text(value, label, limit=1000):
    return engine._text(value, label, limit)


def normalize_project(value):
    normalized = engine.validate_project(value)
    raw = value.get('extensions', {})
    if not isinstance(raw, dict):
        raise ValueError('Дополнительные данные заказа должны быть объектом.')
    actuals = raw.get('actuals', {})
    if not isinstance(actuals, dict) or len(actuals) > 500:
        raise ValueError('Некорректный список фактических расходов.')
    ids = {r['id'] for r in normalized['rows']}
    clean = {}
    for key, item in actuals.items():
        if key not in ids:
            continue
        if not isinstance(item, dict):
            raise ValueError('Фактический расход должен быть объектом.')
        clean[key] = {'amount': engine._numeric_text(item.get('amount', ''), 'Фактический расход', optional=True),
                      'note': text(item.get('note', ''), 'Примечание расхода', 2000)}
    extra = raw.get('extra_actual', '')
    normalized['extensions'] = {'actuals': clean,
        'extra_actual': engine._numeric_text(extra, 'Расходы вне сметы', optional=True),
        'extra_note': text(raw.get('extra_note', ''), 'Дополнительные расходы', 2000),
        'customer_query': text(raw.get('customer_query',''), 'ИНН / ОГРН заказчика', 40),
        'customer': fns_registry.normalize_customer(raw.get('customer',{}))}
    customer=normalized['extensions']['customer']
    query=normalized['extensions']['customer_query']
    if customer and query and re.sub(r'\s+', '',query)!=customer['query']:
        raise ValueError('Карточка заказчика относится к другому ИНН / ОГРН. Обновите реквизиты.')
    return normalized


def normalize_entity(kind, data):
    if not isinstance(data, dict):
        raise ValueError('Нужен объект данных.')
    if kind in {'order', 'template', 'variant', 'draft'}:
        return normalize_project(data)
    if kind == 'price_source':
        result={key:text(data.get(key,''),key,limit) for key,limit in [
            ('supplier',300),('url',2000),('unit',30),('source_date',10),('notes',1000)]}
        if not result['supplier']:raise ValueError('Укажите поставщика прайса.')
        result['format']=data.get('format','csv')
        if result['format'] not in ('csv','xlsx','pdf','product','zenon','forda'):raise ValueError('Выберите поддерживаемый формат прайса.')
        result['pdf_mode']=data.get('pdf_mode','text')
        result['ocr_language']=data.get('ocr_language','rus+eng')
        if result['pdf_mode'] not in ('text','ocr') or result['ocr_language'] not in ('rus+eng','eng','rus'):
            raise ValueError('Неверный режим PDF/OCR.')
        result['currency']=data.get('currency','unknown')
        if result['currency'] not in ('RUB','unknown'):raise ValueError('Поддерживаются только рублёвые цены.')
        if result['url']:
            try:
                parsed=urllib.parse.urlsplit(result['url'])
                valid=parsed.scheme in ('http','https') and parsed.hostname and not parsed.username and not parsed.password
                valid=valid and not any(ord(c)<=32 for c in result['url']) and '\\' not in result['url']
            except ValueError:valid=False
            if not valid:raise ValueError('Нужна публичная ссылка HTTP/HTTPS без логина и пароля.')
        if result['source_date']:
            from datetime import date
            date.fromisoformat(result['source_date'])
        for key,default,maximum in [('sheet',0,999),('start',1,5000)]:
            number=data.get(key,default)
            if type(number) is not int or not 0<=number<=maximum:raise ValueError('Неверный номер листа или начальной строки.')
            result[key]=number
        mapping=data.get('mapping',{})
        if not isinstance(mapping,dict):raise ValueError('Выберите столбцы прайса.')
        result['mapping']={}
        for key in ('name','article','unit','price','currency'):
            number=mapping.get(key,-1)
            if type(number) is not int or not -1<=number<100:raise ValueError('Неверный столбец прайса.')
            result['mapping'][key]=number
        return result
    if kind == 'catalog':
        row = engine.validate_project({'rows': [{**data, 'id': 'catalog-row'}]})['rows'][0]
        if not row['name'] or not row['unit']:
            raise ValueError('Для справочника нужны название и единица.')
        row['supplier'] = text(data.get('supplier', ''), 'Поставщик', 300)
        row['article'] = text(data.get('article', ''), 'Артикул', 100)
        if data.get('price_observation'):
            observation=data['price_observation']
            if not isinstance(observation,dict):raise ValueError('Неверные сведения о проверке цены.')
            row['price_observation']={key:text(observation.get(key,''),key,limit) for key,limit in [
                ('source_date',10),('row',50),('sheet',100),('excerpt',2000),('unit',30),('article',100),('notes',1000)]}
            if row['price_observation']['source_date']:
                from datetime import date
                date.fromisoformat(row['price_observation']['source_date'])
            row['price_observation']['document']=normalize_price_document(observation.get('document'))
            row['price_observation']['price']=engine._numeric_text(observation.get('price'),'Цена наблюдения')
            if observation.get('currency')!='RUB':raise ValueError('В наблюдении нужна валюта RUB.')
            row['price_observation']['currency']='RUB'
        # A catalog's quantity is a reference value, not a new order's requirement.
        return row
    if kind == 'profile':
        logo = text(data.get('logo', ''), 'Логотип', 1400000)
        if logo and not re.fullmatch(r'data:image/(png|jpeg);base64,[A-Za-z0-9+/=]+', logo):
            raise ValueError('Логотип должен быть PNG или JPEG.')
        return {**{k: text(data.get(k, ''), k, 4000) for k in ['company', 'details', 'contacts', 'terms']}, 'logo': logo, 'autosave': engine._boolean(data.get('autosave',False),'Автосохранение')}
    if kind == 'offer':
        out = {k: text(data.get(k, ''), k, 2000) for k in ['supplier', 'item', 'unit', 'source', 'date', 'terms']}
        for k in ['price', 'delivery', 'increment', 'minimum']:
            out[k] = engine._numeric_text(data.get(k, ''), k, optional=True, positive=k=='increment')
        out['comparable'] = engine._boolean(data.get('comparable', False), 'Сопоставимость предложения')
        if out['date']:
            from datetime import date
            try:
                date.fromisoformat(out['date'])
            except ValueError:
                raise ValueError('Неверная дата предложения.') from None
        return out
    raise ValueError('Неизвестный вид записи.')


def normalize_price_document(value):
    if not isinstance(value,dict):raise ValueError('Отсутствует описание исходного прайса.')
    out={key:text(value.get(key,''),key,limit) for key,limit in [
        ('sha256',64),('filename',150),('retrieved_at',40),('source_url',2000),('original_name',300)]}
    if not re.fullmatch(r'[a-f0-9]{64}',out['sha256']) or not re.fullmatch(r'[A-Za-z0-9_-]+\.(csv|xlsx|txt|pdf)',out['filename']):
        raise ValueError('Некорректное имя или контрольная сумма прайса.')
    if type(value.get('size')) is not int or not 1<=value['size']<=8*1024*1024:raise ValueError('Неверный размер исходного прайса.')
    out['size']=value['size']
    return out


class LocalStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else data_directory()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'workspace.sqlite'
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS records (
                kind TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
                data TEXT NOT NULL, revision INTEGER NOT NULL, updated TEXT NOT NULL,
                PRIMARY KEY(kind,id));
              CREATE TABLE IF NOT EXISTS history (
                id TEXT NOT NULL, revision INTEGER NOT NULL, name TEXT NOT NULL,
                data TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(id,revision));
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    @staticmethod
    def kind(value):
        if not isinstance(value,str) or value not in KINDS:
            raise ValueError('Неизвестный раздел локальной базы.')
        return value

    def list(self, kind, query=''):
        kind = self.kind(kind)
        query = text(query, 'Поиск', 300).casefold()
        result = []
        used=0
        with self.connection() as db:
            for record in db.execute('SELECT * FROM records WHERE kind=? ORDER BY updated DESC', (kind,)):
                value = dict(record)
                if query and query not in (value['name']+' '+value['data']).casefold():continue
                used+=len(value['data'].encode())
                if result and (len(result)>=1000 or used>8000000):break
                value['data'] = json.loads(value['data'])
                result.append(value)
        return result

    def get(self, kind, item_id, revision=None):
        kind = self.kind(kind)
        with self.connection() as db:
            if revision is not None:
                if kind != 'order' or type(revision) is not int:
                    raise ValueError('Версии доступны для заказов.')
                record = db.execute('SELECT * FROM history WHERE id=? AND revision=?', (item_id, revision)).fetchone()
            else:
                record = db.execute('SELECT * FROM records WHERE kind=? AND id=?', (kind, item_id)).fetchone()
        if record is None:
            raise ValueError('Запись не найдена.')
        out = dict(record)
        out['kind'] = kind
        out['data'] = json.loads(out['data'])
        return out

    def save(self, kind, name, data, item_id=None, expected_revision=None):
        kind = self.kind(kind)
        name = text(name, 'Название записи', 300)
        if not name:
            raise ValueError('Укажите название записи.')
        payload = json.dumps(normalize_entity(kind, data), ensure_ascii=False, allow_nan=False)
        if len(payload.encode()) > 2500000:
            raise ValueError('Запись превышает 2,5 МБ.')
        if item_id is not None and (not isinstance(item_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', item_id)):
            raise ValueError('Неверный идентификатор.')
        item_id = item_id or uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat(timespec='microseconds')
        with self.connection() as db:
            old = db.execute('SELECT revision FROM records WHERE kind=? AND id=?', (kind, item_id)).fetchone()
            if old is not None and expected_revision != old['revision']:
                raise ValueError('Запись изменена в другом окне. Обновите список перед сохранением.')
            if old is None and expected_revision not in (None, 0):
                raise ValueError('Исходная версия записи не найдена.')
            revision = old['revision'] + 1 if old else 1
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?)', (kind, item_id, name, payload, revision, now))
            if kind == 'order':
                db.execute('INSERT INTO history VALUES(?,?,?,?,?)', (item_id, revision, name, payload, now))
        return {'kind':kind,'id':item_id,'name':name,'data':json.loads(payload),'revision':revision,'updated':now}

    def versions(self, item_id):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT id,revision,name,updated FROM history WHERE id=? ORDER BY revision DESC', (item_id,))]

    def delete(self, kind, item_id, expected_revision):
        kind = self.kind(kind)
        with self.connection() as db:
            old = db.execute('SELECT revision FROM records WHERE kind=? AND id=?', (kind, item_id)).fetchone()
            if old is None or old['revision'] != expected_revision:
                raise ValueError('Запись изменилась. Обновите список.')
            db.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, item_id))
            if kind == 'order':
                db.execute('DELETE FROM history WHERE id=?', (item_id,))
        return {'ok': True}

    def backup(self):
        with self.connection() as db:
            records = [dict(r) for r in db.execute('SELECT * FROM records')]
            history = [dict(r) for r in db.execute('SELECT * FROM history')]
        for record in records + history:
            record['data'] = json.loads(record['data'])
        return {'backup_version': 1, 'created': datetime.now(timezone.utc).isoformat(), 'records': records, 'history': history}

    def import_preview(self, rows):
        if not isinstance(rows,list) or len(rows)>500:raise ValueError('Допустимо до 500 позиций.')
        # Import matching must cover the entire catalog, not a limited UI page.
        with self.connection() as db:
            existing=[{**dict(r),'data':json.loads(r['data'])} for r in db.execute("SELECT id,revision,data FROM records WHERE kind='catalog'")]
        def identity(row):
            return (row.get('article') or row['name']).casefold(),row.get('supplier','').casefold(),row['unit'].casefold()
        index={}
        for item in existing:index.setdefault(identity(item['data']),[]).append(item)
        changes=[]; seen=set()
        for raw in rows:
            row=normalize_entity('catalog',raw);key=identity(row)
            if key in seen:raise ValueError('В импортируемом прайсе повторяется позиция с одинаковым поставщиком и единицей.')
            seen.add(key);matches=index.get(key,[])
            if len(matches)>1:raise ValueError('В справочнике несколько совпадающих позиций. Уточните артикулы перед импортом.')
            old=matches[0] if matches else None
            changes.append({'id':old['id'] if old else None,'expected_revision':old['revision'] if old else None,
                            'name':row['name'],'data':row,'old_price':old['data']['price'] if old else None})
        return changes

    def import_apply(self, changes):
        if not isinstance(changes,list) or len(changes)>500:raise ValueError('Допустимо до 500 изменений.')
        ready=[];seen=set()
        for item in changes:
            row=normalize_entity('catalog',item.get('data'))
            item_id=item.get('id') or uuid.uuid4().hex
            if not isinstance(item_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',item_id) or item_id in seen:
                raise ValueError('Неверные или повторяющиеся идентификаторы импорта.')
            seen.add(item_id);ready.append((item_id,item.get('expected_revision'),row))
        with self.connection() as db:
            now=datetime.now(timezone.utc).isoformat()
            index={}
            def identity(row):
                return (row.get('article') or row['name']).casefold(),row.get('supplier','').casefold(),row['unit'].casefold()
            for item in db.execute("SELECT id,data FROM records WHERE kind='catalog'"):
                index.setdefault(identity(json.loads(item['data'])),[]).append(item['id'])
            for item_id,expected,row in ready:
                key=identity(row);matches=index.get(key,[])
                if (expected is None and matches) or (expected is not None and matches!=[item_id]):
                    raise ValueError('Справочник изменился. Обновите предварительный просмотр импорта.')
                old=db.execute('SELECT revision FROM records WHERE kind=? AND id=?',('catalog',item_id)).fetchone()
                if (old and old['revision']!=expected) or (not old and expected is not None):
                    raise ValueError('Справочник изменился. Обновите предварительный просмотр импорта.')
                db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?)',('catalog',item_id,row['name'],json.dumps(row,ensure_ascii=False),old['revision']+1 if old else 1,now))
                index[key]=[item_id]
        return {'saved':len(ready)}

    def restore(self, backup):
        if not isinstance(backup, dict) or backup.get('backup_version') != 1:
            raise ValueError('Неверный формат резервной копии.')
        records = backup.get('records', [])
        history = backup.get('history', [])
        if not isinstance(records, list) or not isinstance(history, list) or len(records)+len(history)>10000:
            raise ValueError('Резервная копия слишком велика.')
        # Import as copies with fresh IDs, preserving available order histories.
        prepared = []; order_ids={}; identifiers=set()
        for item in records:
            if not isinstance(item, dict):
                raise ValueError('Повреждена запись резервной копии.')
            kind = self.kind(item.get('kind'))
            old_id=item.get('id')
            if not isinstance(old_id,str) or (kind,old_id) in identifiers:raise ValueError('Повторяющиеся идентификаторы резервной копии.')
            identifiers.add((kind,old_id))
            revision=item.get('revision',1)
            if type(revision) is not int or not 1<=revision<=1000000:raise ValueError('Неверная версия резервной копии.')
            item_id=uuid.uuid4().hex
            name=text(item.get('name'), 'Название', 300)
            clean=normalize_entity(kind,item.get('data'))
            updated=text(item.get('updated',''), 'Дата',100)
            prepared.append((kind,item_id,name,clean,revision,updated))
            if kind=='order':order_ids[old_id]=(item_id,revision,name,clean,updated)
        prepared_history=[]; version_keys=set()
        for item in history:
            if not isinstance(item,dict) or item.get('id') not in order_ids:raise ValueError('История ссылается на отсутствующий заказ.')
            item_id,current,name,current_data,updated=order_ids[item['id']]
            revision=item.get('revision')
            if type(revision) is not int or not 1<=revision<=current or (item_id,revision) in version_keys:
                raise ValueError('Неверная или повторяющаяся версия заказа.')
            version_keys.add((item_id,revision))
            clean=normalize_entity('order',item.get('data'))
            if revision==current and clean!=current_data:raise ValueError('Текущая версия заказа противоречит истории.')
            prepared_history.append((item_id,revision,text(item.get('name'), 'Название версии',300),clean,text(item.get('updated',''),'Дата',100)))
        for item_id,revision,name,clean,updated in order_ids.values():
            if (item_id,revision) not in version_keys:prepared_history.append((item_id,revision,name,clean,updated))
        with self.connection() as db:
            now = datetime.now(timezone.utc).isoformat()
            for kind,item_id,name,data,revision,updated in prepared:
                payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
                db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)', (kind,item_id,name,payload,revision,updated or now))
            for item_id,revision,name,data,updated in prepared_history:
                db.execute('INSERT INTO history VALUES(?,?,?,?,?)',(item_id,revision,name,json.dumps(data,ensure_ascii=False),updated or now))
        return {'imported': len(prepared), 'note': 'Записи добавлены как копии вместе с доступными версиями заказов. Прежние записи сохранены.'}
