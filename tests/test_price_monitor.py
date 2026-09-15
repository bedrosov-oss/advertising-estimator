from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import local_store
from price_monitor import PriceMonitor
from supplier_prices import SupplierPrices
from test_supplier_prices import configuration, CSV

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=local_store.LocalStore(self.temp.name);self.timestamp=1700000000
        self.fetch=Mock(return_value={'content':CSV.encode(),'url':'https://supplier.example/p.csv','retrieved_at':'2026-09-14T10:00:00+00:00'})
        self.prices=SupplierPrices(self.store,Path(self.temp.name)/'docs',fetcher=self.fetch)
        self.monitor=PriceMonitor(self.prices,clock=lambda:self.timestamp);self.addCleanup(self.monitor.stop)
        self.source=self.store.save('price_source','Тест',configuration(source_date='2025-01-01'))

    def test_schedule_persists_and_never_applies_without_review(self):
        self.monitor.configure({'source_id':self.source['id'],'hours':1,'enabled':True})
        self.monitor.tick();self.fetch.assert_not_called()
        self.timestamp+=3601;self.monitor.tick();self.fetch.assert_called_once()
        self.assertEqual(self.store.list('catalog'),[])
        other=PriceMonitor(self.prices,clock=lambda:self.timestamp)
        state=other.status();self.assertEqual(len(state['checks']),1)
        book=other.review(state['checks'][0]['id']);self.fetch.assert_called_once()
        self.assertEqual(book['source']['source_date'],'')
        preview=self.prices.preview(book['run_id'],book['source'])
        self.prices.apply(preview['preview_id'],[preview['items'][0]['key']],True)
        self.assertEqual(len(self.store.list('catalog')),1)
        self.monitor.tick();self.fetch.assert_called_once()

    def test_error_record_and_recovery(self):
        self.fetch.side_effect=ValueError('HTTP 502')
        result=self.monitor.check(self.source['id']);self.assertEqual(result['status'],'error')
        self.assertIn('502',self.monitor.status()['checks'][0]['summary']);self.assertFalse(self.monitor.active)
        self.fetch.side_effect=None
        self.assertEqual(self.monitor.check(self.source['id'])['status'],'review')

    def test_disabled_and_local_source_not_scheduled(self):
        self.monitor.configure({'source_id':self.source['id'],'enabled':False,'hours':1})
        self.timestamp+=99999;self.monitor.tick();self.fetch.assert_not_called()
        source=self.store.save('price_source','Файл',configuration(url=''))
        with self.assertRaises(ValueError):self.monitor.configure({'source_id':source['id'],'enabled':True})
