"""Local vector measurement in a bounded worker. No online CAD conversion."""
from io import BytesIO
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import engine
import file_formats
from production import row


def measure(body):
    kind=body.get('format')
    if kind not in ('svg','dxf'):raise ValueError('Выберите SVG или DXF. DWG/CDR нужно экспортировать в редакторе.')
    raw=file_formats.decode_file(body.get('content'))
    if not raw or len(raw)>8*1024*1024:raise ValueError('Нужен непустой векторный файл до 8 МБ.')
    scale=engine._numeric_text(body.get('unit_mm',''),'Миллиметров в единице',optional=True,positive=True,maximum='1000000')
    layer=engine._text(body.get('layer',''),'Слой DXF',200)
    with tempfile.TemporaryDirectory(prefix='estimator-vector-') as directory:
        path=Path(directory)/('drawing.'+kind);path.write_bytes(raw);output=Path(directory)/'result.json'
        command=([sys.executable,'--vector-worker'] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve())])
        try:result=subprocess.run(command+[str(path),json.dumps({'format':kind,'unit_mm':scale,'layer':layer}),str(output)],capture_output=True,timeout=35)
        except subprocess.TimeoutExpired:raise ValueError('Чертёж слишком сложен: превышено время 35 секунд.') from None
        if result.returncode or not output.is_file() or output.stat().st_size>2*1024*1024:raise ValueError('Не удалось обработать векторный файл.')
        try:value=json.loads(output.read_bytes())
        except (ValueError,UnicodeError):raise ValueError('Не удалось прочитать результат измерения.') from None
        if value.get('error'):raise ValueError(value['error'])
    value['row']=row('Резка по контурам '+kind.upper(),'пог. м',value['length_m'],'work',note=value['note'])
    return value


def choose_scale(declared,supplied):
    manual=float(supplied) if supplied else None
    if manual and declared and not math.isclose(manual,declared,rel_tol=1e-6):
        raise ValueError('Заданный масштаб отличается от единиц файла. Исправьте экспорт или очистите поле ручного масштаба.')
    scale=manual or declared
    if scale is None or not math.isfinite(scale) or scale<=0:raise ValueError('В файле не указан физический масштаб. Укажите, сколько мм содержит одна единица чертежа.')
    return scale


def svg_paths(raw,supplied):
    if b'\x00' in raw:raise ValueError('Сохраните SVG в UTF-8.')
    raw.decode('utf-8-sig')
    if re.search(br'<!DOCTYPE|<!ENTITY',raw,re.I):raise ValueError('SVG с DTD/ENTITY не поддерживается.')
    from svgpathtools import Document
    tree=ET.fromstring(raw)
    if tree.tag.rsplit('}',1)[-1]!='svg':raise ValueError('В файле отсутствует корневой SVG.')
    nodes=list(tree.iter())
    if len(nodes)>10000:raise ValueError('В SVG слишком много объектов.')
    forbidden={'use','symbol','clipPath','mask','image','foreignObject','script','style','text','textPath'}
    for node in nodes:
        tag=node.tag.rsplit('}',1)[-1]
        if tag in forbidden or (tag=='svg' and node is not tree):raise ValueError('SVG содержит '+tag+'. Преобразуйте надписи/ссылки в контуры, удалите маски и изображения.')
        if any(node.get(key) for key in ('mask','clip-path','filter')):raise ValueError('SVG содержит маски/фильтры. Экспортируйте чистые контуры.')
        if node.get('display')=='none' or 'display' in node.get('style','') or node.get('visibility')=='hidden':raise ValueError('SVG содержит скрытые объекты. Удалите их перед расчётом.')
    def physical(text):
        match=re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*(mm|cm|in|pt|px)?\s*',text or '')
        if not match:return None
        return float(match[1])*{'mm':1,'cm':10,'in':25.4,'pt':25.4/72,'px':25.4/96,None:25.4/96}[match[2]]
    box=tree.get('viewBox');declared=None
    if box:
        values=[float(x) for x in re.split(r'[\s,]+',box.strip())]
        if len(values)!=4 or values[2]<=0 or values[3]<=0:raise ValueError('Неверный viewBox SVG.')
        width=physical(tree.get('width'));height=physical(tree.get('height'))
        if width and height:
            sx=width/values[2];sy=height/values[3]
            if not math.isclose(sx,sy,rel_tol=1e-6):raise ValueError('SVG имеет неодинаковый масштаб по осям. Экспортируйте чертёж без растяжения.')
            declared=sx
    else:
        # SVG coordinates use CSS px; explicit physical viewport without viewBox
        # changes the canvas dimensions, not the coordinate scale.
        declared=25.4/96
    scale=choose_scale(declared,supplied)
    document=Document(BytesIO(raw));paths=[]
    for path in document.paths():paths.extend(path.continuous_subpaths())
    if not paths or len(paths)>10000:raise ValueError('Нет измеряемых контуров или их слишком много.')
    contours=[]
    for path in paths:
        length=path.length(error=1e-7)*scale
        if not math.isfinite(length) or length<=0:continue
        closed=path.isclosed()
        area=abs(path.area())*scale*scale if closed else 0
        xmin,xmax,ymin,ymax=path.bbox()
        points=[path.point(i/64)*scale for i in range(65)]
        contours.append((length,area,closed,(xmin*scale,xmax*scale,ymin*scale,ymax*scale),points))
    return contours,scale,[]


