"""Synthetic test responses. No customer facts or working API credentials."""
import io
import json
import urllib.parse

INN='7730588444'
OGRN='1087746982157'
KEY='synthetic-test-key-not-a-real-credential'
PDF=b'%PDF-1.4\n% Synthetic transport fixture, not an official extract\n'+b'% padding\n'*20+b'%%EOF\n'


class Response(io.BytesIO):
    def __init__(self,value,headers=None):
        raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode('utf-8')
        super().__init__(raw);self.headers=headers or {}


class QueueOpener:
    def __init__(self,*values):self.values=list(values);self.calls=[]
    def open(self,request,timeout):
        self.calls.append(request)
        if not self.values:raise AssertionError('Unexpected network request')
        value=self.values.pop(0)
        if isinstance(value,BaseException):raise value
        return Response(value)


def row(**extra):
    return dict({'i':INN,'o':OGRN,'p':'770501001','n':'ООО «Учебный заказчик <пример>»',
        'c':'ООО Учебный заказчик','a':'Учебный адрес, дом 1','g':'Тестовый руководитель',
        'r':'15.08.2008','t':'synthetic_document_token_123'},**extra)


def dadata(**extra):
    data={'inn':INN,'ogrn':OGRN,'kpp':'770501001','name':{'full_with_opf':'ООО «Учебный заказчик <пример>»',
        'short_with_opf':'ООО Учебный заказчик'},'management':{'name':'Тестовый руководитель'},
        'address':{'unrestricted_value':'Учебный адрес, дом 1'},
        'state':{'registration_date':1218758400000,'actuality_date':1789084800000,'status':'ACTIVE'},'invalid':True}
    data.update(extra)
    return {'suggestions':[{'value':'ООО Учебный заказчик','data':data}]}


class DemoFNSOpener:
    """Used only by the local developer UI harness."""
    def __init__(self):self.query=INN
    def open(self,request,timeout):
        path=urllib.parse.urlsplit(request.full_url).path
        if path=='/index.html':return Response(b'<html>synthetic</html>')
        if path=='/':
            self.query=urllib.parse.parse_qs(request.data.decode())['query'][0]
            return Response({'t':'synthetic_search_token_123'})
        if path.startswith('/search-result/'):
            if self.query=='7707083893':return Response({'captchaRequired':True})
            return Response({'rows':[row()]})
        if path.startswith('/vyp-request/'):return Response({})
        if path.startswith('/vyp-status/'):return Response({'status':'ready'})
        if path.startswith('/vyp-download/'):return Response(PDF)
        raise AssertionError('Unexpected endpoint')


class TickClock:
    def __init__(self):self.value=0
    def __call__(self):self.value+=4;return self.value
