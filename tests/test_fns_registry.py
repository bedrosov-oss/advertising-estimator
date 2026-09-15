"""Behavioral checks for external adapters using synthetic responses only."""
import os
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch
import zipfile

import fns_registry as fns
import local_store
import reporting
import file_formats
from registry_fixtures import INN, OGRN, KEY, PDF, QueueOpener, row, dadata


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.clock=100

    def service(self,*responses):
        self.opener=QueueOpener(*responses)
        return fns.RegistryService(Path(self.temp.name)/'fns-documents',opener_factory=lambda:self.opener,clock=lambda:self.clock)

    def lookup(self,service):
        lookup=service.search(INN)['lookup_id']
        result=service.search_result(lookup)
        return lookup,result['results'][0]['choice_id'],result['results'][0]['customer']

    def test_identifier_types_and_checksums(self):
        for value in [INN,OGRN,'784806113663','308774631700332',' 7730 588444 ']:
            self.assertTrue(fns.identifier(value).isdigit())
        for value in ['0000000000','7730588443','784806113664','1087746982158','308774631700333','１２３４５６７８９０',None,'12',INN+';']:
            with self.subTest(value=value),self.assertRaises(ValueError):fns.identifier(value)

    def test_bad_identifier_and_key_do_not_send_requests(self):
        service=self.service()
        with self.assertRaises(ValueError):service.search('1234567890')
        with self.assertRaises(fns.FNSFailure):fns.dadata_search(INN,'',opener=self.opener)
        self.assertFalse(self.opener.calls)

    def test_fns_search_pending_and_exact_requisites(self):
        service=self.service(b'<html></html>',{'t':'synthetic_search_token'},{'status':'wait'},{'rows':[row()]})
        lookup=service.search(INN)['lookup_id']
        self.assertEqual(service.search_result(lookup)['status'],'pending')
        response=service.search_result(lookup);customer=response['results'][0]['customer']
        self.assertEqual(customer['registration_date'],'2008-08-15')
        self.assertEqual(customer['inn'],INN);self.assertEqual(customer['provider'],'fns')
        self.assertNotIn('synthetic_document_token',json.dumps(response))
        form=urllib.parse.parse_qs(self.opener.calls[1].data.decode())
        self.assertEqual(form,{'query':[INN],'nameEq':['on']})
        self.assertEqual(service.search_result(lookup),response)
        self.assertEqual(len(self.opener.calls),4)

    def test_exact_match_required(self):
        service=self.service(b'<html></html>',{'t':'synthetic_search_token'},{'rows':[row(i='7707083893')]})
        lookup=service.search(INN)['lookup_id']
        with self.assertRaises(fns.FNSFailure) as caught:service.search_result(lookup)
        self.assertEqual(caught.exception.code,'mismatch')

    def test_not_found_is_distinct_from_invalid_reply(self):
        for payload,expected in [({'rows':[]},'ready'),({'rows':[{'tot':'0'}]},'ready'),({},'invalid_response')]:
            service=self.service(b'<html></html>',{'t':'synthetic_search_token'},payload)
            lookup=service.search(INN)['lookup_id']
            if expected=='ready':self.assertEqual(service.search_result(lookup)['results'],[])
            else:
                with self.assertRaises(fns.FNSFailure) as caught:service.search_result(lookup)
                self.assertEqual(caught.exception.code,expected)

    def test_captcha_stops_session_without_retry(self):
        service=self.service(b'<html></html>',{'t':'synthetic_search_token'},{'captchaRequired':True})
        lookup=service.search(INN)['lookup_id']
        with self.assertRaises(fns.FNSFailure) as caught:service.search_result(lookup)
        self.assertEqual(caught.exception.code,'captcha_required')
        with self.assertRaises(fns.FNSFailure):service.search_result(lookup)
        self.assertEqual(len(self.opener.calls),3)

    def test_http_failures_are_actionable(self):
        for status,expected in [(403,'browser_required'),(429,'rate_limit'),(502,'unavailable')]:
            error=urllib.error.HTTPError(fns.ORIGIN,status,'error',{},None)
            service=self.service(error)
            with self.assertRaises(fns.FNSFailure) as caught:service.search(INN)
            self.assertEqual(caught.exception.code,expected)

    def test_unexpected_html_and_path_tokens_fail_closed(self):
        for reply in [b'<html>captcha</html>',{'t':'../../secret'},{'t':'https://other.example/token'}]:
            service=self.service(b'<html></html>',reply)
            with self.assertRaises(fns.FNSFailure):service.search(INN)
            self.assertEqual(len(self.opener.calls),2)

    def test_expiry_and_rate_limit_before_network(self):
        service=self.service(b'<html></html>',{'t':'synthetic_search_token'})
        lookup=service.search(INN)['lookup_id']
        with self.assertRaises(fns.FNSFailure):service.search(INN)
        self.clock+=601
        with self.assertRaises(fns.FNSFailure) as caught:service.search_result(lookup)
        self.assertEqual(caught.exception.code,'expired');self.assertEqual(len(self.opener.calls),2)

    def test_original_pdf_saved_and_reopened_offline(self):
        service=self.service(b'<html></html>',{'t':'synthetic_search_token'},{'rows':[row()]},b'',{'status':'wait'},{'status':'ready'},PDF)
        lookup,choice,customer=self.lookup(service)
        service.extract_start(lookup,choice);service.extract_start(lookup,choice)
        self.assertEqual(service.extract_result(lookup,choice)['status'],'pending')
        document=service.extract_result(lookup,choice)['document']
        self.assertEqual(document['sha256'],hashlib.sha256(PDF).hexdigest())
        path=service.directory/(document['sha256']+'.pdf')
        self.assertEqual(path.read_bytes(),PDF)
        if os.name != 'nt':self.assertEqual(path.stat().st_mode&0o777,0o600)
        reopened=fns.RegistryService(service.directory)
        self.assertEqual(reopened.saved_document(document)[0],PDF)
        self.assertEqual(service.extract_result(lookup,choice)['document'],document)
        path.write_bytes(PDF.replace(b'padding',b'changed'))
        with self.assertRaises(fns.FNSFailure):reopened.saved_document(document)

    def test_truncated_pdf_does_not_create_file(self):
        service=self.service(b'<html></html>',{'t':'synthetic_search_token'},{'rows':[row()]},{},{'status':'ready'},b'%PDF-1.4\ntruncated')
        lookup,choice,_=self.lookup(service);service.extract_start(lookup,choice)
        with self.assertRaises(fns.FNSFailure) as caught:service.extract_result(lookup,choice)
        self.assertEqual(caught.exception.code,'invalid_pdf');self.assertFalse(service.directory.exists())

    def test_document_paths_and_missing_files(self):
        document={'sha256':'a'*64,'size':200,'filename':'../../file.pdf'}
        with self.assertRaises(ValueError):fns.normalize_document(document)
        document['filename']='FNS_test.pdf'
        with self.assertRaises(fns.FNSFailure) as caught:self.service().saved_document(document)
        self.assertEqual(caught.exception.code,'missing_document')

    def test_large_response_is_rejected(self):
        opener=QueueOpener(b'x'*(fns.MAX_JSON+1))
        with self.assertRaises(fns.FNSFailure) as caught:fns.dadata_search(INN,KEY,opener=opener)
        self.assertEqual(caught.exception.code,'too_large')

    def test_dadata_requisites_and_private_key_scope(self):
        opener=QueueOpener(dadata())
        result=fns.dadata_search(INN,KEY,opener=opener);customer=result['results'][0]['customer']
        self.assertEqual(customer['provider'],'dadata');self.assertTrue(customer['invalid'])
        self.assertEqual(customer['source_url'],fns.DADATA_SOURCE);self.assertEqual(customer['document'],{})
        self.assertTrue(customer['actuality_date']);self.assertEqual(customer['head'],'Тестовый руководитель')
        req=opener.calls[0]
        self.assertEqual(req.full_url,fns.DADATA_ENDPOINT)
        self.assertEqual(req.get_header('Authorization'),'Token '+KEY)
        self.assertEqual(json.loads(req.data),{'query':INN,'count':100,'branch_type':'MAIN'})
        self.assertNotIn(KEY,json.dumps(result));self.assertNotIn('api_key',json.dumps(result))

    def test_dadata_ogrnip_missing_fields_and_multiple_entries(self):
        reply=dadata(inn='784806113663',ogrn='308774631700332',kpp=None,management=None,address=None,state=None)
        reply['suggestions']*=2
        items=fns.dadata_search('308774631700332',KEY,opener=QueueOpener(reply))['results']
        self.assertEqual(len(items),2);self.assertNotEqual(items[0]['choice_id'],items[1]['choice_id'])
        self.assertEqual(items[0]['customer']['kpp'],'');self.assertEqual(items[0]['customer']['address'],'')

    def test_dadata_failures_and_no_secret_in_errors(self):
        for code,expected in [(401,'key_rejected'),(403,'key_rejected'),(429,'rate_limit'),(502,'unavailable')]:
            opener=QueueOpener(urllib.error.HTTPError(fns.DADATA_ENDPOINT,code,KEY,{},None))
            with self.assertRaises(fns.FNSFailure) as caught:fns.dadata_search(INN,KEY,opener=opener)
            self.assertEqual(caught.exception.code,expected);self.assertNotIn(KEY,str(caught.exception))
        for payload in [b'<html>error</html>',{'suggestions':[None]},dadata(inn='7707083893'),dadata(state={'registration_date':float('inf')})]:
            with self.assertRaises(fns.FNSFailure):fns.dadata_search(INN,KEY,opener=QueueOpener(payload))

    def test_redirects_never_forward_authorization(self):
        with self.assertRaises(fns.FNSFailure):
            fns.NoRedirect().redirect_request(None,None,302,'',{},'https://other.example/')
        opener=QueueOpener(fns.FNSFailure('browser_required','redirect'))
        with self.assertRaises(fns.FNSFailure) as caught:fns.dadata_search(INN,KEY,opener=opener)
        self.assertEqual(caught.exception.code,'redirect')

    def test_order_versions_backup_and_exports_preserve_source(self):
        customer=fns.dadata_search(INN,KEY,opener=QueueOpener(dadata()))['results'][0]['customer']
        customer['api_key']=KEY;customer['search_token']='must-not-save'
        project={'project':{'title':'Учебная смета','client':'ООО Учебный заказчик'},
            'extensions':{'customer_query':INN,'customer':customer,'api_key':KEY}}
        normalized=local_store.normalize_project(project)
        self.assertNotIn(KEY,json.dumps(normalized));self.assertNotIn('must-not-save',json.dumps(normalized))
        store=local_store.LocalStore(self.temp.name);saved=store.save('order','Проверка',project)
        again=local_store.LocalStore(self.temp.name).get('order',saved['id'])['data']
        self.assertEqual(again,normalized)
        backup=store.backup();self.assertNotIn(KEY,json.dumps(backup))
        second=local_store.LocalStore(Path(self.temp.name)/'restored');second.restore(backup)
        self.assertEqual(second.list('order')[0]['name'],'Проверка')
        report=reporting.export_html(again,True)
        self.assertIn(INN,report);self.assertIn('DaData',report);self.assertIn('&lt;пример&gt;',report)
        self.assertNotIn('Сведения ФНС получены',report)
        with zipfile.ZipFile(BytesIO(file_formats.export_xlsx(again))) as archive:
            sheet=archive.read('xl/worksheets/sheet1.xml').decode()
            self.assertIn(INN,sheet);self.assertIn('DaData',sheet)
        self.assertIn(INN,' '.join(file_formats.report_content(again,{},True)[1]))
        from pypdf import PdfReader
        pdf=PdfReader(BytesIO(file_formats.export_pdf(again,client=True)))
        contents='\n'.join(page.extract_text() for page in pdf.pages)
        self.assertIn(INN,contents);self.assertIn('DaData',contents)
        self.assertIn('Учебный заказчик <пример>',contents);self.assertNotIn(KEY,contents)
        other=copy.deepcopy(project);other['extensions']['customer_query']='7707083893'
        with self.assertRaises(ValueError):local_store.normalize_project(other)
        partial=local_store.normalize_project({'extensions':{'customer_query':'123'}})
        self.assertEqual(partial['extensions']['customer_query'],'123')


if __name__=='__main__':unittest.main()
