"""Explicit supplier price refresh, immutable previews and local source evidence.

No browser code is executed. Product support is limited to unambiguous JSON-LD
Product/Offer objects; source formats follow https://schema.org/Product and Offer.
"""
import base64
import copy
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from html.parser import HTMLParser
from io import StringIO
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

import engine
import file_formats
import local_store
from supplier_adapters import PRODUCT_FORMATS, check_origin, provider_rows
from price_pdf import read_pdf
from supplier_fetch import fetch_public

MAX_BYTES=8*1024*1024
LIFETIME=900


def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')


def numeric_price(value):
    if type(value) not in (str,int,float,Decimal):raise ValueError('Цена должна быть числом.')
    value=str(value).strip()
    if len(value)>80:raise ValueError('Неверный формат цены.')
    if re.fullmatch(r'[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+(?:[.,][0-9]{1,6})?',value):
        value=re.sub(r'[ \u00a0\u202f]','',value)
    # No ranges, currency symbols, "from", formulas or inferred numbers.
    if not re.fullmatch(r'[0-9]+(?:[.,][0-9]{1,6})?',value):
        raise ValueError('Нужна точная числовая цена; диапазоны, «от», формулы и «по запросу» не применяются.')
    result=engine._numeric_text(value,'Цена поставщика')
    return engine._plain(Decimal(result.replace(',','.')))


def rubles(value):return str(value).strip().casefold() in ('rub','руб','руб.','₽','643')


def csv_read(raw):
    try:content=raw.decode('utf-8-sig')
    except UnicodeError:
        try:content=raw.decode('cp1251')
        except UnicodeError:raise ValueError('CSV должен быть в UTF-8 или Windows-1251.') from None
    if '\0' in content or re.search(r'<(?:!doctype|html|script|body)\b',content[:2000],re.I):
        raise ValueError('Вместо CSV получена веб-страница или двоичный файл. Проверьте прямую ссылку на прайс.')
    try:delimiter=csv.Sniffer().sniff(content[:16000],delimiters=';,\t').delimiter
    except csv.Error:delimiter=';'
    rows=[]
    try:
        for number,values in enumerate(csv.reader(StringIO(content),delimiter=delimiter,strict=True),1):
            if len(rows)>=5001:raise ValueError('Прайс содержит более 5000 строк. Разделите файл.')
            if len(values)>100 or any(len(v)>10000 for v in values):raise ValueError('Слишком много столбцов или слишком длинная ячейка.')
            rows.append({'number':str(number),'values':values,'formulas':[]})
    except csv.Error:raise ValueError('Не удалось прочитать CSV. Проверьте разделители и кавычки.') from None
    if not rows:raise ValueError('CSV пуст.')
    return {'sheets':[{'name':'CSV','hidden':False}],'rows':rows,'warnings':[]}


class JsonLDScripts(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=False);self.reading=False;self.buffer=[];self.scripts=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script':
            values=dict(attrs);self.reading=(values.get('type') or '').lower().split(';')[0].strip()=='application/ld+json';self.buffer=[]
    def handle_data(self,data):
        if self.reading:self.buffer.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.reading:
            if len(self.scripts)>=100:raise ValueError('Слишком много блоков Product/Offer на странице.')
            self.scripts.append(''.join(self.buffer));self.buffer=[];self.reading=False


def is_type(value,name):
    types=value.get('@type',[])
    if isinstance(types,str):types=[types]
    return isinstance(types,list) and any(t in (name,'https://schema.org/'+name,'http://schema.org/'+name) for t in types)


