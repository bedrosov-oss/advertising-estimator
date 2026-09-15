"""A release must fail when documentation loses sync with shipped behaviour."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from check_release import controlled_files,source_hashes,content_hash,markdown,check_release,version

class GuideReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        for name in controlled_files():
            dest=self.root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dest)
        self.guide=json.loads((ROOT/'web/guide.json').read_text(encoding='utf-8'))
        self.app=version(ROOT)
        self.guide['review']={'version':self.app,'note':'Проверены темы и переходы текущего интерфейса','source_sha256':source_hashes(self.root),'content_sha256':content_hash(self.guide)}
        self.write()
    def write(self):
        (self.root/'web/guide.json').write_text(json.dumps(self.guide,ensure_ascii=False), encoding='utf-8')
        (self.root/'docs').mkdir(exist_ok=True)
        (self.root/'docs/USER_GUIDE.md').write_text(markdown(self.guide), encoding='utf-8')
    def test_synced_release_passes(self):self.assertEqual(check_release(self.root),(self.app,len(self.guide['articles'])))
    def test_changed_ui_blocks_build(self):
        p=self.root/'web/ergonomic.js';p.write_text(p.read_text(encoding='utf-8')+'\n// changed behaviour\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'После сверки изменились файлы'):check_release(self.root)
    def test_version_bump_requires_new_guide(self):
        p=self.root/'server.py';p.write_text(p.read_text(encoding='utf-8').replace('VERSION = "'+self.app+'"','VERSION = "999.0.0"'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Версия инструкции не совпадает'):check_release(self.root)
    def test_changed_topic_requires_review(self):
        self.guide['articles'][0]['steps'][0]['text']='Новый сценарий';self.write()
        with self.assertRaisesRegex(ValueError,'Текст инструкции изменён'):check_release(self.root)
    def test_offline_manual_must_match(self):
        (self.root/'docs/USER_GUIDE.md').write_text('Устаревший текст', encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'USER_GUIDE.md'):check_release(self.root)
