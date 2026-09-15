"""Durable daily supplier observations and exact, availability-aware selection."""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import csv
import hashlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import engine
import shipping_quotes
from supplier_fetch import fetch_public, _url
from supplier_prices import numeric_price

DAY=86400

def text(value,label,limit=300):
    result=engine._text(value,label,limit).strip()
    if not result:raise ValueError('Заполните: '+label)
    return result

def feed(raw,format):
    if not raw or len(raw)>8*1024*1024:raise ValueError('Прайс должен быть до 8 МБ.')
    try:
        if format=='json':items=json.loads(raw)
        else:items=list(csv.DictReader(StringIO(raw.decode('utf-8-sig')),delimiter=';'))
    except (ValueError,UnicodeError):raise ValueError('Проверьте JSON или CSV UTF-8 с разделителем ;.') from None
    if not isinstance(items,list) or len(items)>500:raise ValueError('Нужен список не более 500 товаров.')
    result=[];seen=set()
    for item in items:
        if not isinstance(item,dict):raise ValueError('Позиция прайса должна быть объектом.')
        row={k:text(item.get(k,''),k) for k in ('name','article','material_key','unit','tax_basis')}
        if row['article'].casefold() in seen:raise ValueError('Повторяется артикул: '+row['article'])
        seen.add(row['article'].casefold())
        if item.get('currency')!='RUB':raise ValueError('Для автоподбора нужна явная валюта RUB.')
        if row['tax_basis'] not in ('vat_included','no_vat'):raise ValueError('Укажите tax_basis: vat_included или no_vat.')
        row['price']=numeric_price(item.get('price'))
        for k in ('available_quantity','minimum_quantity','package_step'):
            row[k]=numeric_price(item.get(k))
        if Decimal(row['package_step'])<=0:raise ValueError('Кратность упаковки должна быть больше нуля.')
        available=item.get('available')
        if format=='csv' and available in ('true','false'):available=available=='true'
        if type(available) is not bool:raise ValueError('Наличие должно быть явно true или false.')
        row.update(available=available,currency='RUB')
        result.append(row)
    return result

