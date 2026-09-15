"""Reject a release with stale or mismatched user instructions. No network or writes."""
from pathlib import Path
import hashlib
import json
import re
from datetime import date

ROOT=Path(__file__).resolve().parents[1]

def version(root=ROOT):
    return re.search(r'^VERSION\s*=\s*[\'\"]([^\'\"]+)', (root/'server.py').read_text(encoding='utf-8'), re.M).group(1)

def controlled_files(root=ROOT):
    patterns=('*.py','*.command','*.bat','requirements*.txt','web/*.html','web/*.js','web/*.css','scripts/*.py','scripts/*.sh','scripts/*.applescript')
    return sorted({p.relative_to(root).as_posix() for pattern in patterns for p in root.glob(pattern) if p.is_file()})

def source_hashes(root=ROOT):
    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in controlled_files(root)}

def content_hash(guide):
    value={k:v for k,v in guide.items() if k!='review'}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def markdown(guide):
    text=f'# Инструкция сметчика {guide["version"]}\n\nОбновлено: {guide["updated"]}. В программе доступны поиск по темам и кнопки перехода к нужным действиям.\n\n'
    for article in guide['articles']:
        text+=f'## {article["title"]}\n\n{article["lead"]}\n\n'
        for index,step in enumerate(article['steps'],1):
            text+=f'{index}. **{step["title"]}.** {step["text"]}'
            if step.get('action'):text+=f' В программе: «{step["action"]["label"]}».'
            text+='\n\n'
        if article['tips']:text+='Полезно знать:\n\n'+''.join('- '+tip+'\n' for tip in article['tips'])+'\n'
    return text

def check_release(root=ROOT):
    guide=json.loads((root/'web/guide.json').read_text(encoding='utf-8'));errors=[];app=version(root)
    if guide.get('version')!=app:errors.append('Версия инструкции не совпадает с программой.')
    try:date.fromisoformat(guide['updated'])
    except (KeyError,TypeError,ValueError):errors.append('Укажите дату сверки инструкции в формате YYYY-MM-DD.')
    review=guide.get('review',{})
    if review.get('version')!=app or not str(review.get('note','')).strip():errors.append('Нет отметки содержательной сверки инструкции для этого выпуска.')
    if review.get('content_sha256')!=content_hash(guide):errors.append('Текст инструкции изменён после сверки.')
    current=source_hashes(root)
    if review.get('source_sha256')!=current:
        old=review.get('source_sha256',{});changed=[p for p in sorted(set(current)|set(old)) if current.get(p)!=old.get(p)]
        errors.append('После сверки изменились файлы: '+', '.join(changed[:8])+('.' if len(changed)<=8 else ' и другие.'))
    required={'start','navigation','estimate','pricing','customer','catalog','prices','schedule','production','geometry','documents','orders','stock','comparison','actual','company','connections','sources','assistant','backup','trouble','release','online','auto-supply','delivery'}
    articles=guide.get('articles',[]);ids=[a.get('id') for a in articles]
    if len(ids)!=len(set(ids)) or not required.issubset(ids):errors.append('В инструкции пропущены обязательные темы или повторяются идентификаторы.')
    for article in articles:
        if not article.get('title') or not article.get('lead') or not article.get('steps') or any(not s.get('title') or not s.get('text') for s in article.get('steps',[])):
            errors.append('Неполная тема инструкции: '+str(article.get('id')))
    release=next((a for a in articles if a.get('id')=='release'),{})
    if app not in release.get('title',''):errors.append('Тема «Что нового» не обновлена для версии '+app)
    for name in ('supplier_fetch.py','scripts/package_app.py'):
        if app not in (root/name).read_text(encoding='utf-8'):errors.append('Не обновлён номер версии: '+name)
    offline=root/'docs/USER_GUIDE.md'
    if not offline.exists() or offline.read_text(encoding='utf-8')!=markdown(guide):errors.append('Скачиваемая инструкция USER_GUIDE.md не соответствует встроенной.')
    if errors:raise ValueError('\n'.join(errors)+'\nОбновите темы, затем выполните python scripts/review_guide.py --note "Что проверено и изменено".')
    return app,len(articles)

if __name__=='__main__':
    try:
        app,count=check_release();print(f'Инструкция {app}: {count} тем, текст и исходники сверены.')
    except (ValueError,OSError,KeyError) as error:raise SystemExit(str(error))
