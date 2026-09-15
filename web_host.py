"""Single-owner hosted workspace behind HTTPS; original engine stays on loopback."""
from collections import deque
import hashlib
import hmac
from http.cookies import SimpleCookie
import http.client
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit
import server

ROOT=Path(__file__).resolve().parent
LIMIT=64*1024*1024

class Gateway:
    def __init__(self,backend,public_origin,password,*,clock=time.time):
        parsed=urlsplit(public_origin)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise ValueError('PUBLIC_ORIGIN должен быть адресом HTTPS без пути и параметров.')
        if not isinstance(password,str) or not 16<=len(password)<=256:raise ValueError('Задайте ESTIMATOR_OWNER_PASSWORD длиной от 16 до 256 символов.')
        self.origin='https://'+parsed.netloc;self.authority=parsed.netloc;self.backend=backend;self.clock=clock
        self.salt=secrets.token_bytes(32);self.password_hash=self.hash_password(password)
        self.lock=threading.RLock();self.sessions={};self.attempts=deque()
    def hash_password(self,password):return hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),self.salt,600000)
    def session(self,environ):
        try:
            cookie=SimpleCookie();cookie.load(environ.get('HTTP_COOKIE',''));token=cookie.get('__Host-estimator')
            if token is None:return None
            token=token.value
        except Exception:return None
        with self.lock:
            expiry=self.sessions.get(token,0)
            if expiry<=self.clock():self.sessions.pop(token,None);return None
        return token
    def response(self,start,status,value,*,mime='application/json; charset=utf-8',headers=()):
        body=json.dumps(value,ensure_ascii=False).encode() if isinstance(value,dict) else value
        base=[('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Strict-Transport-Security','max-age=31536000'),('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
        start(status,base+list(headers));return [body]
    def body(self,environ,limit=LIMIT):
        if environ.get('CONTENT_TYPE','').split(';')[0]!='application/json':raise ValueError('Нужен JSON-запрос.')
        raw_length=environ.get('CONTENT_LENGTH','')
        if not raw_length.isdigit() or not 0<int(raw_length)<=limit:raise ValueError('Недопустимый размер запроса.')
        raw=environ['wsgi.input'].read(int(raw_length))
        if len(raw)!=int(raw_length):raise ValueError('Запрос получен не полностью.')
        return raw
    def __call__(self,environ,start):
        try:return self.handle(environ,start)
        except ValueError as error:return self.response(start,'400 Bad Request',{'error':str(error)})
        except Exception:return self.response(start,'503 Service Unavailable',{'error':'Не удалось выполнить запрос. Сохранённые проекты находятся на сервере; повторите позже.'})
    def handle(self,environ,start):
        path=environ.get('PATH_INFO','/');method=environ.get('REQUEST_METHOD','GET')
        if path=='/healthz' and method in ('GET','HEAD'):
            return self.response(start,'200 OK',{'ok':True,'version':server.VERSION})
        if environ.get('HTTP_HOST')!=self.authority:return self.response(start,'403 Forbidden',{'error':'Адрес сайта не совпал с настройкой.'})
        if method not in ('GET','POST'):return self.response(start,'405 Method Not Allowed',{'error':'Метод не поддерживается.'})
        if method=='POST' and (environ.get('HTTP_ORIGIN')!=self.origin or environ.get('HTTP_SEC_FETCH_SITE')=='cross-site'):
            return self.response(start,'403 Forbidden',{'error':'Запрос с другого сайта запрещён.'})
        if path in ('/login.js','/login.css') and method=='GET':
            return self.response(start,'200 OK',(ROOT/'web'/path[1:]).read_bytes(),mime='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
        if path=='/auth/login' and method=='POST':
            with self.lock:
                while self.attempts and self.attempts[0]<self.clock()-300:self.attempts.popleft()
                if len(self.attempts)>=12:return self.response(start,'429 Too Many Requests',{'error':'Слишком много попыток. Повторите через пять минут.'})
                self.attempts.append(self.clock())
            value=json.loads(self.body(environ,4096));password=value.get('password','') if isinstance(value,dict) else ''
            if not isinstance(password,str) or len(password)>256 or not hmac.compare_digest(self.hash_password(password),self.password_hash):
                return self.response(start,'401 Unauthorized',{'error':'Пароль не подошёл.'})
            token=secrets.token_urlsafe(32)
            with self.lock:
                self.sessions={key:expiry for key,expiry in self.sessions.items() if expiry>self.clock()}
                if len(self.sessions)>=100:self.sessions.pop(next(iter(self.sessions)))
                self.sessions[token]=self.clock()+8*3600
            return self.response(start,'200 OK',{'ok':True},headers=[('Set-Cookie','__Host-estimator='+token+'; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')])
        session=self.session(environ)
        if not session:
            if path in ('/','/index.html','/login') and method=='GET':return self.response(start,'200 OK',(ROOT/'web/login.html').read_bytes(),mime='text/html; charset=utf-8')
            return self.response(start,'401 Unauthorized',{'error':'Войдите в рабочее место. Откройте главную страницу сайта.'})
        if method=='POST' and path in ('/auth/logout','/api/shutdown'):
            with self.lock:self.sessions.pop(session,None)
            return self.response(start,'200 OK',{'ok':True},headers=[('Set-Cookie','__Host-estimator=; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')])
        # A hosted process never exposes local Ollama or OS keychain operations.
        if path.startswith('/api/assistant/') or path.startswith('/api/secrets/'):
            return self.response(start,'400 Bad Request',{'error':'Эта функция доступна в настольной версии. Ключи серверных интеграций настраиваются в хостинге.'})
        raw=self.body(environ) if method=='POST' else None
        headers={'Host':self.backend.authority,'Origin':self.backend.origin}
        if method=='POST':headers.update({'Content-Type':'application/json','X-Estimator-Token':environ.get('HTTP_X_ESTIMATOR_TOKEN','')})
        connection=http.client.HTTPConnection('127.0.0.1',self.backend.server_port,timeout=150)
        try:
            connection.request(method,path,body=raw,headers=headers)
            response=connection.getresponse();body=response.read(LIMIT+1)
            if len(body)>LIMIT:raise ValueError('Ответ слишком большой. Разделите данные.')
            mime=response.getheader('Content-Type','application/json')
            if path=='/api/bootstrap' and response.status==200:
                value=json.loads(body);value['hosted']=True;body=json.dumps(value,ensure_ascii=False).encode()
            return self.response(start,str(response.status)+' '+response.reason,body,mime=mime)
        finally:connection.close()

def main():
    from waitress import serve
    origin=os.environ.get('PUBLIC_ORIGIN') or os.environ.get('RENDER_EXTERNAL_URL','')
    directory=os.environ.get('ESTIMATOR_DATA_DIR','')
    if not directory or not Path(directory).is_absolute():raise ValueError('Укажите постоянную папку ESTIMATOR_DATA_DIR.')
    backend=server.EstimatorServer(data_dir=directory)
    gateway=Gateway(backend,origin,os.environ.get('ESTIMATOR_OWNER_PASSWORD',''))
    backend.hosted=True
    thread=threading.Thread(target=backend.serve_forever,daemon=True);thread.start()
    backend.start_background();backend.hub().start()
    try:serve(gateway,host='0.0.0.0',port=int(os.environ.get('PORT','10000')),threads=8,max_request_body_size=LIMIT,channel_timeout=160,expose_tracebacks=False)
    finally:backend.shutdown();backend.server_close();thread.join(timeout=2)

if __name__=='__main__':main()
