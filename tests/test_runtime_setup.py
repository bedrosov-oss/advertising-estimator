"""Verify dependency preparation without downloading or installing packages."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import runtime_setup


class RuntimeSetupTests(unittest.TestCase):
    def test_ready_python_does_not_install_anything(self):
        with patch.object(runtime_setup,'works',return_value=True), patch.object(runtime_setup.subprocess,'run') as run:
            self.assertEqual(runtime_setup.prepare(),Path(runtime_setup.sys.executable))
        run.assert_not_called()

    def test_non_mac_does_not_prepare_missing_dependencies(self):
        with patch.object(runtime_setup,'works',return_value=False), patch.object(runtime_setup.sys,'platform','linux'), patch.object(runtime_setup.venv,'EnvBuilder') as builder:
            with self.assertRaisesRegex(RuntimeError,'macOS'):runtime_setup.prepare()
        builder.assert_not_called()

    def test_mac_uses_dedicated_user_environment_and_binary_pinned_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runtime_setup.Path,'home',return_value=Path(directory)), patch.object(runtime_setup.sys,'platform','darwin'), patch.object(runtime_setup,'works',side_effect=[False,True]), patch.object(runtime_setup.venv,'EnvBuilder') as builder, patch.object(runtime_setup.subprocess,'run') as run:
                target=runtime_setup.prepare()
                self.assertTrue(str(target).startswith(str(Path(directory)/'Library/Application Support/AdvertisingEstimator/runtime-python-')))
                builder.assert_called_once_with(with_pip=True)
                builder.return_value.create.assert_called_once_with(target.parent.parent)
                command=run.call_args.args[0]
                self.assertEqual(command[0],str(target))
                self.assertIn('--only-binary=:all:',command)
                self.assertIn('https://pypi.org/simple',command)
                self.assertIn('reportlab==4.4.9',command)
                self.assertIn('pillow==12.3.0',command)
                self.assertEqual(run.call_args.kwargs['timeout'],600)

    def test_failed_install_is_not_reported_as_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runtime_setup.Path,'home',return_value=Path(directory)), patch.object(runtime_setup.sys,'platform','darwin'), patch.object(runtime_setup,'works',return_value=False), patch.object(runtime_setup.venv,'EnvBuilder'), patch.object(runtime_setup.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['pip'])):
                with self.assertRaises(subprocess.CalledProcessError):runtime_setup.prepare()
