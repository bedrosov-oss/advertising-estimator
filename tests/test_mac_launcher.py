"""Read-only launcher integration tests; no macOS build or installation is performed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(Path('/bin/bash').exists(), 'POSIX launcher tests need /bin/bash')
class MacLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='mac-estimator-launcher-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.app = self.base / 'Сметчик рекламы с пробелами'
        self.app.mkdir()
        for name in ('START_MAC.command', 'BUILD_MAC.command', 'scripts/mac-python.sh',
                     'scripts/mac-install-python.sh', 'scripts/install-python.applescript',
                     'scripts/python-packages.tsv'):
            target = self.app / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        for name in ('server.py', 'engine.py', 'reporting.py', 'web/index.html', 'web/app.js',
                     'web/style.css', 'web/workspace.js', 'web/customer.js', 'web/price_updates.js', 'web/price_updates.css', 'local_store.py', 'production.py', 'file_formats.py', 'draft_assistant.py', 'fns_registry.py', 'supplier_fetch.py', 'supplier_prices.py','price_monitor.py','mixed_layout.py','vector_geometry.py','advanced_documents.py','secret_store.py','stock.py','full_backup.py','catalog_match.py', 'supply_hub.py', 'shipping_quotes.py', 'runtime_setup.py', 'data/prices.json', 'data/sources.json',
                     'data/conditions.json', 'data/websites.json', 'examples/demo.json'):
            target = self.app / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('import sys; print(sys.executable)' if name=='runtime_setup.py' else '', encoding='utf-8')
        self.bin = self.base / 'Тестовый Python с пробелами'
        self.bin.mkdir()
        self.log = self.base / 'arguments.json'
        self.env = os.environ.copy()
        self.env['PATH'] = str(self.bin)
        self.env['ESTIMATOR_TEST_LOG'] = str(self.log)

    def fake_python(self, name='python3', supported=True, exit_code=0):
        path = self.bin / name
        # The real test interpreter only records argv; it never runs server.py.
        path.write_text(
            '#!/bin/bash\n'
            'if [ "$1" = -B ] && [ "$2" = -c ]; then\n'
            '  case "$3" in *sys.platform*) exit 1 ;; esac\n'
            f'  exit {0 if supported else 1}\n'
            'fi\n'
            f'exec {self.shell_quote(sys.executable)} -B -c '
            + self.shell_quote('import json, os, pathlib, sys; '
                               'pathlib.Path(os.environ["ESTIMATOR_TEST_LOG"]).write_text('
                               'json.dumps({"args": sys.argv[1:], "cwd": os.getcwd()}), encoding="utf-8"); '
                               f'sys.exit({exit_code})')
            + ' "$@"\n', encoding='utf-8')
        path.chmod(0o755)
        return path

    @staticmethod
    def shell_quote(value):
        return "'" + str(value).replace("'", "'\\''") + "'"

    def launch(self, *args):
        return subprocess.run(['/bin/bash', str(self.app / 'START_MAC.command'), *args],
                              cwd=self.base, env=self.env, capture_output=True, text=True)

    def find_python(self, *candidates):
        return subprocess.run(['/bin/bash', '-c',
            '. "$1"; shift; estimator_find_python "$@"', 'test',
            str(self.app / 'scripts/mac-python.sh'), *map(str, candidates)],
            cwd=self.base, env=self.env, capture_output=True, text=True)

    def test_launch_from_space_and_cyrillic_directory_forwards_exact_arguments(self):
        self.fake_python()
        args = ('--no-browser', '--port', '53123', 'literal $(do-not-execute)', 'word with spaces')
        result = self.launch(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = json.loads(self.log.read_text(encoding='utf-8'))
        self.assertEqual(recorded['args'], ['-B', str(self.app / 'server.py'), *args])
        self.assertEqual(recorded['cwd'], str(self.app))

    def test_preserves_application_exit_code(self):
        self.fake_python(exit_code=27)
        self.assertEqual(self.launch().returncode, 27)

    def test_existing_python_does_not_even_source_installer(self):
        self.fake_python()
        (self.app / 'scripts/mac-install-python.sh').write_text(
            "printf 'INSTALLER_MUST_NOT_RUN' >&2; exit 91\n", encoding='utf-8')
        result = self.launch('--no-browser')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('INSTALLER_MUST_NOT_RUN', result.stderr)
        self.assertTrue(self.log.exists())

    def test_missing_python_installs_then_resumes_with_exact_arguments(self):
        installed = self.fake_python(name='newly-installed-python')
        # Replace helpers only in this isolated test fixture. No OS installer runs.
        (self.app / 'scripts/mac-python.sh').write_text(
            'estimator_find_python() { return 1; }\n', encoding='utf-8')
        (self.app / 'scripts/mac-install-python.sh').write_text(
            'estimator_install_python() {\n'
            '  [ "$1" = ' + self.shell_quote(self.app) + ' ] || return 94\n'
            '  printf "%s\\n" ' + self.shell_quote(installed) + '\n'
            '}\n', encoding='utf-8')
        args = ('--no-browser', '--port', '53124', 'literal $(do-not-execute)')
        result = self.launch(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = json.loads(self.log.read_text(encoding='utf-8'))
        self.assertEqual(recorded['args'], ['-B', str(self.app / 'server.py'), *args])

    def test_cancelled_installation_never_starts_server(self):
        (self.app / 'scripts/mac-python.sh').write_text(
            'estimator_find_python() { return 1; }\n', encoding='utf-8')
        (self.app / 'scripts/mac-install-python.sh').write_text(
            'estimator_install_python() { printf "Отменено\\n" >&2; return 1; }\n',
            encoding='utf-8')
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Автоматическая установка не завершена', result.stderr)
        self.assertFalse(self.log.exists())

    def test_real_python_opens_application_help_without_writing_bytecode(self):
        resolved = Path(sys.executable).resolve()
        if str(resolved).startswith(('/usr/bin/', '/Library/Developer/')):
            self.skipTest('The system interpreter is deliberately excluded by the launcher')
        (self.bin / 'python3').symlink_to(sys.executable)
        for filename in ('server.py', 'engine.py', 'reporting.py', 'local_store.py', 'fns_registry.py'):
            shutil.copy2(ROOT / filename, self.app / filename)
        result = self.launch('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--no-browser', result.stdout)
        self.assertIn('--port', result.stdout)
        self.assertFalse((self.app / '__pycache__').exists())

    def test_rejects_old_python_and_tries_next_version(self):
        old = self.fake_python(name='python3', supported=False)
        current = self.fake_python(name='python3.12')
        result = self.find_python(old, current)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(current))

    def test_no_supported_candidate_returns_failure_without_running_server(self):
        old = self.fake_python(supported=False)
        result = self.find_python(self.bin / 'does-not-exist', old)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertFalse(self.log.exists())

    def test_skips_apple_developer_python_even_through_symlink(self):
        # /usr/bin/python3 is not invoked. An actual system version is irrelevant.
        alias = self.bin / 'python3'
        alias.symlink_to('/usr/bin/python3')
        result = self.find_python(alias)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')

    def test_resolves_supported_python_symlink_in_directory_with_spaces(self):
        original = self.fake_python(name='python3.12')
        alias = self.bin / 'python3'
        alias.symlink_to(original.name)
        result = self.find_python(alias)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(alias))

    def test_missing_resource_reports_filename_before_python_selection(self):
        (self.app / 'data/prices.json').unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        self.assertIn('data/prices.json', result.stderr)
        self.assertIn('README_RU.txt', result.stderr)
        self.assertFalse(self.log.exists())

    def test_missing_helper_reports_filename(self):
        (self.app / 'scripts/mac-python.sh').unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        self.assertIn('scripts/mac-python.sh', result.stderr)

    @unittest.skipIf(sys.platform == 'darwin', 'Non-macOS rejection only')
    def test_build_refuses_non_macos_before_installing_anything(self):
        result = subprocess.run(['/bin/bash', str(self.app / 'BUILD_MAC.command')],
            cwd=self.base, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('только на macOS', result.stderr)
        self.assertFalse((self.app / '.build-venv-mac').exists())

    def test_command_files_have_lf_and_executable_permission(self):
        for name in ('START_MAC.command', 'BUILD_MAC.command', 'scripts/mac-python.sh',
                     'scripts/mac-install-python.sh'):
            with self.subTest(name=name):
                path = ROOT / name
                self.assertNotIn(b'\r', path.read_bytes())
                self.assertTrue(os.access(path, os.X_OK))
                result = subprocess.run(['/bin/bash', '-n', str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
