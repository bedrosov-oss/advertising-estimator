"""Optional real Chromium smoke check. Install requirements-browser and Chromium first."""
from pathlib import Path
import sys
import tempfile
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import EstimatorServer
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory(prefix='estimator-browser-') as directory:
    server=EstimatorServer(data_dir=directory);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(server.origin);page.locator('[data-tab="advanced-tools"]').wait_for()
            page.locator('[data-tab="advanced-geometry"]').click();page.locator('#geometry-calculate').click()
            page.locator('#geometry-layout-result').get_by_text('Листов: 1',exact=False).wait_for()
            page.locator('[data-tab="advanced-tools"]').click();page.locator('#advanced-stock-item').wait_for()
            assert not errors,errors
            browser.close();print('PASS: real Chromium loads all modules and calculates a mixed layout.')
    finally:server.shutdown();server.server_close();thread.join(2)
