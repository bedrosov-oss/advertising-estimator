"""Portable regression tests plus real cmd.exe tests (skipped outside Windows)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_desktop
import check_release
import desktop
import feature_setup
import windows_launcher

class WindowsCompatibilityTests(unittest.TestCase):
    def test_release_and_demo_use_utf8_under_cp1251_default(self):
        original=Path.open
        def legacy_open(path,mode='r',buffering=-1,encoding=None,errors=None,newline=None):
            if 'b' not in mode and encoding in (None,'locale'):encoding='cp1251'
            return original(path,mode,buffering,encoding,errors,newline)
        with patch.object(Path,'open',legacy_open):
            self.assertEqual(check_release.check_release()[0],desktop.server.VERSION)
            self.assertTrue(desktop.self_test()['calculation_check'])

    def test_windows_renderer_is_modern_and_failure_opens_browser(self):
        view=Mock();view.settings={};view.start.side_effect=RuntimeError('WebView2 missing')
        app=Mock();app.origin='http://127.0.0.1:12345'
        thread=Mock();thread.is_alive.return_value=False
        with patch.dict('sys.modules',{'webview':view}),patch.object(desktop.sys,'platform','win32'),patch('desktop.server.EstimatorServer',return_value=app),patch('desktop.threading.Thread',return_value=thread),patch('desktop.webbrowser.open') as opened:
            self.assertEqual(desktop.main([]),0)
        self.assertEqual(view.start.call_args.kwargs['gui'],'edgechromium')
        opened.assert_called_once_with(app.origin)
        app.shutdown.assert_called_once();app.server_close.assert_called_once()

    def test_windowed_entrypoint_reports_success_without_console(self):
        with tempfile.TemporaryDirectory() as folder:
            report=Path(folder)/'Результат проверки.json'
            code="import sys; sys.stdout=None; sys.stderr=None; import desktop; raise SystemExit(desktop.main(['--self-test-report',sys.argv[1]]))"
            result=subprocess.run([sys.executable,'-c',code,str(report)],cwd=ROOT,capture_output=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(report.read_text(encoding='utf-8'))['ok'])

    def test_failed_self_test_writes_failure_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as folder:
            report=Path(folder)/'result.json'
            with patch('desktop.self_test',side_effect=ValueError('Файл повреждён')):
                self.assertEqual(desktop.main(['--self-test-report',str(report)]),1)
            self.assertFalse(json.loads(report.read_text(encoding='utf-8'))['ok'])

    def test_build_rejects_stale_or_incomplete_self_test(self):
        with tempfile.TemporaryDirectory() as folder:
            report=Path(folder)/'self-test.json';report.write_text('{"ok":true}',encoding='utf-8')
            with patch('build_desktop.subprocess.run'):
                with self.assertRaises(FileNotFoundError):build_desktop.verify_executable(Path('program.exe'),report,'1.0.8')
            def incomplete(*args,**kwargs):report.write_text('{"ok":true,"version":"1.0.8"}',encoding='utf-8')
            with patch('build_desktop.subprocess.run',side_effect=incomplete):
                with self.assertRaises(ValueError):build_desktop.verify_executable(Path('program.exe'),report,'1.0.8')

    def test_feature_runtime_missing_or_bad_marker_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder);python=target/'python.exe';python.write_bytes(b'fixture')
            with patch('feature_setup.runtime',return_value=(target,python)):
                self.assertIsNone(feature_setup.ready_python())
                (target/'estimator-ready').write_bytes(b'\xff')
                self.assertIsNone(feature_setup.ready_python())
                (target/'estimator-ready').write_bytes((ROOT/'requirements-features.txt').read_bytes())
                self.assertEqual(feature_setup.ready_python(),python)

    def test_failed_reinstall_invalidates_old_ready_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder);python=target/'python.exe';marker=target/'estimator-ready';marker.write_text('old',encoding='utf-8')
            with patch('feature_setup.runtime',return_value=(target,python)),patch('feature_setup.venv.EnvBuilder') as builder,patch('feature_setup.subprocess.run',side_effect=subprocess.CalledProcessError(1,'pip')),patch.object(sys,'argv',['feature_setup.py']):
                with self.assertRaises(subprocess.CalledProcessError):feature_setup.main()
            self.assertFalse(marker.exists())

    def test_windows_launcher_preserves_unicode_arguments_and_exit_code(self):
        python=Path('C:/Пользователь с пробелами/venv/Scripts/python.exe')
        argv=['start','--self-test-report','Заказ (июнь) & май!.json']
        with patch('windows_launcher.feature_setup.ready_python',return_value=python),patch('windows_launcher.subprocess.run',return_value=Mock(returncode=27)) as run:
            self.assertEqual(windows_launcher.main(argv),27)
        self.assertEqual(run.call_args.args[0],[str(python),'-X','utf8','-B',str(ROOT/'desktop.py'),*argv[1:]])
        self.assertNotIn('shell',run.call_args.kwargs)

    def test_diagnostic_reports_real_host_and_does_not_claim_gui(self):
        import windows_check
        with tempfile.TemporaryDirectory(prefix='Сметчик тест ') as folder,patch.dict(os.environ,{'ESTIMATOR_DATA_DIR':folder}):
            report=windows_check.check()
            self.assertTrue(report['ok'],report)
            self.assertEqual(report['platform'],sys.platform)
            self.assertFalse(report['gui_checked'])
            self.assertEqual(list(Path(folder).iterdir()),[])

@unittest.skipUnless(sys.platform=='win32','Requires real Windows cmd.exe')
class WindowsBatchTests(unittest.TestCase):
    def test_start_from_cyrillic_path_and_preserve_exit_code(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'Сметчик рекламы (тест)';root.mkdir()
            shutil.copy2(ROOT/'START_WINDOWS.bat',root)
            shutil.copy2(ROOT/'windows_launcher.py',root)
            (root/'feature_setup.py').write_text('def ready_python(): return None\n',encoding='utf-8')
            (root/'desktop.py').write_text('import json,sys\nfrom pathlib import Path\nPath("args.json").write_text(json.dumps(sys.argv[1:],ensure_ascii=False),encoding="utf-8")\nraise SystemExit(27)\n',encoding='utf-8')
            result=subprocess.run(['cmd.exe','/d','/c','START_WINDOWS.bat','Заказ (июнь) & май!.json'],cwd=root,env={**os.environ,'ESTIMATOR_NO_PAUSE':'1'},capture_output=True,timeout=30)
            self.assertEqual(result.returncode,27,result.stderr)
            self.assertEqual(json.loads((root/'args.json').read_text(encoding='utf-8')),['Заказ (июнь) & май!.json'])
