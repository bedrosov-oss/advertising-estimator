"""Persistent price checks; a scheduled job never modifies catalog prices."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def stamp(value=None):return datetime.fromtimestamp(time.time() if value is None else value,timezone.utc).isoformat(timespec='seconds')


class PriceMonitor:
    def __init__(self,prices,*,clock=time.time):
        self.prices=prices;self.store=prices.store;self.clock=clock
        self.path=self.store.directory/'price-monitor.sqlite';self.lock=threading.RLock();self.active=set();self.scheduler=None
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS schedules(source_id TEXT PRIMARY KEY, hours INTEGER NOT NULL, enabled INTEGER NOT NULL, next_due REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS checks(id TEXT PRIMARY KEY, source_id TEXT NOT NULL, started TEXT NOT NULL, finished TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL, payload TEXT NOT NULL);''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def start(self):
        with self.lock:
            if self.scheduler:return
            try:from apscheduler.schedulers.background import BackgroundScheduler
            except ImportError:raise ValueError('Для расписания установите дополнения SETUP_FEATURES.') from None
            self.scheduler=BackgroundScheduler(timezone='UTC',job_defaults={'coalesce':True,'max_instances':1})
            self.scheduler.add_job(self.tick,'interval',seconds=60,id='price-checks',misfire_grace_time=120)
            self.scheduler.start()

    def stop(self):
        with self.lock:
            scheduler=self.scheduler;self.scheduler=None
        if scheduler:scheduler.shutdown(wait=False)

    def configure(self,body):
        source_id=body.get('source_id');source=self.store.get('price_source',source_id)
        hours=body.get('hours',24);enabled=body.get('enabled',False)
        if type(hours) is not int or not 1<=hours<=168 or type(enabled) is not bool:
            raise ValueError('Интервал должен быть целым числом от 1 до 168 часов.')
        if enabled and not source['data']['url']:raise ValueError('Для расписания нужна сохранённая публичная ссылка, а не локальный файл.')
        if enabled:self.start()
        with self.lock,self.connect() as db:
            db.execute('INSERT INTO schedules VALUES (?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET hours=excluded.hours,enabled=excluded.enabled,next_due=excluded.next_due',
                       (source_id,hours,int(enabled),self.clock()+hours*3600))
        return self.status()

    def status(self):
        with self.connect() as db:
            schedules=[dict(r) for r in db.execute('SELECT * FROM schedules')]
            checks=[dict(r) for r in db.execute('SELECT id,source_id,started,finished,status,summary FROM checks ORDER BY finished DESC LIMIT 100')]
        for row in schedules:
            row['enabled']=bool(row['enabled']);row['next_due']=stamp(row['next_due'])
        return {'schedules':schedules,'checks':checks,'running':self.scheduler is not None,
                'notice':'Проверки выполняются, пока программа открыта и компьютер не спит. После запуска пропущенная проверка выполняется один раз. Цены сохраняются только после вашего подтверждения.'}

    def tick(self):
        with self.connect() as db:
            ids=[r['source_id'] for r in db.execute('SELECT source_id FROM schedules WHERE enabled=1 AND next_due<=? ORDER BY next_due LIMIT 5',(self.clock(),))]
        for source_id in ids:self.check(source_id,scheduled=True)

    def check(self,source_id,scheduled=False):
        with self.lock:
            if source_id in self.active:raise ValueError('Проверка этого поставщика уже выполняется.')
            with self.connect() as db:
                cfg=db.execute('SELECT * FROM schedules WHERE source_id=?',(source_id,)).fetchone()
                if scheduled and (not cfg or not cfg['enabled'] or cfg['next_due']>self.clock()):return {'skipped':True}
                if cfg:db.execute('UPDATE schedules SET next_due=? WHERE source_id=?',(self.clock()+cfg['hours']*3600,source_id))
            self.active.add(source_id)
        started=stamp(self.clock());identifier=uuid.uuid4().hex;payload={};status='error';summary=''
        try:
            record=self.store.get('price_source',source_id)
            # Repeated retrieval does not establish the publication date of a new price list.
            source={**record['data'],'source_date':''}
            read=self.prices.read({'source':source})
            payload={'source':source,'document':read['document']}
            try:
                preview=self.prices.preview(read['run_id'],source)
                changed=sum(item['action']!='unchanged' for item in preview['items'])
                summary=f"Изменений: {changed}; замечаний: {len(preview['issues'])}. Откройте для проверки."
                status='review'
            except ValueError as exc:
                status='mapping';summary='Документ получен. '+str(exc)
        except ValueError as exc:summary=str(exc)
        except Exception:summary='Проверка не завершена. Проверьте подключение, настройки и комплектность программы.'
        finally:
            with self.lock:
                try:
                    with self.connect() as db:
                        db.execute('INSERT INTO checks VALUES (?,?,?,?,?,?,?)',(identifier,source_id,started,stamp(self.clock()),status,summary[:2000],json.dumps(payload,ensure_ascii=False)))
                        db.execute('DELETE FROM checks WHERE id NOT IN (SELECT id FROM checks ORDER BY finished DESC LIMIT 200)')
                finally:self.active.discard(source_id)
        return {'id':identifier,'status':status,'summary':summary}

    def review(self,identifier):
        if not isinstance(identifier,str):raise ValueError('Выберите запись журнала.')
        with self.connect() as db:row=db.execute('SELECT payload FROM checks WHERE id=?',(identifier,)).fetchone()
        if not row:raise ValueError('Запись журнала отсутствует.')
        payload=json.loads(row['payload'])
        if not payload:raise ValueError('У этой проверки нет полученного документа.')
        return self.prices.reopen(payload['source'],payload['document'])
