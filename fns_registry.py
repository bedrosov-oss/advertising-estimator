"""Single-customer EGRUL/EGRIP lookup using DaData or the public FNS website.

FNS uses a website adapter, not a guaranteed public API. No CAPTCHA solving,
external redirects, credential storage or automatic switching between providers.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE = 'https://egrul.nalog.ru/index.html'
ORIGIN = 'https://egrul.nalog.ru'
MAX_JSON = 1024 * 1024
MAX_PDF = 12 * 1024 * 1024
SESSION_SECONDS = 600
DADATA_SOURCE = 'https://dadata.ru/api/find-party/'
DADATA_ENDPOINT = 'https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party'


class FNSFailure(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def clean_text(value, label, limit=2000):
    if not isinstance(value, str) or len(value) > limit or any(
        (ord(c) < 32 and c not in '\n\r\t') or 0xD800 <= ord(c) <= 0xDFFF for c in value
    ):
        raise ValueError(label + ': неверный текст.')
    return value.strip()


def identifier(value):
    text = re.sub(r'\s+', '', clean_text(value, 'ИНН / ОГРН', 40))
    if not re.fullmatch(r'(?:[0-9]{10}|[0-9]{12}|[0-9]{13}|[0-9]{15})', text) or len(set(text)) == 1:
        raise ValueError('Введите ИНН (10 или 12 цифр), ОГРН (13) либо ОГРНИП (15).')
    digits = list(map(int, text))
    checksum = lambda weights: sum(a*b for a,b in zip(weights, digits)) % 11 % 10
    valid = ((len(text) == 10 and checksum([2,4,10,3,5,9,4,6,8]) == digits[9]) or
             (len(text) == 12 and checksum([7,2,4,10,3,5,9,4,6,8]) == digits[10]
              and checksum([3,7,2,4,10,3,5,9,4,6,8]) == digits[11]) or
             (len(text) in (13,15) and int(text[:-1]) % (11 if len(text)==13 else 13) % 10 == digits[-1]))
    if not valid:
        raise ValueError('Контрольные цифры ИНН / ОГРН не совпадают. Проверьте номер.')
    return text


def normal_date(value):
    value = clean_text(value or '', 'Дата ФНС', 20)
    if not value:
        return ''
    for fmt in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError('ФНС вернула неизвестный формат даты.')


def normalize_document(value):
    if not value:
        return {}
    if not isinstance(value, dict):
        raise ValueError('Неверное описание выписки.')
    digest = clean_text(value.get('sha256',''), 'Контрольная сумма', 64)
    filename = clean_text(value.get('filename',''), 'Имя выписки', 120)
    size = value.get('size')
    if not re.fullmatch('[a-f0-9]{64}', digest) or not re.fullmatch(r'[A-Za-z0-9_-]+\.pdf', filename):
        raise ValueError('Неверное имя или контрольная сумма выписки.')
    if type(size) is not int or not 100 <= size <= MAX_PDF:
        raise ValueError('Неверный размер выписки.')
    return {'sha256':digest, 'filename':filename, 'size':size,
            'retrieved_at':clean_text(value.get('retrieved_at',''), 'Время получения',40)}


def normalize_customer(value):
    if not value:
        return {}
    if not isinstance(value, dict):
        raise ValueError('Карточка заказчика должна быть объектом.')
    result = {key:clean_text(value.get(key,''), label, limit) for key,label,limit in [
        ('query','Запрос ФНС',40),('full_name','Полное наименование',4000),
        ('short_name','Краткое наименование',1000),('inn','ИНН',12),('ogrn','ОГРН',15),
        ('kpp','КПП',9),('address','Адрес',4000),('head','Руководитель',2000),
        ('registration_date','Дата регистрации',10),('termination_date','Дата прекращения',10),
        ('retrieved_at','Время получения',40),('actuality_date','Дата изменений',10),
        ('status','Статус из источника',100)]}
    for key, lengths in [('inn',(10,12)),('ogrn',(13,15)),('kpp',(9,))]:
        if result[key] and (not result[key].isascii() or not result[key].isdigit() or len(result[key]) not in lengths):
            raise ValueError('Неверный формат ' + key.upper() + ' в карточке заказчика.')
    for key in ('registration_date','termination_date','actuality_date'):
        if result[key]:
            date.fromisoformat(result[key])
    if result['query']:
        result['query'] = identifier(result['query'])
        if result['query'] not in (result['inn'],result['ogrn']):
            raise ValueError('Реквизиты заказчика не соответствуют запросу ФНС.')
    provider = value.get('provider', 'fns')
    if provider not in ('fns', 'dadata'):
        raise ValueError('Неизвестный источник реквизитов.')
    result['provider'] = provider
    result['source_url'] = SOURCE if provider == 'fns' else DADATA_SOURCE
    result['invalid'] = value.get('invalid') is True
    result['document'] = normalize_document(value.get('document',{}))
    if provider != 'fns' and result['document']:
        raise ValueError('Карточка DaData не содержит оригинал выписки ФНС.')
    return result


def customer_lines(value):
    """Common plain-text requisites for the estimate's HTML/PDF/XLSX exports."""
    item = normalize_customer(value)
    if not item:
        return []
    lines = ['Полное наименование заказчика: '+item['full_name']] if item['full_name'] else []
    numbers = [label+' '+item[key] for key,label in [('inn','ИНН'),('kpp','КПП'),('ogrn','ОГРН/ОГРНИП')] if item[key]]
    if numbers: lines.append(' · '.join(numbers))
    if item['address']: lines.append('Адрес заказчика: '+item['address'])
    label = 'ФНС' if item['provider']=='fns' else 'DaData'
    if item['retrieved_at']: lines.append('Источник реквизитов: '+label+'; получены: '+item['retrieved_at'])
    if item['actuality_date']: lines.append('Дата последних изменений по данным источника: '+item['actuality_date'])
    if item['termination_date']: lines.append('В источнике указана дата прекращения: '+item['termination_date'])
    if item['invalid']: lines.append('Источник сообщает о недостоверных сведениях. Требуется проверка реквизитов.')
    return lines