def product_rows(raw,unit):
    try:html=raw.decode('utf-8-sig')
    except UnicodeError:raise ValueError('Страница Product/Offer должна быть в UTF-8.') from None
    parser=JsonLDScripts();parser.feed(html)
    products=[];issues=[];visited=0
    for script in parser.scripts:
        try:value=json.loads(script,parse_float=Decimal)
        except (ValueError,RecursionError):continue
        stack=[value]
        while stack:
            value=stack.pop();visited+=1
            if visited>30000:raise ValueError('Структурированные данные страницы слишком велики.')
            if isinstance(value,dict):
                if is_type(value,'Product'):products.append(value)
                stack.extend(v for v in value.values() if isinstance(v,(list,dict)))
            elif isinstance(value,list):stack.extend(value)
    if not products:raise ValueError('На странице нет поддерживаемых Product/Offer. Используйте CSV/XLSX поставщика или ручной ввод.')
    if len(products)>500:raise ValueError('На странице более 500 товаров. Используйте точную карточку товара.')
    rows=[]
    for i,product in enumerate(products,1):
        try:
            name=engine._text(product.get('name',''),'Наименование',300)
            if not name:raise ValueError('В карточке не указано наименование.')
            offers=product.get('offers')
            if isinstance(offers,list):
                if len(offers)!=1:raise ValueError('Несколько предложений или ступеней цены. Требуется выбор на сайте.')
                offers=offers[0]
            if not isinstance(offers,dict) or not is_type(offers,'Offer') or is_type(offers,'AggregateOffer'):
                raise ValueError('Нет однозначного Offer; минимальная цена диапазона не применяется.')
            if offers.get('eligibleQuantity') or offers.get('eligibleCustomerType') or offers.get('eligibleTransactionVolume'):
                raise ValueError('Цена зависит от объёма или категории покупателя. Используйте согласованный прайс.')
            business=offers.get('businessFunction','https://purl.org/goodrelations/v1#Sell')
            if not isinstance(business,str) or not business.endswith('#Sell'):raise ValueError('Предложение не является обычной продажей.')
            availability=offers.get('availability','')
            if availability and availability not in ('https://schema.org/InStock','http://schema.org/InStock','InStock'):
                raise ValueError('Товар не отмечен как имеющийся в наличии. Уточните предложение.')
            for key,expired in [('priceValidUntil',True),('validThrough',True),('validFrom',False)]:
                if offers.get(key):
                    moment=date.fromisoformat(str(offers[key])[:10])
                    if (expired and moment<date.today()) or (not expired and moment>date.today()):
                        raise ValueError('Предложение ещё не действует или срок цены истёк.')
            specification=offers.get('priceSpecification')
            if specification:
                if not isinstance(specification,dict):raise ValueError('Несколько компонентов цены. Нужна ручная проверка.')
                if specification.get('eligibleQuantity') or specification.get('minPrice') or specification.get('maxPrice'):
                    raise ValueError('Цена зависит от диапазона или объёма.')
                reference=specification.get('referenceQuantity')
                if reference:
                    if not isinstance(reference,dict) or Decimal(str(reference.get('value','0')))!=1:
                        raise ValueError('Цена относится к нескольким единицам. Уточните цену единицы.')
                    if reference.get('unitText') and str(reference['unitText']).casefold()!=unit.casefold():
                        raise ValueError('Единица в структурированной цене отличается от указанной вами.')
                    unit_code=reference.get('unitCode')
                    if unit_code:
                        known={'H87':{'шт','шт.','штука'},'MTK':{'м²','м2'},'MTR':{'м','пог. м'},'KGM':{'кг'}}
                        if unit_code not in known or unit.casefold() not in known[unit_code]:
                            raise ValueError('Код единицы в предложении требует проверки; пересчёт не выполняется.')
                if specification.get('price') is not None and numeric_price(specification['price'])!=numeric_price(offers.get('price')):
                    raise ValueError('В карточке противоречивые значения цены.')
            if not rubles(offers.get('priceCurrency','')):raise ValueError('В карточке не подтверждена валюта RUB.')
            price=numeric_price(offers.get('price'))
            article=product.get('sku') or product.get('mpn') or ''
            if not isinstance(article,str):raise ValueError('Артикул должен быть текстом с сохранением ведущих нулей.')
            article=engine._text(article,'Артикул',100)
            rows.append({'number':str(i),'values':[name,article,unit,price,'RUB'],'formulas':[]})
        except (ValueError,TypeError,ArithmeticError) as exc:
            issues.append({'row':str(i),'message':str(exc) or 'Неоднозначные сведения о цене товара.'})
    return {'sheets':[{'name':'Product/Offer','hidden':False}],'rows':rows,'issues':issues,
        'warnings':['Единица цены задаётся вами после проверки карточки. Сверьте артикул, налоговый состав, регион и комплектацию.']}


def document_table(raw,source):
    if source['format']=='pdf':return read_pdf(raw,source)
    if raw.startswith(b'%PDF-'):raise ValueError('Выберите формат PDF для этого файла.')
    if source['format'] in ('zenon','forda'):return provider_rows(raw,source)
    if source['format']=='csv':return csv_read(raw)
    if source['format']=='product':return product_rows(raw,source['unit'])
    result=file_formats.xlsx_read({'content':base64.b64encode(raw).decode('ascii'),'sheet':source['sheet']})
    result['warnings']=[result['note']]
    return result


