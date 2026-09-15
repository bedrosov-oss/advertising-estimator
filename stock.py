"""Local inventory with decimal balances, transaction isolation and idempotent writes."""
from contextlib import contextmanager
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import re
import sqlite3
import threading
import uuid
import engine


class Stock:
    def __init__(self,store):
        self.store=store;self.path=store.directory/'stock.sqlite';self.lock=threading.RLock()
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS balances(item_id TEXT PRIMARY KEY,name TEXT NOT NULL,unit TEXT NOT NULL,on_hand TEXT NOT NULL,reserved TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reservations(order_id TEXT NOT NULL,item_id TEXT NOT NULL,quantity TEXT NOT NULL,PRIMARY KEY(order_id,item_id));
                CREATE TABLE IF NOT EXISTS movements(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,request_hash TEXT NOT NULL,time TEXT NOT NULL,kind TEXT NOT NULL,item_id TEXT NOT NULL,order_id TEXT NOT NULL,quantity TEXT NOT NULL,on_hand TEXT NOT NULL,reserved TEXT NOT NULL,note TEXT NOT NULL);''')
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def status(self):
        with self.lock,self.connection() as db:
            items=[dict(r) for r in db.execute('SELECT * FROM balances ORDER BY name')]
            reservations=[dict(r) for r in db.execute('SELECT * FROM reservations WHERE quantity<>\'0\'')]
            history=[dict(r) for r in db.execute('SELECT time,kind,item_id,order_id,quantity,on_hand,reserved,note FROM movements ORDER BY time DESC LIMIT 100')]
        for item in items:item['available']=engine._plain(Decimal(item['on_hand'])-Decimal(item['reserved']))
        return {'items':items,'reservations':reservations,'history':history}

    def assert_delete_allowed(self,kind,identifier):
        with self.lock,self.connection() as db:
            if kind=='catalog':
                row=db.execute('SELECT on_hand,reserved FROM balances WHERE item_id=?',(identifier,)).fetchone()
                if row and (Decimal(row['on_hand']) or Decimal(row['reserved'])):raise ValueError('У материала есть складской остаток или резерв. Сначала завершите складские операции.')
            if kind=='order':
                rows=db.execute('SELECT quantity FROM reservations WHERE order_id=?',(identifier,))
                if any(Decimal(r['quantity']) for r in rows):raise ValueError('У заказа есть резерв материалов. Сначала спишите или освободите его.')

    def move(self,body):
        kind=body.get('kind');identifier=body.get('item_id');order=body.get('order_id') or '';request=body.get('request_id')
        if kind not in ('receipt','reserve','release','consume','writeoff'):raise ValueError('Неизвестная складская операция.')
        if not isinstance(request,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',request):raise ValueError('Нужен идентификатор складской операции.')
        quantity=engine._number(body.get('quantity'),'Количество',positive=True,maximum='1000000000')
        if quantity*1000000!=(quantity*1000000).to_integral_value():raise ValueError('Допустимо до 6 знаков после запятой.')
        note=engine._text(body.get('note',''),'Основание операции',1000)
        if not note:raise ValueError('Укажите основание: накладную, заказ или причину списания.')
        if kind in ('receipt','writeoff'):order=''
        payload={'kind':kind,'item_id':identifier,'order_id':order,'quantity':engine._plain(quantity),'note':note}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        with self.store.lock,self.lock,self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT id,request_hash FROM movements WHERE request_id=?',(request,)).fetchone()
            if previous:
                if previous['request_hash']!=digest:raise ValueError('Идентификатор операции уже использован с другими данными.')
                return {'id':previous['id'],'replayed':True}
            item=self.store.get('catalog',identifier)['data']
            if item['category']!='material':raise ValueError('На складе учитываются только позиции категории «Материал».')
            if kind in ('reserve','release','consume'):self.store.get('order',order)
            balance=db.execute('SELECT * FROM balances WHERE item_id=?',(identifier,)).fetchone()
            if balance and balance['unit'].casefold()!=item['unit'].casefold():raise ValueError('Единица материала отличается от складской. Автоматическая конвертация не выполняется.')
            on_hand=Decimal(balance['on_hand']) if balance else Decimal(0);reserved=Decimal(balance['reserved']) if balance else Decimal(0)
            reservation=db.execute('SELECT quantity FROM reservations WHERE order_id=? AND item_id=?',(order,identifier)).fetchone()
            amount=Decimal(reservation['quantity']) if reservation else Decimal(0)
            if kind=='receipt':on_hand+=quantity
            elif kind=='writeoff':
                if on_hand-reserved<quantity:raise ValueError('Недостаточно свободного остатка для списания.')
                on_hand-=quantity
            elif kind=='reserve':
                if on_hand-reserved<quantity:raise ValueError('Недостаточно свободного остатка для резервирования.')
                reserved+=quantity;amount+=quantity
            else:
                if amount<quantity:raise ValueError('Количество превышает резерв этого заказа.')
                amount-=quantity;reserved-=quantity
                if kind=='consume':on_hand-=quantity
            if not Decimal(0)<=reserved<=on_hand<=Decimal('1000000000000'):raise ValueError('Складские остатки вышли за допустимые границы.')
            db.execute('INSERT INTO balances VALUES (?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET name=excluded.name,on_hand=excluded.on_hand,reserved=excluded.reserved',
                       (identifier,item['name'],item['unit'],engine._plain(on_hand),engine._plain(reserved)))
            if order:db.execute('INSERT INTO reservations VALUES (?,?,?) ON CONFLICT(order_id,item_id) DO UPDATE SET quantity=excluded.quantity',(order,identifier,engine._plain(amount)))
            movement=uuid.uuid4().hex
            db.execute('INSERT INTO movements VALUES (?,?,?,?,?,?,?,?,?,?,?)',(movement,request,digest,datetime.now(timezone.utc).isoformat(),kind,identifier,order,engine._plain(quantity),engine._plain(on_hand),engine._plain(reserved),note))
        return {'id':movement,'replayed':False}
