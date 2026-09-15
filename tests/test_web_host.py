"""Security and persistence through the real HTTP engine; synthetic owner credentials."""
from io import BytesIO
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
import server
from web_host import Gateway

class HostedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.backend=server.EstimatorServer(data_dir=self.temp.name)
        self.thread=threading.Thread(target=self.backend.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
        self.gateway=Gateway(self.backend,'https://estimator.example','synthetic-test-password-ONLY')
    def close(self):self.backend.shutdown();self.backend.server_close();self.thread.join(timeout=2)
    def request(self,path='/',body=None,cookie='',origin='https://estimator.example',host='estimator.example',token=''):
        raw=json.dumps(body).encode() if body is not None else b'';output={}
        env={'PATH_INFO':path,'REQUEST_METHOD':'POST' if body is not None else 'GET','HTTP_HOST':host,'HTTP_ORIGIN':origin,
             'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),'wsgi.input':BytesIO(raw),'HTTP_COOKIE':cookie,'HTTP_X_ESTIMATOR_TOKEN':token}
        def start(status,headers):output.update(status=int(status.split()[0]),headers=dict(headers))
        output['body']=b''.join(self.gateway(env,start));return output
    def login(self):
        response=self.request('/auth/login',{'password':'synthetic-test-password-ONLY'})
        self.assertEqual(response['status'],200,response)
        self.assertIn('Secure; HttpOnly; SameSite=Strict',response['headers']['Set-Cookie'])
        return response['headers']['Set-Cookie'].split(';')[0]
    def test_unauthenticated_client_cannot_read_or_write_projects(self):
        for path,body in [('/api/bootstrap',None),('/app.js',None),('/api/workspace/list',{'kind':'order'}),('/api/shutdown',{})]:
            self.assertEqual(self.request(path,body)['status'],401)
        self.assertIn('Пароль владельца'.encode(),self.request()['body'])
    def test_wrong_password_origin_host_and_cookie_rejected(self):
        self.assertEqual(self.request('/auth/login',{'password':'wrong'})['status'],401)
        self.assertEqual(self.request('/auth/login',{'password':'synthetic-test-password-ONLY'},origin='https://bad.example')['status'],403)
        self.assertEqual(self.request(host='bad.example')['status'],403)
        self.assertEqual(self.request('/api/bootstrap',cookie='__Host-estimator=forged')['status'],401)
    def test_session_csrf_save_reopen_logout_and_restart(self):
        cookie=self.login();boot=json.loads(self.request('/api/bootstrap',cookie=cookie)['body'])
        self.assertTrue(boot['hosted']);token=boot['token']
        body={'kind':'order','name':'Проект до обновления','data':{'rows':[]}}
        self.assertEqual(self.request('/api/workspace/save',body,cookie=cookie)['status'],403)
        saved=self.request('/api/workspace/save',body,cookie=cookie,token=token);self.assertEqual(saved['status'],200,saved)
        record=json.loads(saved['body'])
        # Stop and recreate the HTTP engine as well, preserving only its data directory.
        self.close()
        self.backend=server.EstimatorServer(data_dir=self.temp.name)
        self.thread=threading.Thread(target=self.backend.serve_forever,daemon=True);self.thread.start()
        self.gateway=Gateway(self.backend,'https://estimator.example','synthetic-test-password-ONLY')
        self.assertEqual(self.request('/api/bootstrap',cookie=cookie)['status'],401)
        new_cookie=self.login()
        token=json.loads(self.request('/api/bootstrap',cookie=new_cookie)['body'])['token']
        value=self.request('/api/workspace/get',{'kind':'order','id':record['id']},cookie=new_cookie,token=token)
        self.assertEqual(json.loads(value['body'])['name'],'Проект до обновления')
        self.assertEqual(self.request('/auth/logout',{},cookie=new_cookie)['status'],200)
        self.assertEqual(self.request('/api/bootstrap',cookie=new_cookie)['status'],401)
        self.assertTrue(self.thread.is_alive())
    def test_login_rate_limit_and_expiry(self):
        cookie=self.login()
        with patch.object(self.gateway,'clock',return_value=self.gateway.clock()+9*3600):
            self.assertEqual(self.request('/api/bootstrap',cookie=cookie)['status'],401)
        self.gateway.attempts.extend([self.gateway.clock()]*12)
        self.assertEqual(self.request('/auth/login',{'password':'wrong'})['status'],429)
    def test_local_only_tools_unavailable_online(self):
        cookie=self.login()
        for path in ('/api/assistant/models','/api/secrets/save'):
            self.assertEqual(self.request(path,{},cookie=cookie)['status'],400)
