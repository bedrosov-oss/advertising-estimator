"""Build a portable Finder .app launcher bundle from source; no native compilation."""
from pathlib import Path
import plistlib
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'dist-launcher/AdvertisingEstimator.app'
FILES=['BUILD_MAC.command','requirements-web.txt','requirements-web-lock.txt','supply_hub.py','shipping_quotes.py','web_host.py','windows_launcher.py','windows_check.py','CHECK_WINDOWS.bat','START_MAC.command','START_DESKTOP.command','START_WINDOWS.bat','BUILD_WINDOWS.bat','requirements-build.txt','requirements-dev.txt','requirements-browser.txt','requirements-lock.txt','desktop.py','server.py','engine.py','reporting.py','local_store.py','production.py',
       'file_formats.py','draft_assistant.py','fns_registry.py','supplier_fetch.py','supplier_prices.py','price_monitor.py','mixed_layout.py','vector_geometry.py','advanced_documents.py','secret_store.py','stock.py','full_backup.py','catalog_match.py','feature_setup.py','requirements-features.txt','SETUP_FEATURES.command','SETUP_FEATURES.bat','price_pdf.py','supplier_adapters.py','runtime_setup.py','README.md','README_RU.txt','CHANGELOG.md','LICENSE','THIRD_PARTY_NOTICES.md']
DIRS=['web','data','examples','docs','fonts','scripts','supporting-skill']

LAUNCHER='''#!/bin/bash
if [ "$(/usr/bin/uname -s)" != Darwin ]; then
    printf '%s\\n' 'Это приложение запускается на macOS.' >&2
    exit 1
fi
launcher_dir=${BASH_SOURCE[0]%/*}
cd -P -- "$launcher_dir/../Resources/app" || exit 1
app_dir=$PWD
log_file=$(/usr/bin/mktemp /private/tmp/advertising-estimator-launch.XXXXXX) || exit 1
/bin/chmod 600 "$log_file" || exit 1
trap '/bin/rm -f -- "$log_file"' EXIT
/usr/bin/osascript -e 'display notification "Подготовка программы. При первом запуске может потребоваться интернет." with title "Сметчик"' >/dev/null 2>&1 || true
/bin/bash "$app_dir/START_DESKTOP.command" >"$log_file" 2>&1
launch_status=$?
if [ "$launch_status" -ne 0 ]; then
    /usr/bin/osascript - "$log_file" <<'APPLESCRIPT'
on run arguments
    set logPath to item 1 of arguments
    set errorText to do shell script "/usr/bin/tail -n 25 " & quoted form of logPath
    if (length of errorText) > 3500 then set errorText to text -3500 thru -1 of errorText
    display alert "Сметчик не запущен" message errorText as critical buttons {"OK"} default button "OK"
end run
APPLESCRIPT
fi
exit "$launch_status"
'''


def package():
    sys.path.insert(0,str(ROOT/'scripts'))
    from check_release import check_release
    check_release(ROOT)
    if DEST.exists():shutil.rmtree(DEST)
    code=DEST/'Contents/Resources/app';code.mkdir(parents=True)
    for name in FILES:shutil.copy2(ROOT/name,code/name)
    for name in DIRS:shutil.copytree(ROOT/name,code/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    executable=DEST/'Contents/MacOS/AdvertisingEstimator';executable.parent.mkdir()
    executable.write_text(LAUNCHER,encoding='utf-8');executable.chmod(0o755)
    with (DEST/'Contents/Info.plist').open('wb') as stream:
        plistlib.dump({'CFBundleName':'Сметчик','CFBundleDisplayName':'Сметчик рекламы',
          'CFBundleIdentifier':'local.advertising.estimator.launcher','CFBundleExecutable':'AdvertisingEstimator',
          'CFBundlePackageType':'APPL','CFBundleVersion':'1.0.12','CFBundleShortVersionString':'1.0.12',
          'LSMinimumSystemVersion':'10.13','LSUIElement':False,'NSHighResolutionCapable':True},stream)
    print(DEST)


if __name__=='__main__':package()
