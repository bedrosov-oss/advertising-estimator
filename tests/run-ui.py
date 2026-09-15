"""Developer-only simulated DOM + real local HTTP integration tests.

Requires Node.js and jsdom installed separately for development. Ordinary users
need only Python. This does not test browser rendering or Windows executables.
"""
import os
from pathlib import Path
import subprocess
import sys
import threading
import tempfile
import base64
from io import BytesIO
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import EstimatorServer
import fns_registry
from registry_fixtures import DemoFNSOpener, TickClock, QueueOpener, dadata

temporary = tempfile.TemporaryDirectory(prefix="estimator-ui-workspace-")
server = EstimatorServer(("127.0.0.1", 0), data_dir=temporary.name)
server._fns=fns_registry.RegistryService(Path(temporary.name)/'fns-documents',opener_factory=DemoFNSOpener,clock=TickClock())
real_dadata=fns_registry.dadata_search
registry_patch=patch('server.fns_registry.dadata_search',side_effect=lambda query,key:real_dadata(query,key,opener=QueueOpener(dadata())))
registry_patch.start()
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    env = dict(os.environ, ESTIMATOR_TEST_URL=server.origin)
    import file_formats
    original = file_formats.export_xlsx({'rows':[]})
    fixture = BytesIO()
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for number, values in enumerate([['Название','Единица','Цена','Артикул','Поставщик'],['ПВХ из прайса','лист','1 200,50','PVC-3','Проверочный поставщик']],1):
        sheet += '<row r="'+str(number)+'">'
        for column,value in enumerate(values):
            sheet += '<c r="'+chr(65+column)+str(number)+'" t="inlineStr"><is><t>'+value+'</t></is></c>'
        sheet += '</row>'
    sheet += '</sheetData></worksheet>'
    with zipfile.ZipFile(BytesIO(original)) as source, zipfile.ZipFile(fixture,'w') as output:
        for name in source.namelist():
            output.writestr(name,sheet if name=='xl/worksheets/sheet1.xml' else source.read(name))
    env['ESTIMATOR_TEST_PRICEBOOK'] = base64.b64encode(fixture.getvalue()).decode()
    completed = subprocess.run(["node", str(ROOT / "tests" / "ui-smoke.cjs")],
                               cwd=ROOT, env=env, timeout=60)
    exit_code = completed.returncode
    if exit_code == 0:
        completed = subprocess.run(["node", str(ROOT / "tests" / "price-updates-ui.cjs")],
                                   cwd=ROOT, env=env, timeout=60)
        exit_code = completed.returncode
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    registry_patch.stop()
    temporary.cleanup()
raise SystemExit(exit_code)
