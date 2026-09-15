"""Read-only ApiShip calculator. Never creates a delivery order."""
import json
import os
import re
import urllib.error
import urllib.request
from decimal import Decimal
from supplier_prices import numeric_price

ENDPOINT='https://api.apiship.ru/v1/calculator'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def normalize(value):
    if not isinstance(value,dict):raise ValueError('Укажите параметры доставки.')
    result={}
    for field in ('from_address','to_address'):
        text=value.get(field,'')
        if not isinstance(text,str) or not 5<=len(text.strip())<=500:raise ValueError('Укажите полный адрес отправления и получения.')
        result[field]=text.strip()
    for field in ('weight_g','width_cm','height_cm','length_cm'):
        text=numeric_price(value.get(field,''));number=Decimal(text)
        if not 0<number<=100000000:raise ValueError('Вес и габариты должны быть положительными.')
        result[field]=text
    result['assessed_cost']=numeric_price(value.get('assessed_cost','0'))
    result['pickup_type']=value.get('pickup_type',1)
    if type(result['pickup_type']) is not int or result['pickup_type'] not in (1,2):raise ValueError('Выберите забор от двери или со склада.')
    providers=value.get('providers',['cdek'])
    if not isinstance(providers,list) or not 1<=len(providers)<=10 or any(not isinstance(v,str) or not re.fullmatch(r'[a-z0-9_-]{2,40}',v) for v in providers):raise ValueError('Укажите коды перевозчиков ApiShip.')
    result['providers']=sorted(set(providers))
    return result

def parse_response(payload,config):
    if not isinstance(payload,dict):raise ValueError('Непонятный ответ ApiShip.')
    offers=[]
    for group in payload.get('deliveryToDoor',[]):
        if not isinstance(group,dict) or group.get('providerKey') not in config['providers']:continue
        for tariff in group.get('tariffs',[]):
            if not isinstance(tariff,dict):continue
            if config['pickup_type'] not in tariff.get('pickupTypes',[]) or 1 not in tariff.get('deliveryTypes',[]):continue
            try:
                price=Decimal(numeric_price(tariff.get('deliveryCost')))
                included=tariff.get('feesIncluded')
                if included is False:
                    price+=Decimal(numeric_price(tariff.get('insuranceFee')))+Decimal(numeric_price(tariff.get('cashServiceFee')))
                elif included is not True:continue
                if not tariff.get('tariffId'):continue
                offers.append({'provider':group['providerKey'],'tariff_id':str(tariff['tariffId']),
                               'name':str(tariff.get('tariffName','Тариф'))[:300],'price':str(price),'currency':'RUB',
                               'days_min':tariff.get('calendarDaysMin'), 'days_max':tariff.get('calendarDaysMax')})
            except (ValueError,ArithmeticError):continue
    if not offers:raise ValueError('Нет подходящих тарифов с определённой итоговой стоимостью. Проверьте договор, адреса, габариты и сборы.')
    return sorted(offers,key=lambda item:(Decimal(item['price']),item['provider'],item['tariff_id']))

def calculate(value,*,opener=None):
    config=normalize(value);token=os.environ.get('APISHIP_TOKEN','')
    if not token:raise ValueError('Для ежедневной доставки задайте APISHIP_TOKEN в настройках сервера.')
    if len(token)>4096 or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError('Неверный ключ ApiShip в настройках сервера.')
    body={'from':{'countryCode':'RU','addressString':config['from_address']},
          'to':{'countryCode':'RU','addressString':config['to_address']},
          'weight':float(config['weight_g']),'width':float(config['width_cm']),
          'height':float(config['height_cm']),'length':float(config['length_cm']),
          'assessedCost':float(config['assessed_cost']),'codCost':0,'includeFees':True,
          'pickupTypes':[config['pickup_type']],'deliveryTypes':[1],'providerKeys':config['providers'],'timeout':20000}
    request=urllib.request.Request(ENDPOINT,data=json.dumps(body).encode(),headers={'Authorization':token,'Content-Type':'application/json','Accept':'application/json'},method='POST')
    opener=opener or urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request,timeout=30) as response:
            raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('Ответ ApiShip слишком большой.')
        return {'quotes':parse_response(json.loads(raw),config),'config':config}
    except (urllib.error.URLError,TimeoutError,OSError):raise ValueError('ApiShip не ответил или отклонил доступ. Предыдущий тариф не считается свежим.') from None
    except (json.JSONDecodeError,UnicodeError):raise ValueError('Не удалось прочитать ответ ApiShip.') from None
