"""Supplier refresh with local documents and deterministic synthetic web responses."""
import os
import base64
import copy
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import local_store
import supplier_prices as prices

CSV='Наименование;Артикул;Единица;Цена;Валюта\nПВХ;0001;лист;125,50;RUB\nПечать;0002;м²;200;RUB\n'


def configuration(**extra):
    return {**{'supplier':'Поставщик теста','url':'https://supplier.example/prices.csv','format':'csv','sheet':0,'start':1,
        'mapping':{'name':0,'article':1,'unit':2,'price':3,'currency':4},'currency':'unknown','unit':'','source_date':'','notes':''},**extra}


def product(**extra):
    return {**{'@type':'Product','name':'ПВХ','sku':'0001','offers':{'@type':'Offer','price':'125.50','priceCurrency':'RUB','availability':'https://schema.org/InStock'}},**extra}


def html(value):return ('<html><script type="application/ld+json">'+json.dumps(value,ensure_ascii=False)+'</script></html>').encode()


class SupplierPricesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=local_store.LocalStore(self.temp.name);self.clock=10
        self.fetch=Mock(return_value={'content':CSV.encode(),'url':'https://supplier.example/prices.csv','retrieved_at':'2026-09-13T12:00:00+00:00','content_type':'text/csv'})
        self.service=prices.SupplierPrices(self.store,Path(self.temp.name)/'price-documents',fetcher=self.fetch,clock=lambda:self.clock)

    def existing(self,**updates):
        return self.store.save('catalog','ПВХ',dict({'name':'ПВХ','article':'0001','supplier':'Поставщик теста','unit':'лист','price':'100',
            'quantity':'5','increment':'2','minimum_charge':'50','category':'work','note':'Сохранить комплектацию и условия',
            'confirmed':True,'source':'Прежний прайс','price_date':'2026-09-01'},**updates))

    def read(self,content=CSV,**settings):
        source=configuration(**settings)
        payload=content.encode() if isinstance(content,str) else content
        return source,self.service.read({'source':source,'content':base64.b64encode(payload).decode(),'file_name':'прайс.csv'})

    def test_selected_update_preserves_terms_and_orders(self):
        old=self.existing();order=self.store.save('order','Согласованный заказ',{'rows':[old['data']]})
        source,read=self.read();preview=self.service.preview(read['run_id'],source)
        self.assertEqual(len(preview['items']),2);first=preview['items'][0]
        self.assertEqual((first['old_price'],first['new_price'],first['change_percent']),('100','125.5','25.50'))
        self.assertEqual(first['action'],'update');self.assertNotIn('proposal',first)
        self.assertEqual(self.store.get('catalog',old['id'])['data']['price'],'100')
        result=self.service.apply(preview['preview_id'],[first['key']],True)
        self.assertEqual(result['saved'],1);current=self.store.get('catalog',old['id'])['data']
        self.assertEqual(current['price'],'125.5');self.assertFalse(current['confirmed'])
        for key in ('quantity','increment','minimum_charge','category','note'):self.assertEqual(current[key],old['data'][key])
        self.assertEqual(current['price_date'],'');self.assertEqual(current['price_observation']['source_date'],'')
        self.assertIn('price_observation',current);self.assertEqual(current['price_observation']['document'],read['document'])
        self.assertEqual(self.store.get('order',order['id'])['data']['rows'][0]['price'],'100')
        self.assertEqual(len(self.store.list('catalog')),1)
        with self.assertRaisesRegex(ValueError,'уже применён'):self.service.apply(preview['preview_id'],[first['key']],True)

    def test_read_remote_and_file_keep_original_bytes_without_mutating_catalog(self):
        source=configuration();result=self.service.read({'source':source})
        self.fetch.assert_called_once_with(source['url'])
        saved=self.service.document(result['document'])
        self.assertEqual(base64.b64decode(saved['content']),CSV.encode())
        self.assertEqual(self.store.list('catalog'),[])
        path=self.service.directory/(result['document']['sha256']+'.bin')
        if os.name != 'nt':self.assertEqual(path.stat().st_mode&0o777,0o600)
        other=prices.SupplierPrices(self.store,self.service.directory)
        self.assertEqual(other.document(result['document']),saved)
        path.write_bytes(b'broken')
        with self.assertRaises(ValueError):other.document(result['document'])

    def test_failed_download_does_not_change_price_or_date(self):
        old=self.existing();self.fetch.side_effect=ValueError('Поставщик вернул HTTP 502')
        with self.assertRaisesRegex(ValueError,'502'):self.service.read({'source':configuration()})
        self.assertEqual(self.store.get('catalog',old['id']),old);self.assertFalse(self.service.directory.exists())

    def test_foreign_unknown_and_malformed_prices_are_issues(self):
        bad='Имя;Артикул;Ед;Цена;Валюта\n'+''.join(f'Материал{i};A{i};лист;{p};{c}\n' for i,(p,c) in enumerate([
            ('99','USD'),('10',''),('от 100','RUB'),('100-200','RUB'),('по запросу','RUB'),('=1+1','RUB'),('-1','RUB')]))
        source,read=self.read(bad);preview=self.service.preview(read['run_id'],source)
        self.assertEqual(preview['items'],[]);self.assertEqual(len(preview['issues']),7)
        self.assertEqual(self.store.list('catalog'),[])

    def test_unit_mismatch_and_duplicates_never_create_second_item(self):
        self.existing()
        for content in [CSV.replace('0001;лист','0001;шт.'),CSV+CSV.splitlines()[1]+'\n']:
            source,read=self.read(content);preview=self.service.preview(read['run_id'],source)
            self.assertFalse(any(item['article']=='0001' for item in preview['items']))
            self.assertTrue(preview['issues'])
        self.assertEqual(len(self.store.list('catalog')),1)

    def test_changed_or_missing_article_does_not_silently_duplicate(self):
        self.existing()
        for article in ('','00001'):
            source,read=self.read(CSV.replace(';0001;', ';'+article+';'))
            preview=self.service.preview(read['run_id'],source)
            self.assertFalse(any(item['name']=='ПВХ' for item in preview['items']))
            self.assertTrue(preview['issues'])

    def test_revision_conflict_is_atomic(self):
        old=self.existing();source,read=self.read();preview=self.service.preview(read['run_id'],source)
        self.store.save('catalog','ПВХ',{**old['data'],'price':'111'},old['id'],old['revision'])
        keys=[entry['key'] for entry in reversed(preview['items'])]
        with self.assertRaisesRegex(ValueError,'изменился'):self.service.apply(preview['preview_id'],keys,True)
        self.assertEqual(len(self.store.list('catalog')),1);self.assertEqual(self.store.get('catalog',old['id'])['data']['price'],'111')

    def test_apply_requires_review_and_server_owned_keys(self):
        source,read=self.read();preview=self.service.preview(read['run_id'],source);key=preview['items'][0]['key']
        for keys,ack in [([key],False),(['invented'],True),([key,key],True),([],True)]:
            with self.assertRaises(ValueError):self.service.apply(preview['preview_id'],keys,ack)
        self.assertEqual(self.store.list('catalog'),[])

    def test_old_preview_expires_and_changed_url_requires_read(self):
        source,read=self.read();preview=self.service.preview(read['run_id'],source)
        with self.assertRaisesRegex(ValueError,'изменились'):self.service.preview(read['run_id'],{**source,'url':'https://other.example/'})
        self.clock+=901
        with self.assertRaises(ValueError):self.service.preview(read['run_id'],source)
        with self.assertRaises(ValueError):self.service.apply(preview['preview_id'],[preview['items'][0]['key']],True)

    def test_explicit_unit_and_currency_defaults_and_cp1251(self):
        source,read=self.read('Товар;Цена\nПВХ;0\n'.encode('cp1251'),unit='лист',currency='RUB',mapping={'name':0,'price':1})
        preview=self.service.preview(read['run_id'],source)
        self.assertEqual(preview['items'][0]['new_price'],'0');self.assertEqual(preview['items'][0]['unit'],'лист')
        with self.assertRaisesRegex(ValueError,'рублях'):self.service.preview(read['run_id'],{**source,'currency':'unknown'})

    def test_equal_price_can_refresh_evidence_without_false_increase(self):
        old=self.existing(price='125.50');source,read=self.read(source_date='2026-09-10')
        preview=self.service.preview(read['run_id'],source);item=preview['items'][0]
        self.assertEqual(item['action'],'unchanged');self.assertEqual(item['change_percent'],'0.00')
        self.service.apply(preview['preview_id'],[item['key']],True)
        self.assertEqual(self.store.get('catalog',old['id'])['data']['price_date'],'2026-09-10')

    def test_source_and_observation_survive_backup_without_secrets(self):
        record=self.store.save('price_source','Наш прайс',{**configuration(),'api_key':'drop-me'})
        self.assertNotIn('api_key',record['data'])
        source,read=self.read();preview=self.service.preview(read['run_id'],source)
        self.service.apply(preview['preview_id'],[preview['items'][0]['key']],True)
        backup=self.store.backup();new=local_store.LocalStore(Path(self.temp.name)/'new');new.restore(backup)
        self.assertEqual(new.list('price_source')[0]['data'],record['data'])
        self.assertEqual(new.list('catalog')[0]['data']['price_observation']['document'],read['document'])

    def test_xlsx_sheets_formulas_and_leading_zero_articles(self):
        from openpyxl import Workbook
        workbook=Workbook();sheet=workbook.active;sheet.title='Условия';sheet.append(['Условия прайса'])
        sheet=workbook.create_sheet('Цены');sheet.append(['Товар','Артикул','Ед.','Цена','Валюта'])
        sheet.append(['ПВХ','0001','лист',125.5,'RUB']);sheet.append(['Формула','0003','лист','=1+1','RUB'])
        buffer=BytesIO();workbook.save(buffer)
        source,read=self.read(buffer.getvalue(),format='xlsx',sheet=1)
        self.assertEqual(len(read['sheets']),2)
        self.assertEqual(self.service.sheet(read['run_id'],0)['rows'][0]['values'][0],'Условия прайса')
        preview=self.service.preview(read['run_id'],source)
        self.assertEqual(len(preview['items']),1);self.assertEqual(preview['items'][0]['article'],'0001')
        self.assertEqual(len(preview['issues']),1)

    def test_pdf_html_instead_csv_and_size_limits(self):
        for raw in [b'%PDF-1.4 file',b'<html>CAPTCHA</html>',b'',b'x'*(prices.MAX_BYTES+1)]:
            with self.assertRaises(ValueError):self.read(raw)
        source,read=self.read('Товар;Артикул;Ед;Цена;Валюта\n'+''.join(f'A{i};{i};шт.;1;RUB\n' for i in range(501)))
        with self.assertRaisesRegex(ValueError,'500'):self.service.preview(read['run_id'],source)

    def test_jsonld_precise_product_and_safe_document_download(self):
        self.fetch.return_value['content']=html({'@graph':[product()]})
        source=configuration(format='product',unit='лист');read=self.service.read({'source':source})
        preview=self.service.preview(read['run_id'],source)
        self.assertEqual(preview['items'][0]['new_price'],'125.5');self.assertEqual(preview['items'][0]['article'],'0001')
        self.assertTrue(read['document']['filename'].endswith('.txt'))

    def test_jsonld_ambiguous_foreign_expired_and_quantity_prices_are_rejected(self):
        base=product();offer=base['offers']
        for changes in [dict(priceCurrency='USD'),dict(priceCurrency=None),dict(price='от 100'),dict(availability='OutOfStock'),
                dict(priceValidUntil='2001-01-01'),dict(validFrom='2099-01-01'),dict(eligibleQuantity={'minValue':5}),
                dict(priceSpecification={'referenceQuantity':{'value':10}}),
                dict(priceSpecification={'referenceQuantity':{'value':1,'unitText':'м²'}}),dict(priceSpecification=[{'price':1}]),
                dict(priceSpecification={'price':'100'}),dict(businessFunction='https://purl.org/goodrelations/v1#LeaseOut')]:
            result=prices.product_rows(html(product(offers={**offer,**changes})),'лист')
            self.assertEqual(result['rows'],[]);self.assertTrue(result['issues'])
        for offers in [[offer,offer],{'@type':'AggregateOffer','lowPrice':'10','highPrice':'20','priceCurrency':'RUB'}]:
            self.assertFalse(prices.product_rows(html(product(offers=offers)),'лист')['rows'])
        with self.assertRaisesRegex(ValueError,'нет поддерживаемых'):prices.product_rows(b'<html>captcha</html>','лист')


if __name__=='__main__':unittest.main()
