"""Integer geometry with independently checked bounds; heuristic rectangular nesting."""
from decimal import Decimal
from html import escape
import engine
from production import row


def mm(value,label,positive=False):
    number=engine._number(value,label,maximum='100000',positive=positive)
    if number*1000!=(number*1000).to_integral_value():raise ValueError(label+': допустимо до 3 знаков после запятой.')
    return int(number*1000)


def layout(body):
    try:from rectpack import newPacker,MaxRectsBssf,MaxRectsBaf,SORT_AREA
    except ImportError:raise ValueError('Для смешанного раскроя установите дополнения SETUP_FEATURES.') from None
    width=mm(body.get('sheet_width_mm'),'Ширина листа',True);height=mm(body.get('sheet_height_mm'),'Высота листа',True)
    edge=mm(body.get('edge_mm','0'),'Отступ');gap=mm(body.get('gap_mm','0'),'Зазор');bleed=mm(body.get('bleed_mm','0'),'Вылет')
    rotate=engine._boolean(body.get('allow_rotate',False),'Поворот деталей')
    usable_w=width-2*edge;usable_h=height-2*edge
    if min(usable_w,usable_h)<=0:raise ValueError('Отступы больше листа.')
    parts=body.get('parts');items=[];net_area=0
    if not isinstance(parts,list) or not 1<=len(parts)<=100:raise ValueError('Введите от 1 до 100 типов деталей.')
    for part in parts:
        if not isinstance(part,dict):raise ValueError('Неверная деталь.')
        name=engine._text(part.get('name',''),'Название детали',100) or 'Деталь'
        w=mm(part.get('width_mm'),'Ширина детали',True);h=mm(part.get('height_mm'),'Высота детали',True)
        count=part.get('quantity')
        if type(count) is not int or not 1<=count<=200 or len(items)+count>200:raise ValueError('Один раскрой поддерживает до 200 деталей; количество должно быть целым.')
        net_area+=w*h*count
        w+=bleed*2;h+=bleed*2
        fits=(w<=usable_w and h<=usable_h) or (rotate and h<=usable_w and w<=usable_h)
        if not fits:raise ValueError('Деталь «'+name+'» не помещается на лист с выбранными отступами.')
        for _ in range(count):items.append({'name':name,'w':w,'h':h})
    best=None
    for algorithm in (MaxRectsBssf,MaxRectsBaf):
        packer=newPacker(rotation=rotate,pack_algo=algorithm,sort_algo=SORT_AREA)
        for i,item in enumerate(items):packer.add_rect(item['w']+gap,item['h']+gap,rid=i)
        packer.add_bin(usable_w+gap,usable_h+gap,count=len(items));packer.pack()
        result=packer.rect_list()
        if len(result)!=len(items):continue
        if best is None or len(packer)<best[0]:best=(len(packer),result)
    if best is None:raise ValueError('Не удалось разместить все детали.')
    placed=[]
    for sheet,x,y,w,h,identifier in best[1]:
        if min(x,y)<0 or x+w>usable_w+gap or y+h>usable_h+gap:raise ValueError('Раскрой не прошёл проверку границ.')
        if sorted((w-gap,h-gap))!=sorted((items[identifier]['w'],items[identifier]['h'])):raise ValueError('Размер детали изменился при раскрое.')
        for other in placed:
            if other['sheet']==sheet and x<other['x']+other['w'] and other['x']<x+w and y<other['y']+other['h'] and other['y']<y+h:
                raise ValueError('Раскрой не прошёл проверку пересечений.')
        placed.append({'sheet':sheet,'x':x,'y':y,'w':w,'h':h,'id':identifier})
    if len({r['id'] for r in placed})!=len(items):raise ValueError('Повтор детали в раскрое.')
    output=[];diagrams=[]
    for sheet in range(best[0]):
        rects=[]
        for item in placed:
            if item['sheet']!=sheet:continue
            value={'sheet':sheet+1,'id':item['id']+1,'name':items[item['id']]['name'],
                   'x_mm':(item['x']+edge)/1000,'y_mm':(item['y']+edge)/1000,'width_mm':(item['w']-gap)/1000,'height_mm':(item['h']-gap)/1000}
            output.append(value)
            rects.append(f'<rect x="{value["x_mm"]}" y="{value["y_mm"]}" width="{value["width_mm"]}" height="{value["height_mm"]}" fill="#bfdbfe" stroke="#1e40af" stroke-width="1"/><text x="{value["x_mm"]+2}" y="{value["y_mm"]+12}" font-size="10">{value["id"]}</text>')
        diagrams.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width/1000} {height/1000}" width="600" role="img"><title>Лист {sheet+1}</title><rect width="100%" height="100%" fill="white" stroke="#475569"/>'+''.join(rects)+'</svg>')
    utilization=Decimal(net_area)/Decimal(width*height*best[0])*100
    return {'sheets':best[0],'placements':output,'svgs':diagrams,'utilization_percent':engine._plain(utilization.quantize(Decimal('.01'))),
            'warnings':['Эвристическая прямоугольная раскладка: глобальный минимум листов не гарантируется. Проверены границы и отсутствие пересечений с заданным зазором.','Заполнение считается по чистой площади деталей; вылеты и технологический зазор не считаются готовой продукцией.'],
            'row':row('Материал: листы по смешанному раскрою','лист',best[0],note='Смешанный раскрой; размеры листа '+str(width/1000)+' × '+str(height/1000)+' мм. Проверьте направление материала и технологию.')}
