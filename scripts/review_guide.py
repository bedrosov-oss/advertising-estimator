"""Record a completed human/editor review, then generate the offline manual.

This does not write new instructional content. Edit web/guide.json first.
"""
import argparse
from datetime import date
import json
from check_release import ROOT,version,source_hashes,content_hash,markdown,check_release

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--note',required=True,help='What was checked and updated in the actual instructions')
    parser.add_argument('--date',default=date.today().isoformat())
    args=parser.parse_args();date.fromisoformat(args.date)
    if len(args.note.strip())<12:parser.error('Опишите содержательную сверку, а не только смену номера.')
    path=ROOT/'web/guide.json';guide=json.loads(path.read_text(encoding='utf-8'));app=version()
    if guide.get('version')!=app:parser.error('Сначала обновите version и темы в web/guide.json.')
    guide['updated']=args.date
    guide['review']={'version':app,'note':args.note.strip(),'content_sha256':content_hash(guide),'source_sha256':source_hashes()}
    path.write_text(json.dumps(guide,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    (ROOT/'docs/USER_GUIDE.md').write_text(markdown(guide), encoding='utf-8')
    checked,count=check_release();print(f'Сверка инструкции {checked} сохранена, тем: {count}.')
