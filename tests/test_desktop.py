import unittest
from unittest.mock import Mock,patch
import desktop

class DesktopTests(unittest.TestCase):
    def test_resource_and_engine_smoke(self):
        result=desktop.self_test();self.assertTrue(result['resource_check']);self.assertTrue(result['calculation_check'])

    def test_native_lifecycle_has_private_renderer_and_no_python_bridge(self):
        view=Mock();view.settings={};app=Mock();app.origin='http://127.0.0.1:12345'
        with patch.dict('sys.modules',{'webview':view}),patch('desktop.server.EstimatorServer',return_value=app):
            self.assertEqual(desktop.main([]),0)
        self.assertEqual(view.create_window.call_args.args[1],app.origin)
        self.assertNotIn('js_api',view.create_window.call_args.kwargs)
        view.start.assert_called_once();self.assertTrue(view.start.call_args.kwargs['private_mode'])
        self.assertFalse(view.settings['ALLOW_FILE_URLS']);self.assertTrue(view.settings['ALLOW_DOWNLOADS'])
        app.start_background.assert_called_once();app.shutdown.assert_called_once();app.server_close.assert_called_once()