class SupplierPrices:
    def __init__(self,store,directory,*,fetcher=fetch_public,clock=time.monotonic):
        self.store=store;self.directory=Path(directory);self.fetcher=fetcher;self.clock=clock
        self.lock=threading.RLock();self.runs={};self.previews={}

    def prune(self):
        timestamp=self.clock()
        self.runs={k:v for k,v in self.runs.items() if timestamp-v['created']<LIFETIME}
        self.previews={k:v for k,v in self.previews.items() if timestamp-v['created']<LIFETIME}
        while len(self.runs)>=8:self.runs.pop(next(iter(self.runs)))
        while len(self.previews)>=16:self.previews.pop(next(iter(self.previews)))

    def run(self,key):
        if not isinstance(key,str):raise ValueError('Неверный сеанс чтения прайса.')
        value=self.runs.get(key)
        if value is None or self.clock()-value['created']>=LIFETIME:
            raise ValueError('Прайс в сеансе устарел. Нажмите «Проверить цены» заново.')
        return value

    def save_document(self,raw,source,origin,stamp,original_name):
        digest=hashlib.sha256(raw).hexdigest();extension={'csv':'csv','xlsx':'xlsx','product':'txt','zenon':'txt','forda':'txt','pdf':'pdf'}[source['format']]
        document=local_store.normalize_price_document({'sha256':digest,'filename':'Price_'+stamp[:10]+'_'+digest[:12]+'.'+extension,
            'size':len(raw),'retrieved_at':stamp,'source_url':origin,'original_name':original_name})
        target=self.directory/(digest+'.bin')
        try:
            self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'wb') as stream:stream.write(raw)
        except FileExistsError:
            if target.is_symlink() or target.stat().st_size!=len(raw) or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
                raise ValueError('Исходный прайс в папке документов повреждён. Сохраните нужные копии и проверьте папку price-documents.') from None
        except OSError:raise ValueError('Не удалось сохранить исходный прайс. Проверьте права папки и свободное место.') from None
        return document

    def read(self,body):
        source=local_store.normalize_entity('price_source',body.get('source'))
        check_origin(source)
        if 'content' in body:
            if source['format'] in PRODUCT_FORMATS:raise ValueError('Для Product/Offer используйте публичную ссылку на карточку.')
            raw=file_formats.decode_file(body['content']);origin='';stamp=now()
            original=engine._text(body.get('file_name','Прайс'),'Имя файла',300).replace('\\','/').rsplit('/',1)[-1]
        else:
            if not source['url']:raise ValueError('Введите прямую ссылку на прайс или выберите файл CSV/XLSX.')
            response=self.fetcher(source['url']);check_origin({**source,'url':response['url']});raw=response['content'];origin=response['url'];stamp=response['retrieved_at'];original=''
        if not raw or len(raw)>MAX_BYTES:raise ValueError('Нужен непустой прайс размером до 8 МБ.')
        table=document_table(raw,source)
        document=self.save_document(raw,source,origin,stamp,original)
        with self.lock:
            self.prune();key=secrets.token_urlsafe(24)
            self.runs[key]={'created':self.clock(),'source':source,'raw':raw,'document':document,'tables':{self.table_key(source):table}}
        return {'run_id':key,'source':source,'document':document,'product_mode':source['format'] in PRODUCT_FORMATS,**table}

    def reopen(self,configuration,document):
        source=local_store.normalize_entity('price_source',configuration)
        saved=self.document(document)
        raw=base64.b64decode(saved['content'])
        table=document_table(raw,source)
        with self.lock:
            self.prune();key=secrets.token_urlsafe(24)
            self.runs[key]={'created':self.clock(),'source':source,'raw':raw,'document':document,'tables':{self.table_key(source):table}}
        return {'run_id':key,'source':source,'document':document,'product_mode':source['format'] in PRODUCT_FORMATS,**table}

    @staticmethod
    def table_key(source):
        return (source['sheet'],source.get('pdf_mode'),source.get('ocr_language'),source['unit'])

    def table(self,run,source):
        key=self.table_key(source)
        with self.lock:
            cached=run['tables'].get(key)
            if cached is not None:return copy.deepcopy(cached)
        value=document_table(run['raw'],source)
        with self.lock:
            if len(run['tables'])>=8:run['tables'].pop(next(iter(run['tables'])))
            run['tables'][key]=value
        return copy.deepcopy(value)

    def sheet(self,key,sheet):
        with self.lock:run=self.run(key);source={**run['source'],'sheet':sheet}
        source=local_store.normalize_entity('price_source',source)
        return self.table(run,source)

    def preview(self,key,configuration):
        source=local_store.normalize_entity('price_source',configuration)
        with self.lock:run=self.run(key)
        if any(source[k]!=run['source'][k] for k in ('url','format')):
            raise ValueError('Ссылка или формат изменились. Загрузите прайс заново.')
        table=self.table(run,source);issues=list(table.get('issues',[]));entries=[]
        mapping=source['mapping'];start=source['start']
        if source['format'] in PRODUCT_FORMATS:
            if not source['unit']:raise ValueError('Укажите единицу цены после проверки карточки товара.')
            mapping=dict(name=0,article=1,unit=2,price=3,currency=4);start=0
        else:
            if any(mapping[k]<0 for k in ('name','price')):raise ValueError('Выберите столбцы названия и цены.')
            if mapping['unit']<0 and not source['unit']:raise ValueError('Выберите столбец единицы или явно укажите общую единицу цены.')
            active=[v for v in mapping.values() if v>=0]
            if len(active)!=len(set(active)):raise ValueError('Для разных полей выберите разные столбцы.')
            if mapping['currency']<0 and source['currency']!='RUB':raise ValueError('Выберите столбец валюты или явно укажите, что цены файла в рублях.')
        rows=table['rows'][start:]
        if len(rows)>500:raise ValueError('За один раз можно проверить до 500 позиций. Разделите прайс.')
        document=run['document']
        with self.store.connection() as db:
            existing=[{**dict(record),'data':json.loads(record['data'])} for record in db.execute("SELECT id,revision,data FROM records WHERE kind='catalog'")]
        index={}
        def identity(item):return ((item.get('article') or item['name']).casefold(),item.get('supplier','').casefold())
        for item in existing:index.setdefault(identity(item['data']),[]).append(item)
        parsed=[]
        for row in rows:
            if not any(v.strip() for v in row['values']):continue
            def value(k):
                column=mapping[k]
                return row['values'][column].strip() if 0<=column<len(row['values']) else ''
            try:
                if any(column in row['formulas'] for column in mapping.values() if column>=0):
                    raise ValueError('В выбранных полях есть формулы. Сохраните прайс как значения.')
                currency=value('currency') if mapping['currency']>=0 else source['currency']
                if not rubles(currency):raise ValueError('Цена не подтверждена как рублёвая. Конвертация валют не выполняется.')
                candidate={'name':value('name'),'unit':value('unit') if mapping['unit']>=0 else source['unit'],'price':numeric_price(value('price')),
                    'supplier':source['supplier'],'article':value('article'),'quantity':'1','category':'material','minimum_charge':'0'}
                candidate=local_store.normalize_entity('catalog',candidate)
                parsed.append((row,candidate))
            except ValueError as exc:issues.append({'row':row['number'],'message':str(exc)})
        counts={}
        for _,candidate in parsed:counts[identity(candidate)]=counts.get(identity(candidate),0)+1
        for row,candidate in parsed:
            try:
                target=identity(candidate)
                if counts[target]>1:raise ValueError('В прайсе повторяется артикул / название. Возможны разные условия или ступени цены; строка исключена.')
                matches=index.get(target,[])
                if len(matches)>1:raise ValueError('В справочнике несколько совпадений. Уточните артикулы и поставщика.')
                previous=matches[0] if matches else None
                if previous and previous['data']['unit'].casefold()!=candidate['unit'].casefold():
                    raise ValueError('Единица изменилась: '+previous['data']['unit']+' / '+candidate['unit']+'. Автоматический пересчёт не выполняется.')
                old_price=previous['data']['price'] if previous else None
                row_data=copy.deepcopy(previous['data'] if previous else candidate)
                excerpt=' | '.join(row['values'])[:2000]
                observation={'document':document,'source_date':source['source_date'],'row':str(row['number']),
                    'sheet':table['sheets'][source['sheet'] if source['format']=='xlsx' else 0]['name'],
                    'excerpt':excerpt,'price':candidate['price'],'currency':'RUB','unit':candidate['unit'],'article':candidate['article'],'notes':source['notes']}
                origin=document['source_url'] or ('Файл: '+document['original_name'])
                evidence='Проверка '+document['retrieved_at']+'; '+origin+'; '+observation['sheet']+', строка '+str(row['number'])
                row_data.update(price=candidate['price'],source=evidence,price_date=source['source_date'],confirmed=False,
                    source_status='published_snapshot',price_observation=observation)
                row_data=local_store.normalize_entity('catalog',row_data)
                same=old_price not in ('',None) and Decimal(old_price.replace(',','.'))==Decimal(candidate['price'])
                percent=None
                if old_price not in ('',None) and Decimal(old_price.replace(',','.'))!=0:
                    percent=str(((Decimal(candidate['price'])/Decimal(old_price.replace(',','.'))-1)*100).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
                warning='Проверьте налоговый состав, регион, комплектацию и условия поставки. Цена требует подтверждения для сметы.'
                if not candidate['article']:warning+=' Артикул отсутствует: совпадение только по названию.'
                if not source['source_date']:warning+=' Дата публикации цены не установлена.'
                if source['notes']:warning+=' '+source['notes']
                if not previous:
                    # A missing article can change matching from name to SKU. Do not silently duplicate a same-name item.
                    near=[item for item in existing if item['data']['name'].casefold()==candidate['name'].casefold() and item['data'].get('supplier','').casefold()==candidate['supplier'].casefold()]
                    if near:raise ValueError('Наименование уже есть, но артикул отличается или отсутствует. Уточните карточку перед обновлением.')
                proposal={'id':previous['id'] if previous else None,'expected_revision':previous['revision'] if previous else None,'name':row_data['name'],'data':row_data,'old_price':old_price}
                entries.append({'key':secrets.token_hex(12),'name':row_data['name'],'supplier':candidate['supplier'],'article':candidate['article'],
                    'unit':candidate['unit'],'old_price':old_price,'new_price':candidate['price'],'change_percent':percent,
                    'action':'unchanged' if same else 'update' if previous else 'new','warning':warning,'source_date':source['source_date'],'proposal':proposal})
            except ValueError as exc:issues.append({'row':row['number'],'message':str(exc)})
        with self.lock:
            self.prune();preview_id=secrets.token_urlsafe(24)
            self.previews[preview_id]={'created':self.clock(),'entries':entries,'used':False}
        return {'preview_id':preview_id,'items':[{k:v for k,v in item.items() if k!='proposal'} for item in entries],
                'issues':issues,'document':document,'checked_count':len(rows),'warnings':table.get('warnings',[])}

    def apply(self,key,keys,acknowledged):
        if acknowledged is not True:raise ValueError('Подтвердите проверку валюты, единицы, артикула и условий цены.')
        if not isinstance(keys,list) or not keys or len(keys)>500 or any(not isinstance(k,str) for k in keys) or len(keys)!=len(set(keys)):
            raise ValueError('Выберите от 1 до 500 разных позиций.')
        with self.lock:
            preview=self.previews.get(key) if isinstance(key,str) else None
            if not preview or preview['used'] or self.clock()-preview['created']>=LIFETIME:raise ValueError('Просмотр устарел или уже применён. Проверьте цены заново.')
            index={item['key']:item['proposal'] for item in preview['entries']}
            if any(k not in index for k in keys):raise ValueError('Выбранная позиция отсутствует в этом просмотре.')
            result=self.store.import_apply([index[k] for k in keys])
            preview['used']=True
        return {**result,'note':'Цены сохранены в «Мои материалы». Прежние сметы сохранили свои значения; новые цены требуют проверки для конкретного заказа.'}

    def document(self,value):
        document=local_store.normalize_price_document(value);path=self.directory/(document['sha256']+'.bin')
        if path.is_symlink() or not path.is_file():raise ValueError('Исходный прайс отсутствует на этом компьютере. Перенесите папку price-documents или загрузите источник заново.')
        if path.stat().st_size!=document['size']:raise ValueError('Размер исходного прайса изменился. Получите его заново.')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=document['sha256']:raise ValueError('Контрольная сумма прайса не совпадает. Получите источник заново.')
        return {'content':base64.b64encode(raw).decode('ascii'),'filename':document['filename']}
