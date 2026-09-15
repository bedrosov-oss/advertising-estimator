"""Explicit recipe calculations; uncertain prices and engineering norms stay blank."""
from decimal import Decimal, ROUND_CEILING
import uuid
import engine
from local_store import normalize_project

TEMPLATES = {'banner':'Баннер', 'sign':'Таблички ПВХ', 'lightbox':'Световой короб',
             'letters':'Объёмные буквы', 'cards':'Визитки'}


def row(name, unit, quantity='', category='material', price='', note=''):
    return {'id':uuid.uuid4().hex, 'name':name, 'unit':unit, 'quantity':str(quantity),
            'category':category, 'price':str(price), 'note':note, 'source':'',
            'price_date':'', 'source_status':'manual', 'confirmed':False,
            'increment':'', 'minimum_charge':'0'}


def recipe(body):
    kind = body.get('kind')
    if kind not in TEMPLATES:
        raise ValueError('Выберите тип изделия.')
    qty = engine._number(body.get('quantity', ''), 'Количество изделий', positive=True, maximum='1000000')
    if qty != qty.to_integral_value():
        raise ValueError('Количество готовых изделий должно быть целым.')
    loss = engine._number(body.get('waste_percent','0'), 'Запас материала', maximum='100')
    width = engine._number(body.get('width_mm',''), 'Ширина', optional=True, positive=True, maximum='1000000')
    height = engine._number(body.get('height_mm',''), 'Высота', optional=True, positive=True, maximum='1000000')
    if kind != 'letters' and (width is None or height is None):
        raise ValueError('Для выбранного изделия укажите ширину и высоту в миллиметрах.')
    area = width*height*qty/Decimal(1000000) if width is not None and height is not None else None
    perimeter = (width+height)*2*qty/Decimal(1000) if area is not None else None
    fmt = lambda x: '' if x is None else engine._plain(x.quantize(Decimal('.000001')))
    material_area = area*(1+loss/100) if area is not None else None
    notes = ['Черновик по шаблону. Цены, технологические нормы и состав нужно подтвердить.']
    rows = []
    if kind == 'banner':
        rows = [row('Баннерный материал', 'м²', fmt(material_area)),
                row('Печать баннера без материала', 'м²', fmt(area), 'work'),
                row('Обработка края', 'пог. м', fmt(perimeter), 'work'), row('Люверсы', 'шт.', '', 'work')]
    elif kind == 'sign':
        sw, sh = body.get('sheet_width_mm',''), body.get('sheet_height_mm','')
        sheets = ''
        if sw or sh:
            g = engine.geometry({'width_mm':fmt(width),'height_mm':fmt(height),'quantity':fmt(qty),
                'sheet_width_mm':sw,'sheet_height_mm':sh,'edge_mm':body.get('edge_mm','0'),
                'gap_mm':body.get('gap_mm','0'),'bleed_mm':'0','waste_percent':'0','allow_rotate':True})
            if g['sheets'] is None:
                raise ValueError('Изделие не размещается на указанном листе.')
            sheets = str(g['sheets'])
            notes.append(g['layout_label'])
        rows = [row('ПВХ: закупаемые листы', 'лист', sheets), row('Печать без ПВХ', 'м²', fmt(area), 'work'),
                row('Резка по периметру', 'пог. м', fmt(perimeter), 'work')]
        notes.append('Количество листов рассчитано только при указанных размерах листа; запас печати не добавлен к листам.')
    elif kind == 'lightbox':
        rows = [row('Лицевая панель', 'м²', fmt(material_area)),row('Профиль / каркас', 'пог. м', fmt(perimeter)),
                row('Задняя панель', 'м²', fmt(material_area)),row('Светодиодные модули', 'шт.'),
                row('Блоки питания', 'шт.'),row('Электрические комплектующие', 'комплект'),
                row('Сборка короба', 'шт.',fmt(qty),'work')]
        notes.append('Рама, электрика, число модулей и блоков питания определяются по проекту специалиста.')
    elif kind == 'letters':
        rows = [row('Лицевой материал букв', 'м²'),row('Бортовой материал', 'пог. м'),
                row('Задники букв', 'м²'),row('Светодиоды и питание', 'комплект'),
                row('Изготовление и сборка букв', 'комплект',fmt(qty),'work')]
        notes.append('Количество означает число готовых комплектов; расход определяется по векторным контурам и проекту.')
    else:
        sheets = ''
        if body.get('sheet_width_mm') and body.get('sheet_height_mm'):
            g=engine.geometry({'width_mm':fmt(width),'height_mm':fmt(height),'quantity':fmt(qty),
                'sheet_width_mm':body['sheet_width_mm'],'sheet_height_mm':body['sheet_height_mm'],
                'edge_mm':body.get('edge_mm','0'),'gap_mm':body.get('gap_mm','0'),
                'bleed_mm':body.get('bleed_mm','0'),'waste_percent':'0','allow_rotate':True})
            if g['sheets'] is None:
                raise ValueError('Визитка не размещается на печатном листе.')
            sheets = str((Decimal(g['sheets'])*(1+loss/100)).to_integral_value(rounding=ROUND_CEILING))
            notes.append(g['layout_label'])
        sides = body.get('sides','1')
        if str(sides) not in {'1','2'}:
            raise ValueError('Укажите одну или две стороны печати.')
        rows = [row('Бумага для визиток', 'лист',sheets),
                row('Печать без бумаги', 'листопрогон',str(int(sheets)*int(sides)) if sheets else '', 'work'),
                row('Резка и упаковка тиража', 'заказ','1','work')]
        notes.append('Регулярный прямоугольный раскрой; захваты, вылеты и зазоры задаются отдельно. Цена листопрогона требует подтверждения.')
    if body.get('laminate') is True and area is not None:
        rows.append(row('Ламинация','м²',fmt(area),'work'))
    if body.get('delivery') is True:
        rows.append(row('Доставка','рейс','','delivery'))
    if body.get('installation') is True:
        rows.append(row('Монтаж','час','','work'))
    for item in rows:
        item['note'] = 'Количество из шаблона; проверьте технологию. Материал и печать указаны раздельно, проверьте состав выбранной расценки.'
    return normalize_project({'project':{'title':TEMPLATES[kind], 'quantity':fmt(qty),'notes':'\n'.join(notes)},'rows':rows})