def dxf_paths(path,supplied,layer):
    import ezdxf
    from ezdxf.path import make_path
    doc=ezdxf.readfile(path)
    units=int(doc.header.get('$INSUNITS',0))
    scale=choose_scale({1:25.4,2:304.8,4:1,5:10,6:1000,13:.001}.get(units),supplied)
    entities=list(doc.modelspace())
    if len(entities)>10000:raise ValueError('В DXF слишком много объектов.')
    contours=[];ignored=0;vertices=0
    for entity in entities:
        if layer and entity.dxf.layer!=layer:continue
        kind=entity.dxftype()
        if kind in ('TEXT','MTEXT','DIMENSION','POINT'):ignored+=1;continue
        if kind not in ('LINE','ARC','CIRCLE','ELLIPSE','LWPOLYLINE','POLYLINE','SPLINE'):
            raise ValueError('Объект DXF '+kind+' не поддерживается. Разберите блоки и экспортируйте плоские контуры.')
        extrusion=entity.dxf.get('extrusion',(0,0,1))
        if tuple(extrusion)!=(0,0,1):raise ValueError('DXF содержит повёрнутую плоскость: экспортируйте в XY.')
        points=list(make_path(entity).flattening(distance=.01/scale,segments=8));vertices+=len(points)
        if vertices>100000:raise ValueError('Слишком много вершин: упростите DXF.')
        if len(points)<2:continue
        if any(abs(p.z)>1e-8 for p in points):raise ValueError('Нужен плоский DXF с Z=0.')
        xy=[complex(p.x*scale,p.y*scale) for p in points]
        length=sum(abs(b-a) for a,b in zip(xy,xy[1:]))
        closed=abs(xy[0]-xy[-1])<1e-6
        area=abs(sum(a.real*b.imag-b.real*a.imag for a,b in zip(xy,xy[1:])))/2 if closed else 0
        bbox=(min(p.real for p in xy),max(p.real for p in xy),min(p.imag for p in xy),max(p.imag for p in xy))
        contours.append((length,area,closed,bbox,xy))
    return contours,scale,[f'Пропущены аннотации и точки: {ignored}. Дуги DXF аппроксимированы с допуском 0,01 мм.']


def extract(path,options):
    if options['format']=='svg':contours,scale,warnings=svg_paths(Path(path).read_bytes(),options['unit_mm'])
    else:contours,scale,warnings=dxf_paths(path,options['unit_mm'],options['layer'])
    if not contours:raise ValueError('На выбранном слое нет измеряемых контуров.')
    length=sum(c[0] for c in contours);area=sum(c[1] for c in contours)
    if not math.isfinite(length) or not math.isfinite(area) or length>1e10:raise ValueError('Неверный масштаб или слишком большой чертёж.')
    # Detect exactly matching sampled contours, including reversed open paths.
    signatures=set()
    for contour in contours:
        points=tuple((round(p.real,5),round(p.imag,5)) for p in contour[4]);signature=min(points,tuple(reversed(points)))
        if signature in signatures:raise ValueError('Обнаружены дублирующиеся контуры. Удалите дубликаты в редакторе перед расчётом.')
        signatures.add(signature)
    warnings+=['Длина включает открытые и замкнутые контуры по их осевым линиям. Ширина реза, заходы инструмента и скорость станка задаются отдельно.',
               'Площадь показана как сумма площадей замкнутых контуров: отверстия и пересечения не вычитаются. Это справочное число, не расход материала. Проверьте чертёж на дубли с другой разбивкой сегментов.']
    note=f"Масштаб {scale:g} мм/ед.; контуров {len(contours)}; открытых {sum(not c[2] for c in contours)}. "+warnings[0]
    return {'length_m':f'{length/1000:.6f}','closed_area_sum_m2':f'{area/1000000:.6f}',
            'width_mm':round(max(c[3][1] for c in contours)-min(c[3][0] for c in contours),3),
            'height_mm':round(max(c[3][3] for c in contours)-min(c[3][2] for c in contours),3),
            'contours':len(contours),'open_contours':sum(not c[2] for c in contours),'unit_mm':scale,'warnings':warnings,'note':note}


def worker(argv):
    try:value=extract(argv[0],json.loads(argv[1]))
    except ImportError:value={'error':'Установите дополнения DXF/SVG через SETUP_FEATURES.'}
    except ValueError as exc:value={'error':str(exc)}
    except Exception:value={'error':'Не удалось прочитать геометрию. Экспортируйте плоский DXF/SVG с контурами.'}
    if len(argv)>2:Path(argv[2]).write_text(json.dumps(value,ensure_ascii=True),encoding='utf-8')
    else:print(json.dumps(value,ensure_ascii=True))
    return 0

if __name__=='__main__':raise SystemExit(worker(sys.argv[1:]))
