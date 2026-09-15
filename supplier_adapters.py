"""Independently written public document adapters; no supplier crawler code copied."""
import json
import re
from urllib.parse import urlsplit

PRODUCT_FORMATS={'product','zenon','forda'}


def check_origin(source):
    if source['format'] in ('zenon','forda'):
        host=(urlsplit(source['url']).hostname or '').lower()
        domain={'zenon':'zenonline.ru','forda':'forda.ru'}[source['format']]
        if host!=domain and not host.endswith('.'+domain):
            raise ValueError('Для этого адаптера укажите карточку на '+domain+'.')


def provider_rows(raw,source):
    from supplier_prices import numeric_price, product_rows, rubles
    check_origin(source)
    if source['format']=='forda':
        # Explicit public API URL or exported JSON. Never select the first tier.
        try:data=json.loads(raw)
        except (ValueError,UnicodeError):return product_rows(raw,source['unit'])
        products=data if isinstance(data,list) else [data]
        if len(products)>500:raise ValueError('Слишком много предложений поставщика.')
        rows=[];issues=[]
        for i,item in enumerate(products,1):
            try:
                if not isinstance(item,dict):raise ValueError('Неизвестная структура товара Forda.')
                prices=item.get('prices',[])
                if not isinstance(prices,list) or len(prices)!=1:raise ValueError('Нужно одно предложение цены; ступени и варианты требуют отдельного прайса.')
                price=prices[0]
                if not isinstance(price,dict) or any(price.get(k) for k in ('minQuantity','maxQuantity','quantity','from','discount')):
                    raise ValueError('Цена зависит от объёма или условий.')
                currency=price.get('currency',item.get('currency',''))
                if not rubles(currency):raise ValueError('В ответе поставщика не подтверждена валюта RUB.')
                unit=item.get('unit',source['unit'])
                if not isinstance(unit,str) or not unit:raise ValueError('Укажите единицу цены по карточке поставщика.')
                article=item.get('article',item.get('sku',''))
                if not isinstance(article,str):raise ValueError('Артикул должен быть строкой.')
                name=item.get('name','')
                if not isinstance(name,str) or not name:raise ValueError('Нет наименования товара.')
                rows.append({'number':str(i),'values':[name,article,unit,numeric_price(price.get('price')),str(currency)],'formulas':[]})
            except ValueError as exc:issues.append({'row':str(i),'message':str(exc)})
        return {'sheets':[{'name':'Forda JSON','hidden':False}],'rows':rows,'issues':issues,'warnings':['Проверьте регион склада, комплектацию, единицу и НДС. API поставщика может измениться.']}
    if source['format']=='zenon':
        try:return product_rows(raw,source['unit'])
        except ValueError:pass
        try:from bs4 import BeautifulSoup
        except ImportError:raise ValueError('Для карточек Zenon установите дополнения SETUP_FEATURES.') from None
        soup=BeautifulSoup(raw,'html.parser');name=soup.select_one('h1.js_c1name');product=soup.select_one('#product[data-articul]')
        price_parts=soup.select('span.rub')
        if soup.select_one('span.manager_price') or len(price_parts)!=1:
            raise ValueError('Цена по запросу либо несколько цен Zenon. Используйте согласованный прайс.')
        if not name or not product:raise ValueError('Карточка Zenon изменилась: не найдены название или артикул.')
        # Numeric amounts alone do not establish currency.
        currency=soup.select_one('[itemprop="priceCurrency"]')
        marker=currency.get('content','') if currency else ''
        if not rubles(marker) and not re.search(r'₽|\bруб[.\s]',price_parts[0].parent.get_text(' ',strip=True),re.I):
            raise ValueError('Рядом с ценой Zenon не подтверждены рубли.')
        rub=price_parts[0].get_text('',strip=True);kopecks=soup.select('span.kop')
        if len(kopecks)>1:raise ValueError('Несколько дробных частей цены.')
        if kopecks:
            fraction=kopecks[0].get_text('',strip=True).lstrip('.,')
            if not re.fullmatch(r'\d{2}',fraction):raise ValueError('Неоднозначные копейки в карточке.')
            rub+='.'+fraction
        unit=soup.select_one('div.buy_wrapper-minimum span.nobr')
        unit=unit.get_text(' ',strip=True) if unit else source['unit']
        if not unit:raise ValueError('Укажите единицу цены по карточке Zenon.')
        return {'sheets':[{'name':'Zenon','hidden':False}], 'rows':[{'number':'1','values':[name.get_text(' ',strip=True),product['data-articul'],unit,numeric_price(rub),'RUB'],'formulas':[]}],
                'warnings':['Сверьте филиал, минимальный отпуск и единицу. Наличие страницы не гарантирует актуальность предложения.']}
    raise ValueError('Неизвестный адаптер поставщика.')
