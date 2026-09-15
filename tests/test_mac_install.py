"""Offline macOS bootstrap checks; no real download or privileged command runs.

Selection tests use the production helper and manifest unchanged. Workflow tests
replace fixed system-command paths only in a temporary COPY of the shell helper.
The shipped launcher has no switches for bypassing verification or OS checks.
AppleScript itself cannot be executed or validated as macOS code on Linux.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/mac-install-python.sh'
MANIFEST = ROOT / 'scripts/python-packages.tsv'
PAYLOAD = b'Offline test package, not an installable Python package.\n'


@unittest.skipUnless(Path('/bin/bash').exists(), 'Bootstrap tests require /bin/bash')
class MacPythonInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='mac-python-bootstrap-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.app = self.base / "Сметчик с пробелами и 'кавычкой'"
        (self.app / 'scripts').mkdir(parents=True)
        self.bin = self.base / 'mock-bin'
        self.bin.mkdir()
        self.private_tmp = self.base / 'private/tmp'
        self.private_tmp.mkdir(parents=True)
        self.log = self.base / 'events.log'
        self.env = os.environ.copy()
        self.env.update({
            'ESTIMATOR_TEST_ROOT': str(self.base),
            'ESTIMATOR_TEST_LOG': str(self.log),
            'ESTIMATOR_TEST_PLATFORM': 'Darwin',
            'ESTIMATOR_TEST_MACOS': '14.0.1',
            'ESTIMATOR_TEST_ARCH': 'arm64',
            'ESTIMATOR_TEST_DOWNLOAD': 'ok',
            'ESTIMATOR_TEST_SIGNATURE': 'ok',
            'ESTIMATOR_TEST_AUTH': 'ok',
            'ESTIMATOR_TEST_PROBE': 'ok',
            'ESTIMATOR_TEST_SIZE': 'ok',
            'ESTIMATOR_TEST_PAYLOAD': PAYLOAD.decode(),
        })

    def run_bash(self, script, *args):
        return subprocess.run(['/bin/bash', '-c', script, 'bootstrap-test', *map(str, args)],
                              cwd=self.base, env=self.env, capture_output=True,
                              text=True, timeout=15)

    def select(self, macos, arch='x86_64', manifest=MANIFEST):
        return self.run_bash('. "$1"; estimator_select_python_package "$2" "$3" "$4"',
                             HELPER, manifest, macos, arch)

    def command(self, name, body):
        target = self.bin / name
        target.write_text('#!/bin/bash\nset -eu\n' + body + '\n', encoding='utf-8')
        target.chmod(0o755)
        return target

    def workflow_fixture(self):
        """All changed command paths and framework paths exist only below tempdir."""
        copied = HELPER.read_text(encoding='utf-8')
        manifest = MANIFEST.read_text(encoding='utf-8')
        framework = str(self.base / 'Python.framework/Versions')
        for original in ('/Library/Frameworks/Python.framework/Versions', '/private/tmp'):
            replacement = framework if 'Frameworks' in original else str(self.private_tmp)
            copied = copied.replace(original, replacement)
            manifest = manifest.replace(original, replacement)
        fixture_hash = hashlib.sha256(PAYLOAD).hexdigest()
        records = [line.split('\t') for line in manifest.splitlines()]
        for fields in records[1:]:
            fields[4] = fixture_hash
        (self.app / 'scripts/python-packages.tsv').write_text(
            '\n'.join('\t'.join(fields) for fields in records) + '\n', encoding='utf-8')
        shutil.copy2(ROOT / 'scripts/install-python.applescript', self.app / 'scripts')
        self.python = Path(framework) / '3.14/bin/python3'
        self.python.parent.mkdir(parents=True)
        self.python_stub = self.base / 'python-stub'
        self.python_stub.write_text(
            '#!/bin/bash\n'
            'printf "%s\\n" python >> "$ESTIMATOR_TEST_LOG"\n'
            'printf "%s\\n" "$@" > "$ESTIMATOR_TEST_ROOT/python-args"\n'
            '[ "$ESTIMATOR_TEST_PROBE" = ok ]\n', encoding='utf-8')
        self.python_stub.chmod(0o755)
        self.env['ESTIMATOR_TEST_PYTHON_PATH'] = str(self.python)
        self.env['ESTIMATOR_TEST_PYTHON_STUB'] = str(self.python_stub)
        commands = {
            '/usr/bin/uname': ('uname', '''
case "$1" in
  -s) printf '%s\\n' "$ESTIMATOR_TEST_PLATFORM" ;;
  -m) printf '%s\\n' "$ESTIMATOR_TEST_ARCH" ;;
  *) exit 89 ;;
esac'''),
            '/usr/bin/sw_vers': ('sw_vers', '''
[ "${SYSTEM_VERSION_COMPAT:-}" = 0 ] || exit 88
[ "$1" = -productVersion ] || exit 89
printf '%s\\n' "$ESTIMATOR_TEST_MACOS"'''),
            '/usr/bin/mktemp': ('mktemp', '''
printf '%s\\n' temp >> "$ESTIMATOR_TEST_LOG"
exec /usr/bin/mktemp "$@"'''),
            '/usr/bin/curl': ('curl', '''
printf '%s\\n' download >> "$ESTIMATOR_TEST_LOG"
printf '%s\\n' "$@" > "$ESTIMATOR_TEST_ROOT/curl-args"
destination=
while [ "$#" -gt 0 ]; do
  if [ "$1" = --output ]; then destination=$2; break; fi
  shift
done
case "$destination" in "$ESTIMATOR_TEST_ROOT"/private/tmp/*/python.pkg) ;; *) exit 89 ;; esac
case "$ESTIMATOR_TEST_DOWNLOAD" in
  failed) printf partial > "$destination"; exit 22 ;;
  empty) : > "$destination" ;;
  changed) printf changed > "$destination" ;;
  ok) printf '%s' "$ESTIMATOR_TEST_PAYLOAD" > "$destination" ;;
  *) exit 89 ;;
esac'''),
            '/usr/bin/wc': ('wc', '''
if [ "$ESTIMATOR_TEST_SIZE" = oversize ]; then
  printf '240000001\\n'
else
  exec /usr/bin/wc "$@"
fi'''),
            '/usr/sbin/pkgutil': ('pkgutil', '''
printf '%s\\n' signature >> "$ESTIMATOR_TEST_LOG"
[ "$1" = --check-signature ] || exit 89
case "$ESTIMATOR_TEST_SIGNATURE" in
  failed) printf 'Status: no signature\\n'; exit 1 ;;
  wrong) printf 'Status: signed by a certificate trusted by macOS\\n  1. Developer ID Installer: Another Publisher (TESTTEAM01)\\n' ;;
  ok) printf 'Status: signed by a certificate trusted by macOS\\n  1. Developer ID Installer: Python Software Foundation (TESTTEAM01)\\n' ;;
  *) exit 89 ;;
esac'''),
            '/usr/bin/osascript': ('osascript', '''
printf '%s\\n' auth >> "$ESTIMATOR_TEST_LOG"
printf '%s\\n' "$@" > "$ESTIMATOR_TEST_ROOT/osascript-args"
[ "$ESTIMATOR_TEST_AUTH" = ok ] || exit 1
if [ "$ESTIMATOR_TEST_PROBE" != missing ]; then
  /bin/cp "$ESTIMATOR_TEST_PYTHON_STUB" "$ESTIMATOR_TEST_PYTHON_PATH"
fi'''),
            '/bin/rm': ('rm', '''
printf '%s\\n' cleanup >> "$ESTIMATOR_TEST_LOG"
[ "$1" = -rf ] && [ "$2" = -- ] && [ "$#" = 3 ] || exit 89
case "$3" in "$ESTIMATOR_TEST_ROOT"/private/tmp/advertising-python.??????) ;; *) exit 89 ;; esac
exec /bin/rm "$@"'''),
        }
        for absolute, (name, body) in commands.items():
            self.assertIn(absolute, copied, f'Mocked production command moved: {absolute}')
            copied = copied.replace(absolute, shlex.quote(str(self.command(name, body))))
        self.helper_copy = self.app / 'scripts/mac-install-python.sh'
        self.helper_copy.write_text(copied, encoding='utf-8')

    def install(self):
        return self.run_bash('. "$1"; estimator_install_python "$2"', self.helper_copy, self.app)

    def events(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def assert_failed_and_clean(self, result, expected_events):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.events(), expected_events, result.stderr)
        self.assertEqual(list(self.private_tmp.iterdir()), [])

    def test_sourcing_production_helper_has_no_actions_or_output(self):
        result = self.run_bash('. "$1"', HELPER)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.assertEqual(self.events(), [])

    def test_latest_verified_package_for_modern_intel_and_apple_silicon(self):
        for macos, arch in [('10.15', 'x86_64'), ('10.15.7', 'x86_64'),
                            ('11.0', 'arm64'), ('26.0', 'arm64'), ('26.0', 'x86_64')]:
            with self.subTest(macos=macos, arch=arch):
                result = self.select(macos, arch)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.split('\t')[0], '3.14.7')

    def test_supported_older_intel_uses_compatible_verified_package(self):
        for macos in ('10.13', '10.13.6', '10.14.6'):
            with self.subTest(macos=macos):
                result = self.select(macos)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.split('\t')[0], '3.13.15')

    def test_rejects_unsupported_or_inconsistent_platform_values(self):
        for macos, arch in [('10.9', 'x86_64'), ('10.12.6', 'x86_64'),
                            ('10.15.7', 'arm64'), ('14.0', 'i386'),
                            ('14.0', 'aarch64'), ('14.0', 'arm64;echo bad'),
                            ('latest', 'x86_64'), ('14.0.0.1', 'x86_64')]:
            with self.subTest(macos=macos, arch=arch):
                result = self.select(macos, arch)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def test_selection_is_numeric_and_independent_of_manifest_order(self):
        lines = MANIFEST.read_text().splitlines()
        reverse = self.base / 'reverse.tsv'
        reverse.write_text('\n'.join([lines[0], *reversed(lines[1:])]) + '\n')
        result = self.select('26.0', manifest=reverse)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split('\t')[0], '3.14.7')
        numeric = self.run_bash(
            '. "$1"; estimator_version_at_least 10.9 10.13; exit "$?"', HELPER)
        self.assertEqual(numeric.returncode, 1)

    def test_malformed_manifest_fails_closed_even_after_valid_record(self):
        original = MANIFEST.read_text().splitlines()
        mutations = {
            'untrusted domain': (3, 'https://other.example/python-3.13.15-macos11.pkg'),
            'http scheme': (3, original[2].split('\t')[3].replace('https:', 'http:')),
            'wrong release in url': (3, original[2].split('\t')[3].replace('3.13.15', '3.13.14')),
            'short hash': (4, 'a' * 63),
            'nonhex hash': (4, 'z' * 64),
            'different destination': (5, '/tmp/python3'),
            'other framework version': (5, '/Library/Frameworks/Python.framework/Versions/3.14/bin/python3'),
            'missing field': (2, ''),
            'invalid architecture': (2, 'all'),
            'invalid minimum': (1, 'any'),
            'invalid python version': (0, '2.7.18'),
        }
        for label, (field, value) in mutations.items():
            with self.subTest(label=label):
                fields = original[2].split('\t')
                fields[field] = value
                path = self.base / 'invalid.tsv'
                path.write_text('\n'.join([original[0], original[1], '\t'.join(fields)]) + '\n')
                result = self.select('26.0', manifest=path)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
        for suffix in ('\textra', '\t'):
            with self.subTest(suffix=repr(suffix)):
                path = self.base / 'extra.tsv'
                path.write_text('\n'.join([original[0], original[1], original[2] + suffix]) + '\n')
                result = self.select('26.0', manifest=path)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def test_missing_and_incorrect_header_manifest_rejected(self):
        invalid = self.base / 'header.tsv'
        invalid.write_text(MANIFEST.read_text().replace('min_macos', 'minimum_os', 1))
        for manifest in (invalid, self.base / 'missing.tsv'):
            with self.subTest(manifest=manifest):
                result = self.select('26.0', manifest=manifest)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    @unittest.skipIf(sys.platform == 'darwin', 'Checks genuine non-macOS rejection')
    def test_real_non_macos_rejected_before_any_installation(self):
        result = self.run_bash('. "$1"; estimator_install_python "$2"', HELPER, self.app)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('только для macOS', result.stderr)
        self.assertEqual(self.events(), [])
        self.assertEqual(list(self.private_tmp.iterdir()), [])

    def test_success_checks_package_before_auth_and_returns_verified_path_only(self):
        self.workflow_fixture()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(self.python) + '\n')
        self.assertEqual(self.events(), ['temp', 'download', 'signature', 'auth', 'python', 'cleanup'])
        self.assertEqual(list(self.private_tmp.iterdir()), [])
        arguments = (self.base / 'osascript-args').read_text().splitlines()
        self.assertEqual(arguments[0], str(self.app / 'scripts/install-python.applescript'))
        self.assertEqual(arguments[2], hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(Path(arguments[1]).name, 'python.pkg')
        python_args = (self.base / 'python-args').read_text().splitlines()
        self.assertEqual(python_args[:3], ['-I', '-B', '-c'])
        self.assertIn('ssl', python_args[3])
        self.assertEqual(python_args[-1], '3.14.7')
        curl_args = (self.base / 'curl-args').read_text().splitlines()
        for flag, value in [('--proto', '=https'), ('--proto-redir', '=https'),
                            ('--max-filesize', '240000000')]:
            self.assertEqual(curl_args[curl_args.index(flag) + 1], value)
        self.assertIn('--tlsv1.2', curl_args)
        self.assertIn('--fail', curl_args)
        self.assertLessEqual(int(curl_args[curl_args.index('--max-time') + 1]), 600)
        self.assertNotIn('--insecure', curl_args)
        self.assertTrue(curl_args[-1].startswith('https://www.python.org/ftp/python/3.14.7/'))

    def test_download_failure_cleans_partial_file_without_verification_or_auth(self):
        self.workflow_fixture()
        self.env['ESTIMATOR_TEST_DOWNLOAD'] = 'failed'
        result = self.install()
        self.assert_failed_and_clean(result, ['temp', 'download', 'cleanup'])
        self.assertIn('Не удалось загрузить', result.stderr)

    def test_empty_and_oversized_downloads_stop_before_signature_or_auth(self):
        self.workflow_fixture()
        for download, size in [('empty', 'ok'), ('ok', 'oversize')]:
            with self.subTest(download=download, size=size):
                self.log.unlink(missing_ok=True)
                self.env['ESTIMATOR_TEST_DOWNLOAD'] = download
                self.env['ESTIMATOR_TEST_SIZE'] = size
                result = self.install()
                self.assert_failed_and_clean(result, ['temp', 'download', 'cleanup'])
                self.assertIn('Размер установщика', result.stderr)

    def test_hash_mismatch_stops_before_signature_or_admin_prompt(self):
        self.workflow_fixture()
        self.env['ESTIMATOR_TEST_DOWNLOAD'] = 'changed'
        result = self.install()
        self.assert_failed_and_clean(result, ['temp', 'download', 'cleanup'])
        self.assertIn('Контрольная сумма', result.stderr)

    def test_untrusted_or_other_publisher_signature_prevents_admin_prompt(self):
        self.workflow_fixture()
        for signature in ('failed', 'wrong'):
            with self.subTest(signature=signature):
                self.log.unlink(missing_ok=True)
                self.env['ESTIMATOR_TEST_SIGNATURE'] = signature
                result = self.install()
                self.assert_failed_and_clean(result, ['temp', 'download', 'signature', 'cleanup'])

    def test_admin_cancellation_stops_without_running_python(self):
        self.workflow_fixture()
        self.env['ESTIMATOR_TEST_AUTH'] = 'cancelled'
        result = self.install()
        self.assert_failed_and_clean(result, ['temp', 'download', 'signature', 'auth', 'cleanup'])
        self.assertIn('отменена', result.stderr)
        self.assertFalse(self.python.exists())

    def test_failed_post_install_checks_never_return_unverified_python(self):
        self.workflow_fixture()
        for probe in ('missing', 'failed'):
            with self.subTest(probe=probe):
                self.log.unlink(missing_ok=True)
                self.python.unlink(missing_ok=True)
                self.env['ESTIMATOR_TEST_PROBE'] = probe
                result = self.install()
                expected = ['temp', 'download', 'signature', 'auth']
                if probe == 'failed':
                    expected.append('python')
                self.assert_failed_and_clean(result, expected + ['cleanup'])
                self.assertIn('Проверка установленного Python не пройдена', result.stderr)

    def test_incompatible_system_stops_before_temporary_files_or_download(self):
        self.workflow_fixture()
        self.env['ESTIMATOR_TEST_MACOS'] = '10.12.6'
        self.env['ESTIMATOR_TEST_ARCH'] = 'x86_64'
        self.assert_failed_and_clean(self.install(), [])

    def test_missing_native_install_script_stops_before_download(self):
        self.workflow_fixture()
        (self.app / 'scripts/install-python.applescript').unlink()
        result = self.install()
        self.assert_failed_and_clean(result, [])
        self.assertIn('Не найден системный сценарий', result.stderr)


if __name__ == '__main__':
    unittest.main()
