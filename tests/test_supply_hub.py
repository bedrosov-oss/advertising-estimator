import json
from decimal import Decimal
import tempfile
import unittest
from unittest.mock import Mock,patch
from local_store import LocalStore
from supply_hub import SupplyHub,feed,DAY
import shipping_quotes

ITEM={'name':'Белый ПВХ 3 мм','article':'PVC3','material_key':'pvc-white-3','unit':'лист','price':'1000','currency':'RUB','tax_basis':'vat_included','available':True,'available_quantity':'20','minimum_quantity':'1','package_step':'1'}
QUERY={'material_key':'pvc-white-3','unit':'лист','quantity':'3','tax_basis':'vat_included'}
SHIP={'from_address':'Москва, улица Тестовая, 1','to_address':'Тверь, улица Тестовая, 2','weight_g':'1000','length_cm':'30','width_cm':'20','height_cm':'10','assessed_cost':'1000','providers':['cdek'],'pickup_type':1}

class SupplyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.store=LocalStore(self.temp.name);self.now=1000000
        self.fetch=Mock(return_value={'content':json.dumps([ITEM]).encode()})
        self.hub=SupplyHub(self.store,fetcher=self.fetch,clock=lambda:self.now)
    def source(self,name='Поставщик',item=None):
        id=self.hub.save({'kind':'supplier','name':name,'enabled':True,'config':{'url':'https://supplier.example/feed.json','format':'json'}})['id']
        if item is not None:self.hub.refresh(id,raw=json.dumps([item]).encode())
        return id
    def test_daily_persists_across_restarts_and_does_not_change_saved_orders(self):
        saved=self.store.save('order','Прежняя смета',{'rows':[]});id=self.source()
        self.hub.tick();self.assertEqual(self.fetch.call_count,1)
        other=SupplyHub(self.store,fetcher=self.fetch,clock=lambda:self.now)
        other.tick();self.assertEqual(self.fetch.call_count,1)
        self.now+=DAY;other.tick();self.assertEqual(self.fetch.call_count,2)
        self.assertEqual(self.store.get('order',saved['id']),saved)
        self.assertEqual(other.select(QUERY)['selected']['article'],'PVC3')
    def test_selects_lowest_purchase_total_with_packaging_and_stock(self):
        self.source('Дешёвая единица',{**ITEM,'price':'800','package_step':'5'})
        self.source('Дешевле заказ',{**ITEM,'price':'1100'})
        self.source('Недостаточный остаток',{**ITEM,'price':'1','available_quantity':'2'})
        result=self.hub.select(QUERY)
        self.assertEqual(result['selected']['supplier'],'Дешевле заказ')
        self.assertEqual(Decimal(result['selected']['total']),Decimal('3300'))
        self.assertEqual(len(result['alternatives']),2)
    def test_unknown_availability_currency_duplicates_and_bad_units_not_selected(self):
        for change in ({'available':None},{'currency':'USD'},{'package_step':'0'},{'price':'от 100'}):
            with self.assertRaises(ValueError):feed(json.dumps([{**ITEM,**change}]).encode(),'json')
        with self.assertRaises(ValueError):feed(json.dumps([ITEM,ITEM]).encode(),'json')
        self.source(item={**ITEM,'unit':'м²'})
        self.assertIsNone(self.hub.select(QUERY)['selected'])
    def test_failure_and_staleness_exclude_old_offer_without_erasing_it(self):
        id=self.source(item=ITEM);self.assertIsNotNone(self.hub.select(QUERY)['selected'])
        self.now+=DAY+1;self.assertIsNone(self.hub.select(QUERY)['selected'])
        self.hub.refresh(id,raw=json.dumps([ITEM]).encode())
        self.fetch.side_effect=ValueError('Недоступен')
        self.hub.refresh(id)
        self.assertIsNone(self.hub.select(QUERY)['selected'])
        self.assertEqual(self.hub.status()['sources'][0]['snapshot'][0]['price'],'1000')
    def test_out_of_stock_wrong_tax_and_key_are_not_substitutes(self):
        for item in ({**ITEM,'available':False},{**ITEM,'tax_basis':'no_vat'},{**ITEM,'material_key':'another-material'}):self.source(item=item)
        self.assertIsNone(self.hub.select(QUERY)['selected'])
    def test_private_url_and_unconfigured_shipping_rejected(self):
        with self.assertRaises(ValueError):self.hub.save({'kind':'supplier','name':'X','config':{'url':'http://127.0.0.1/prices'}})
        with patch.dict('os.environ',{'APISHIP_TOKEN':''}):
            with self.assertRaises(ValueError):shipping_quotes.calculate(SHIP)
    def test_shipping_filters_pickup_and_unknown_fees(self):
        tariff={'tariffId':1,'tariffName':'Тариф','deliveryCost':'300','feesIncluded':False,'insuranceFee':'20','cashServiceFee':'0','pickupTypes':[1],'deliveryTypes':[1]}
        response={'deliveryToDoor':[{'providerKey':'cdek','tariffs':[tariff,{**tariff,'tariffId':2,'deliveryCost':'1','feesIncluded':None},{**tariff,'tariffId':3,'pickupTypes':[2]}]}]}
        result=shipping_quotes.parse_response(response,SHIP)
        self.assertEqual(len(result),1);self.assertEqual(result[0]['price'],'320')
        ship=Mock(return_value={'quotes':result});hub=SupplyHub(self.store,shipping=ship,clock=lambda:self.now)
        id=hub.save({'kind':'delivery','name':'Доставка','config':SHIP})['id'];hub.tick();hub.tick();ship.assert_called_once()
        self.now+=DAY;hub.tick();self.assertEqual(ship.call_count,2)
    def test_interrupted_check_is_retried_after_restart(self):
        id=self.source(item=ITEM)
        with self.hub.connect() as db:db.execute("UPDATE sources SET last_status='checking',next_due=? WHERE id=?",(self.now+DAY,id))
        other=SupplyHub(self.store,fetcher=self.fetch,clock=lambda:self.now)
        other.tick();self.fetch.assert_called_once()
        self.assertTrue(other.status()['sources'][0]['fresh'])
    def test_full_backup_includes_daily_database(self):
        import full_backup,zipfile
        from io import BytesIO
        self.source(item=ITEM)
        with zipfile.ZipFile(BytesIO(full_backup.create(self.store))) as archive:self.assertIn('supply-hub.sqlite',archive.namelist())
