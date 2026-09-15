"""Build on the target OS/architecture. Never labels a Linux binary as macOS/Windows."""
from pathlib import Path
import subprocess
import plistlib
import shutil
import importlib.metadata
import json
import sys

ROOT=Path(__file__).resolve().parents[1]

def verify_executable(executable, report_path, expected_version):
    report_path.unlink(missing_ok=True)
    subprocess.run([str(executable),'--self-test-report',str(report_path)],check=True,timeout=120)
    report=json.loads(report_path.read_text(encoding='utf-8'))
    required=('resource_check','calculation_check','word_excel_check','pdf_vector_workers_check','mixed_layout_check','ok')
    if report.get('version')!=expected_version or any(report.get(key) is not True for key in required):
        raise ValueError('Собранная программа не подтвердила самопроверку: '+str(report_path))

def main():
    from check_release import check_release
    check_release(ROOT)
    import PyInstaller.__main__
    sys.path.insert(0,str(ROOT))
    import server
    target=ROOT/'dist-native'/sys.platform
    args=['--noconfirm','--clean','--onedir','--noupx','--name','AdvertisingEstimator',
          '--distpath',str(target),'--workpath',str(ROOT/'build-native'/sys.platform),
          '--specpath',str(ROOT/'build-native')]
    if sys.platform in ('darwin','win32'):args+=['--windowed']
    if sys.platform=='darwin':args+=['--osx-bundle-identifier','local.advertising.estimator']
    for name in ('web','data','docs','examples','fonts'):
        args+=['--add-data',str(ROOT/name)+':'+name]
    for name in ('LICENSE','THIRD_PARTY_NOTICES.md','requirements-features.txt'):
        args+=['--add-data',str(ROOT/name)+':.']
    for name in ('pdfplumber','pypdfium2','apscheduler','webview','rectpack','ezdxf','svgpathtools','docxtpl','keyring','rapidfuzz'):
        args+=['--collect-all',name]
    for name in ('PIL','reportlab','bs4','xlsxwriter','jinja2.ext'):
        args+=['--hidden-import',name]
    if sys.platform=='win32':args+=['--collect-all','pythonnet','--collect-all','clr_loader']
    licenses=ROOT/'build-native/licenses';licenses.mkdir(parents=True,exist_ok=True)
    for dist in importlib.metadata.distributions():
        name=dist.metadata.get('Name','package')
        for entry in dist.files or []:
            if str(entry).rsplit('/',1)[-1].lower().startswith(('license','copying','notice')):
                source=Path(dist.locate_file(entry))
                if source.is_file() and source.stat().st_size<2*1024*1024:
                    target_file=licenses/name/str(entry).replace('/','_');target_file.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target_file)
    args+=['--add-data',str(licenses)+':third-party-licenses']
    if sys.platform=='win32':
        version=tuple(int(part) for part in server.VERSION.split('.'))+(0,)
        version_file=ROOT/'build-native/windows-version.txt'
        version_file.write_text("VSVersionInfo(ffi=FixedFileInfo(filevers="+repr(version)+",prodvers="+repr(version)+",mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',[StringStruct('FileVersion','"+server.VERSION+"'),StringStruct('ProductVersion','"+server.VERSION+"'),StringStruct('ProductName','Advertising Estimator')])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])")
        args+=['--version-file',str(version_file)]
    args.append(str(ROOT/'desktop.py'))
    PyInstaller.__main__.run(args)
    if sys.platform=='darwin':
        info_path=target/'AdvertisingEstimator.app/Contents/Info.plist'
        info=plistlib.loads(info_path.read_bytes());info['CFBundleVersion']=info['CFBundleShortVersionString']=server.VERSION
        info_path.write_bytes(plistlib.dumps(info))
        subprocess.run(['codesign','--force','--deep','--sign','-',str(target/'AdvertisingEstimator.app')],check=True)
    executable=(target/'AdvertisingEstimator.app/Contents/MacOS/AdvertisingEstimator' if sys.platform=='darwin'
                else target/'AdvertisingEstimator'/('AdvertisingEstimator.exe' if sys.platform=='win32' else 'AdvertisingEstimator'))
    verify_executable(executable,target/'self-test.json',server.VERSION)
    print('Built',server.VERSION,sys.platform,executable)

if __name__=='__main__':main()