def operation(body):
    name = engine._text(body.get('name',''), 'Операция',300)
    if not name:
        raise ValueError('Назовите производственную операцию.')
    count = engine._number(body.get('quantity',''), 'Объём операции', positive=True)
    setup = engine._number(body.get('setup_minutes',''), 'Подготовка, минут')
    each = engine._number(body.get('minutes_per_unit',''), 'Минут на единицу')
    hourly = engine._number(body.get('hourly_rate',''), 'Ставка часа', optional=True)
    hours = (setup+count*each)/Decimal(60)
    if hours <= 0:
        raise ValueError('Время операции должно быть больше нуля.')
    hours = hours.quantize(Decimal('.000001'))
    note = f'Подготовка {setup} мин + {count} ед. × {each} мин; часы округлены до 6 знаков. Уточните, включает ли ставка труд и оборудование.'
    item = row(name,'час',engine._plain(hours),'work','' if hourly is None else engine._plain(hourly),note)
    return {'row':item,'minutes':engine._plain(setup+count*each),'hours':engine._plain(hours)}


def compare_offers(body):
    qty = engine._number(body.get('quantity',''), 'Потребность',positive=True)
    unit = engine._text(body.get('unit',''), 'Единица',100)
    offers = body.get('offers',[])
    if not isinstance(offers,list) or len(offers)>100:
        raise ValueError('Допустимо до 100 предложений.')
    from local_store import normalize_entity
    results=[]
    for raw in offers:
        item=normalize_entity('offer',raw)
        price=engine._number(item['price'],'Цена',optional=True)
        shipping=engine._number(item['delivery'],'Доставка',optional=True)
        increment=engine._number(item['increment'],'Кратность',optional=True,positive=True)
        minimum=engine._number(item['minimum'],'Минимум заказа',optional=True)
        billed=(qty/increment).to_integral_value(rounding=ROUND_CEILING)*increment if increment else qty
        known=billed*price if price is not None else None
        if known is not None and minimum is not None:
            known=max(known,minimum)
        total=known+shipping if known is not None and shipping is not None and minimum is not None else None
        gaps=[]
        if price is None:gaps.append('Цена')
        if shipping is None:gaps.append('Доставка')
        if minimum is None:gaps.append('Минимальная оплата: укажите 0, если её нет')
        if not item['comparable']:gaps.append('Характеристики, налоги и состав не подтверждены как сопоставимые')
        if not unit or item['unit'].casefold()!=unit.casefold():gaps.append('Единицы различаются')
        if not item['source'] or not item['date']:gaps.append('Источник и дата')
        results.append({**item,'billed_quantity':engine._plain(billed), 'total':None if total is None else engine._money(total),
                        'eligible':not gaps,'missing':gaps})
    eligible=[Decimal(x['total']) for x in results if x['eligible']]
    best=min(eligible) if eligible else None
    for item in results:
        item['best']=item['eligible'] and Decimal(item['total'])==best
    return {'offers':results,'best_total':None if best is None else engine._money(best)}


def actuals(project):
    project=normalize_project(project)
    plan=engine.calculate(project)
    entries=[]
    complete=bool(plan['rows'])
    actual_total=Decimal(0)
    for item in plan['rows']:
        fact=project['extensions']['actuals'].get(item['id'],{})
        amount=engine._number(fact.get('amount',''),'Фактическая сумма',optional=True)
        delta=None if amount is None or item['amount'] is None else amount-Decimal(item['amount'])
        complete=complete and amount is not None and item['amount'] is not None
        if amount is not None:actual_total+=amount
        entries.append({'id':item['id'],'name':item['name'],'planned':item['amount'],
                        'actual':None if amount is None else engine._money(amount),
                        'difference':None if delta is None else engine._money(delta),'note':fact.get('note','')})
    extra=engine._number(project['extensions']['extra_actual'],'Расходы вне сметы',optional=True)
    complete=complete and extra is not None
    if extra is not None:actual_total+=extra
    return {'rows':entries,'known_actual':engine._money(actual_total),'planned':plan['totals']['direct'],
            'complete':complete,'difference':engine._money(actual_total-Decimal(plan['totals']['direct'])) if complete else None}