class SupplyHub:
    def __init__(self,store,*,fetcher=fetch_public,shipping=shipping_quotes.calculate,clock=time.time):
        self.store=store;self.path=store.directory/'supply-hub.sqlite';self.clock=clock;self.fetcher=fetcher;self.shipping=shipping
        self.lock=threading.RLock();self.active=set();self.scheduler=None
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,kind TEXT NOT NULL,name TEXT NOT NULL,config TEXT NOT NULL,enabled INTEGER NOT NULL,next_due REAL NOT NULL,last_status TEXT NOT NULL,last_success REAL,snapshot TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS history(id TEXT PRIMARY KEY,source_id TEXT NOT NULL,checked REAL NOT NULL,status TEXT NOT NULL,summary TEXT NOT NULL,digest TEXT NOT NULL);''')
            db.execute("UPDATE sources SET last_status='error',next_due=? WHERE last_status='checking'",(self.clock(),))
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def save(self,body):
        kind=body.get('kind');name=text(body.get('name',''),'Название')
        if kind not in ('supplier','delivery'):raise ValueError('Выберите поставщика или доставку.')
        enabled=body.get('enabled',True)
        if type(enabled) is not bool:raise ValueError('Неверный режим расписания.')
        if kind=='supplier':
            raw=body.get('config',{});format=raw.get('format','json');url=raw.get('url','')
            if format not in ('json','csv'):raise ValueError('Автопрайс: JSON или CSV.')
            if url:_url(url)
            if enabled and not url:raise ValueError('Для ежедневной проверки нужна прямая публичная ссылка.')
            config={'url':url,'format':format}
        else:config=shipping_quotes.normalize(body.get('config'))
        identifier=body.get('id')
        with self.lock,self.connect() as db:
            if identifier:
                old=db.execute('SELECT id,kind FROM sources WHERE id=?',(identifier,)).fetchone()
                if not old or old['kind']!=kind:raise ValueError('Настройка не найдена.')
                if identifier in self.active:raise ValueError('Идёт проверка. Дождитесь завершения.')
                db.execute('UPDATE sources SET name=?,config=?,enabled=?,next_due=?,last_status=?,last_success=NULL,snapshot=? WHERE id=?',
                           (name,json.dumps(config),int(enabled),self.clock(),'new','[]',identifier))
            else:
                if db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]>=50:raise ValueError('Поддерживается до 50 источников.')
                identifier=uuid.uuid4().hex
                db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(identifier,kind,name,json.dumps(config),int(enabled),self.clock(),'new',None,'[]'))
        return {'id':identifier}
    def remove(self,identifier):
        with self.lock,self.connect() as db:
            if identifier in self.active:raise ValueError('Идёт проверка. Дождитесь завершения.')
            db.execute('DELETE FROM sources WHERE id=?',(identifier,))
        return {'deleted':True}
    def status(self):
        with self.connect() as db:
            rows=[dict(row) for row in db.execute('SELECT * FROM sources ORDER BY kind,name')]
            history=[dict(row) for row in db.execute('SELECT * FROM history ORDER BY checked DESC LIMIT 100')]
        for row in rows:
            row['config']=json.loads(row['config']);row['snapshot']=json.loads(row['snapshot']);row['enabled']=bool(row['enabled'])
            row['fresh']=row['last_status']=='ok' and row['last_success'] is not None and 0<=self.clock()-row['last_success']<=DAY
        return {'sources':rows,'history':history,'running':self.scheduler is not None,'interval_hours':24}
    def refresh(self,identifier,*,raw=None,scheduled=False):
        with self.lock,self.connect() as db:
            row=db.execute('SELECT * FROM sources WHERE id=?',(identifier,)).fetchone()
            if not row:raise ValueError('Источник не найден.')
            if identifier in self.active:raise ValueError('Проверка уже выполняется.')
            if scheduled and (not row['enabled'] or row['next_due']>self.clock()):return {'skipped':True}
            self.active.add(identifier)
            db.execute("UPDATE sources SET next_due=?,last_status='checking' WHERE id=?",(self.clock()+DAY,identifier))
        status='error';summary='';digest='';snapshot=None
        try:
            config=json.loads(row['config'])
            if row['kind']=='supplier':
                if raw is None:
                    if not config['url']:raise ValueError('Укажите ссылку или загрузите проверочный файл.')
                    raw=self.fetcher(config['url'])['content']
                snapshot=feed(raw,config['format']);digest=hashlib.sha256(raw).hexdigest()
            else:
                if raw is not None:raise ValueError('Доставка рассчитывается через API.')
                snapshot=self.shipping(config)['quotes'];digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()
            summary='Предложений: '+str(len(snapshot));status='ok'
        except ValueError as error:summary=str(error)
        except Exception:summary='Проверка не завершена. Проверьте источник и подключение.'
        finally:
            with self.lock,self.connect() as db:
                try:
                    if status=='ok':db.execute('UPDATE sources SET last_status=?,last_success=?,snapshot=? WHERE id=?',(status,self.clock(),json.dumps(snapshot,ensure_ascii=False),identifier))
                    else:db.execute('UPDATE sources SET last_status=? WHERE id=?',(status,identifier))
                    db.execute('INSERT INTO history VALUES(?,?,?,?,?,?)',(uuid.uuid4().hex,identifier,self.clock(),status,summary[:1000],digest))
                    db.execute('DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY checked DESC LIMIT 500)')
                finally:self.active.discard(identifier)
        return {'status':status,'summary':summary}
    def select(self,body):
        key=text(body.get('material_key',''),'Ключ материала').casefold();unit=text(body.get('unit',''),'Единица').casefold()
        tax=body.get('tax_basis');quantity=Decimal(numeric_price(body.get('quantity')))
        if quantity<=0:raise ValueError('Количество должно быть больше нуля.')
        if tax not in ('vat_included','no_vat'):raise ValueError('Выберите налоговый состав цены.')
        offers=[]
        for source in self.status()['sources']:
            if source['kind']!='supplier' or not source['fresh']:continue
            for row in source['snapshot']:
                if row['material_key'].casefold()!=key or row['unit'].casefold()!=unit or row['tax_basis']!=tax or not row['available']:continue
                step=Decimal(row['package_step']);minimum=Decimal(row['minimum_quantity'])
                billed=(max(quantity,minimum)/step).to_integral_value(rounding=ROUND_CEILING)*step
                if Decimal(row['available_quantity'])<billed:continue
                cost=Decimal(row['price'])*billed
                offers.append({**row,'source_id':source['id'],'supplier':source['name'],'requested_quantity':str(quantity),'purchase_quantity':str(billed),
                               'total':str(cost),'checked_at':source['last_success'],'source_url':source['config']['url']})
        offers.sort(key=lambda row:(Decimal(row['total']),row['supplier'],row['article']))
        return {'selected':offers[0] if offers else None,'alternatives':offers,'notice':'Выбор по стоимости материала с упаковкой и минимальным отпуском. Доставка рассчитывается отдельно. Сходство названий не используется как взаимозаменяемость.'}
    def tick(self):
        with self.connect() as db:ids=[r['id'] for r in db.execute('SELECT id FROM sources WHERE enabled=1 AND next_due<=? ORDER BY next_due LIMIT 5',(self.clock(),))]
        for identifier in ids:self.refresh(identifier,scheduled=True)
    def start(self):
        with self.lock:
            if self.scheduler:return
            from apscheduler.schedulers.background import BackgroundScheduler
            self.scheduler=BackgroundScheduler(timezone='UTC',job_defaults={'coalesce':True,'max_instances':1})
            self.scheduler.add_job(self.tick,'interval',seconds=60,id='daily-suppliers-delivery',next_run_time=datetime.now(timezone.utc),misfire_grace_time=120)
            self.scheduler.start()
    def stop(self):
        with self.lock:scheduler=self.scheduler;self.scheduler=None
        if scheduler:scheduler.shutdown(wait=True)