def dadata_date(value):
    if value is None:
        return ''
    if type(value) not in (int, float):
        raise ValueError('Неизвестный формат даты DaData.')
    try:
        return datetime.fromtimestamp(value/1000, timezone.utc).date().isoformat()
    except (ValueError, OSError, OverflowError):
        raise ValueError('Неверная дата в ответе DaData.') from None


def dadata_search(query, key, *, opener=None):
    """Documented authenticated API; never stores a key or returns raw payloads.

    https://dadata.ru/api/find-party/ (checked 2026-09-13).
    Only head offices are requested; subsidiaries require an explicit KPP feature.
    """
    query = identifier(query)
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', key):
        raise FNSFailure('key_required','Введите API-ключ DaData из личного кабинета. Для этого метода Secret key не нужен.')
    request = urllib.request.Request(DADATA_ENDPOINT,
        data=json.dumps({'query':query,'count':100,'branch_type':'MAIN'}).encode('utf-8'),
        headers={'Content-Type':'application/json','Accept':'application/json',
                 'Authorization':'Token '+key,'User-Agent':'AdvertisingEstimator/1.0'})
    # Reject redirects so the authorization header can never reach another host.
    opener = opener or urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            raw=response.read(MAX_JSON+1)
    except urllib.error.HTTPError as exc:
        status=exc.code; exc.close()
        if status in (401,403):
            raise FNSFailure('key_rejected','DaData отклонила доступ. Проверьте API-ключ, подтверждение почты и доступный лимит в кабинете.') from None
        if status in (402,429):
            raise FNSFailure('rate_limit','DaData сообщает о лимите запросов или ограничении тарифа. Проверьте кабинет и повторите позже.') from None
        raise FNSFailure('unavailable',f'DaData вернула HTTP {status}. Повторите позже или выберите ФНС.') from None
    except FNSFailure:
        raise FNSFailure('redirect','DaData перенаправила запрос. Ключ не отправлен на другой адрес.') from None
    except (OSError, urllib.error.URLError):
        raise FNSFailure('unavailable','Нет соединения с DaData. Проверьте интернет или выберите ФНС.') from None
    if len(raw)>MAX_JSON:
        raise FNSFailure('too_large','Ответ DaData слишком велик. Уточните поиск на сайте сервиса.')
    try:
        result=json.loads(raw.decode('utf-8-sig'))
        if not isinstance(result,dict) or not isinstance(result.get('suggestions'),list) or len(result['suggestions'])>100:
            raise ValueError()
        items=[]
        for suggestion in result['suggestions']:
            data=suggestion['data']
            if query not in (data.get('inn'),data.get('ogrn')):
                raise FNSFailure('mismatch','DaData вернула реквизиты другого номера. Карточка не изменена.')
            name=data.get('name') or {}; state=data.get('state') or {}
            customer=normalize_customer({'provider':'dadata','query':query,
                'full_name':name.get('full_with_opf') or suggestion.get('unrestricted_value') or suggestion.get('value') or '',
                'short_name':name.get('short_with_opf') or '', 'inn':data.get('inn') or '',
                'ogrn':data.get('ogrn') or '', 'kpp':data.get('kpp') or '',
                'address':(data.get('address') or {}).get('unrestricted_value') or (data.get('address') or {}).get('value') or '',
                'head':(data.get('management') or {}).get('name') or '',
                'registration_date':dadata_date(state.get('registration_date')),
                'termination_date':dadata_date(state.get('liquidation_date')),
                'actuality_date':dadata_date(state.get('actuality_date')),
                'status':state.get('status') or '', 'invalid':data.get('invalid') is True,
                'retrieved_at':utc_now()})
            if not customer['full_name']:
                raise ValueError()
            items.append({'choice_id':str(len(items)), 'customer':customer})
        if len(items)==100:
            raise FNSFailure('too_many','Найдено слишком много записей. Используйте ОГРН или ОГРНИП вместо ИНН.')
        return {'status':'ready','query':query,'results':items,'source_url':DADATA_SOURCE}
    except FNSFailure:
        raise
    except (ValueError, TypeError, AttributeError, KeyError):
        raise FNSFailure('invalid_response','Не удалось прочитать реквизиты DaData. Формат ответа изменился или данные неполны.') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FNSFailure('browser_required', 'ФНС перенаправила запрос. Откройте сервис ФНС в браузере.')


