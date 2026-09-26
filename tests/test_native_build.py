import builtins
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_desktop import is_license_document
import check_native_ssl
import price_pdf


class NativeBuildTests(unittest.TestCase):
    def test_license_collection_excludes_native_and_debug_binaries(self):
        for name in ('LICENSE', 'LICENSE.txt', 'COPYING.LESSER', 'NOTICE.md'):
            self.assertTrue(is_license_document(name), name)
        for name in ('copying.cpython-312-darwin.so', 'LICENSE.dll',
                     'PyObjCTest/copying.cpython-312-darwin.so.dSYM/Contents/Resources/DWARF/copying.cpython-312-darwin.so'):
            self.assertFalse(is_license_document(name), name)

    def test_frozen_import_error_does_not_suggest_source_installer(self):
        original_import = builtins.__import__
        def missing(name, *args, **kwargs):
            if name == 'pdfplumber':
                raise ImportError('simulated missing PDF dependency')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=missing), patch.object(sys, 'frozen', True, create=True):
            with self.assertRaisesRegex(ValueError, 'готовой сборке'):
                price_pdf.extract('unused.pdf', {'sheet': 0})

    def test_native_ssl_rejects_dynamic_dependency(self):
        with patch.object(sys, 'platform', 'darwin'), patch('check_native_ssl.subprocess.run') as run:
            run.return_value.stdout = '_rust.so:\n /usr/local/opt/openssl@3/lib/libcrypto.3.dylib\n'
            with self.assertRaisesRegex(RuntimeError, 'dynamic OpenSSL'):
                check_native_ssl.check()
            run.return_value.stdout = '_rust.so:\n /usr/lib/libSystem.B.dylib\n'
            check_native_ssl.check()
