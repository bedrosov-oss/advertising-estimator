"""Similar-name suggestions only; prices and item identities never change here."""
import engine


def suggest(store,body):
    query=engine._text(body.get('query',''),'Название',300);unit=engine._text(body.get('unit',''),'Единица',30)
    if len(query)<3:raise ValueError('Введите не менее трёх символов названия.')
    try:from rapidfuzz.fuzz import WRatio
    except ImportError:raise ValueError('Для похожих названий установите дополнения SETUP_FEATURES.') from None
    items=[]
    for record in store.list('catalog'):
        item=record['data']
        if unit and item['unit'].casefold()!=unit.casefold():continue
        score=WRatio(query.casefold(),item['name'].casefold())
        if score>=65:items.append({'id':record['id'],'name':item['name'],'unit':item['unit'],'article':item['article'],'supplier':item['supplier'],'score':round(score,1)})
    return {'items':sorted(items,key=lambda r:(-r['score'],r['name']))[:8],'notice':'Сходство названий — подсказка. Сверьте артикул, материал, толщину и единицу; автоматического объединения нет.'}