class RegistryService:
    def __init__(self, directory, *, opener_factory=None, clock=time.monotonic):
        self.directory = Path(directory)
        self.sessions = {}
        self.lock = threading.RLock()
        self.clock = clock
        self.next_search = 0
        self.opener_factory = opener_factory or (lambda: urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()), NoRedirect()))

    def _request(self, session, path, form=None, *, pdf=False, html=False, ack=False):
        # Paths are formed exclusively from constants and server-validated tokens.
        if not re.fullmatch(r'/(?:index\.html|(?:search-result|vyp-request|vyp-status|vyp-download)/[A-Za-z0-9_-]{8,4096})?', path):
            raise FNSFailure('invalid_response','Неверный адрес ответа ФНС.')
        request = urllib.request.Request(ORIGIN+path,
            data=None if form is None else urllib.parse.urlencode(form).encode('ascii'),
            headers={'User-Agent':'AdvertisingEstimator/1.0 (local desktop)',
                     'Accept':'application/pdf' if pdf else 'application/json, text/html;q=0.8',
                     'Accept-Language':'ru-RU,ru;q=0.9', 'Accept-Encoding':'identity',
                     'Referer':SOURCE, 'X-Requested-With':'XMLHttpRequest',
                     'Content-Type':'application/x-www-form-urlencoded'})
        limit = MAX_PDF if pdf else MAX_JSON
        try:
            with session['opener'].open(request, timeout=20) as response:
                length = response.headers.get('Content-Length','')
                if length.isdigit() and int(length)>limit:
                    raise FNSFailure('too_large','Ответ ФНС превышает допустимый размер. Скачайте выписку на сайте.')
                raw = response.read(limit+1)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code == 429:
                raise FNSFailure('rate_limit','ФНС ограничила частоту запросов. Повторите попытку позже.') from None
            if code in (401,403):
                raise FNSFailure('browser_required','ФНС не разрешила прямой запрос. Откройте сервис в браузере.') from None
            raise FNSFailure('unavailable',f'Соединение с ФНС вернуло HTTP {code}. Повторите позже или откройте сайт.') from None
        except (OSError, urllib.error.URLError):
            raise FNSFailure('unavailable','Нет соединения с ФНС. Проверьте интернет и доступ к egrul.nalog.ru.') from None
        if len(raw)>limit:
            raise FNSFailure('too_large','Ответ ФНС слишком велик. Скачайте выписку на сайте.')
        if pdf:
            if len(raw)<100 or not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-4096:]:
                raise FNSFailure('invalid_pdf','ФНС вернула ответ, который не является полной PDF-выпиской.')
            return raw
        if html:
            return None
        if ack and not raw.strip():
            return {}
        try:
            value = json.loads(raw.decode('utf-8-sig'))
        except (ValueError, UnicodeError):
            raise FNSFailure('browser_required','ФНС вернула веб-страницу вместо данных. Продолжите на сайте ФНС.') from None
        if not isinstance(value,dict):
            raise FNSFailure('invalid_response','Формат ответа ФНС изменился. Используйте сайт для получения выписки.')
        if value.get('captchaRequired') in (True, 'true', 1) or value.get('captcha') is True:
            raise FNSFailure('captcha_required','ФНС требует капчу. Откройте сайт и пройдите проверку самостоятельно.')
        if value.get('ERRORS') or value.get('error') or value.get('errors'):
            raise FNSFailure('browser_required','ФНС сообщила об ошибке запроса. Проверьте номер или продолжите на сайте.')
        return value

    @staticmethod
    def _token(value):
        if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{8,4096}',value):
            raise FNSFailure('invalid_response','ФНС не выдала пригодный идентификатор запроса.')
        return value

    @contextmanager
    def _session(self, lookup_id):
        if not isinstance(lookup_id,str):
            raise ValueError('Неверный сеанс поиска ФНС.')
        with self.lock:
            session = self.sessions.get(lookup_id)
            if session is None or self.clock()-session['created']>SESSION_SECONDS:
                self.sessions.pop(lookup_id,None)
                raise FNSFailure('expired','Сеанс ФНС истёк. Повторите поиск по номеру заказчика.')
        if not session['lock'].acquire(blocking=False):
            raise FNSFailure('busy','Предыдущий запрос ФНС ещё выполняется.')
        try:
            if session.get('blocked'):
                raise FNSFailure('browser_required','Этот сеанс ФНС остановлен после ограничения. Используйте сайт либо повторите поиск позже.')
            yield session
        except FNSFailure as exc:
            if exc.code in ('captcha_required','browser_required','rate_limit'):
                session['blocked']=True
            raise
        finally:
            session['lock'].release()

    def search(self, query):
        query = identifier(query)
        with self.lock:
            now = self.clock()
            if now < self.next_search:
                raise FNSFailure('rate_limit','Подождите несколько секунд перед следующим поиском ФНС.')
            self.next_search = now+3
            self.sessions = {k:v for k,v in self.sessions.items() if now-v['created']<=SESSION_SECONDS}
            if len(self.sessions)>=32:
                raise FNSFailure('busy','Слишком много незавершённых поисков. Повторите позже.')
        session = {'query':query,'created':now,'opener':self.opener_factory(),'lock':threading.Lock(),'choices':{}}
        self._request(session,'/index.html',html=True)
        response = self._request(session,'/',{'query':query,'page':'','region':'','nameEq':'on',
                                              'vyp3CaptchaToken':'','PreventChromeAutocomplete':''})
        session['search_token'] = self._token(response.get('t'))
        lookup_id = secrets.token_urlsafe(24)
        with self.lock:
            self.sessions[lookup_id]=session
        return {'lookup_id':lookup_id,'status':'pending','query':query}

    def search_result(self, lookup_id):
        with self._session(lookup_id) as session:
            if 'result' in session:
                return session['result']
            data = self._request(session,'/search-result/'+session['search_token'])
            if str(data.get('status','')).lower() in ('wait','pending','processing'):
                return {'status':'pending','lookup_id':lookup_id}
            rows = data.get('rows')
            if not isinstance(rows,list) or len(rows)>100:
                raise FNSFailure('invalid_response','Неизвестный или слишком большой результат ФНС. Уточните запрос на сайте.')
            items=[]
            for row in rows:
                if not isinstance(row,dict):
                    raise FNSFailure('invalid_response','Повреждена строка результата ФНС.')
                if str(row.get('tot',''))=='0':
                    continue
                if session['query'] not in (row.get('i'),row.get('o')):
                    raise FNSFailure('mismatch','ФНС вернула реквизиты, не совпадающие с введённым номером. Карточка не изменена.')
                value = {'query':session['query'], 'retrieved_at':utc_now(), 'source_url':SOURCE}
                for key,remote in [('full_name','n'),('short_name','c'),('inn','i'),('ogrn','o'),('kpp','p'),('address','a'),('head','g')]:
                    value[key]=row.get(remote) or ''
                value['full_name'] = value['full_name'] or value['short_name']
                if not value['full_name']:
                    raise FNSFailure('invalid_response','В ответе ФНС отсутствует наименование заказчика.')
                value['registration_date']=normal_date(row.get('r'))
                value['termination_date']=normal_date(row.get('e'))
                customer = normalize_customer(value)
                choice_id = secrets.token_hex(16)
                session['choices'][choice_id]={'data':customer,'token':self._token(row.get('t'))}
                items.append({'choice_id':choice_id,'customer':customer})
            result={'status':'ready','lookup_id':lookup_id,'query':session['query'],'results':items,'source_url':SOURCE}
            session['result']=result
            return result

    def _choice(self, session, choice_id):
        if not isinstance(choice_id,str) or choice_id not in session['choices']:
            raise ValueError('Выберите заказчика из результата этого поиска ФНС.')
        return session['choices'][choice_id]

    def extract_start(self, lookup_id, choice_id):
        with self._session(lookup_id) as session:
            item = self._choice(session,choice_id)
            if not item.get('requested'):
                self._request(session,'/vyp-request/'+item['token'],ack=True)
                item['requested']=True
            return {'status':'pending'}

    def extract_result(self, lookup_id, choice_id):
        with self._session(lookup_id) as session:
            item = self._choice(session,choice_id)
            if not item.get('requested'):
                raise ValueError('Сначала запросите формирование выписки.')
            if item.get('document'):
                return {'status':'ready','document':item['document']}
            result = self._request(session,'/vyp-status/'+item['token'])
            status = str(result.get('status','')).lower()
            if status in ('wait','pending','processing'):
                return {'status':'pending'}
            if status != 'ready':
                raise FNSFailure('invalid_response','ФНС не подтвердила готовность выписки. Повторите поиск позже.')
            raw = self._request(session,'/vyp-download/'+item['token'],pdf=True)
            digest=hashlib.sha256(raw).hexdigest()
            target=self.directory/(digest+'.pdf')
            try:
                self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
                fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as stream:
                    stream.write(raw)
            except FileExistsError:
                if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
                    raise FNSFailure('storage_error','Сохранённая выписка повреждена. Проверьте папку документов программы.') from None
            except OSError:
                raise FNSFailure('storage_error','Не удалось сохранить выписку на компьютере. Проверьте свободное место и права папки.') from None
            stamp=utc_now()
            item['document']={'sha256':digest,'size':len(raw),'retrieved_at':stamp,
                'filename':'FNS_'+(item['data']['ogrn'] or item['data']['inn'])+'_'+stamp[:10]+'_'+digest[:10]+'.pdf'}
            return {'status':'ready','document':item['document']}

    def saved_document(self, description):
        description=normalize_document(description)
        if not description:
            raise ValueError('Описание выписки отсутствует.')
        path=self.directory/(description['sha256']+'.pdf')
        if path.is_symlink() or not path.is_file():
            raise FNSFailure('missing_document','PDF не найден на этом компьютере. Перенесите папку fns-documents или получите новую выписку.')
        if path.stat().st_size!=description['size']:
            raise FNSFailure('storage_error','Размер сохранённой выписки изменился. Получите её заново.')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=description['sha256']:
            raise FNSFailure('storage_error','Контрольная сумма выписки не совпадает. Получите её заново.')
        return raw,description
