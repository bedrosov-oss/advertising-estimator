"""Prepare optional document dependencies in a dedicated user-owned virtualenv."""
from pathlib import Path
import os
import subprocess
import sys
import venv


def works(python):
    return subprocess.run([str(python),'-I','-B','-c',
        'import reportlab, PIL; from reportlab.platypus import LongTable; '
        'from reportlab.pdfbase.ttfonts import TTFont; '
        'assert tuple(int(x) for x in reportlab.Version.split(".")[:3]) >= (4,4,9); '
        'assert tuple(int(x) for x in PIL.__version__.split(".")[:3]) >= (12,3,0)'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0


def prepare():
    if works(sys.executable):return Path(sys.executable)
    if sys.platform!='darwin':
        raise RuntimeError('Автоматическая подготовка библиотек предназначена для macOS.')
    target=Path.home()/'Library/Application Support/AdvertisingEstimator'/('runtime-python-'+str(sys.version_info.major)+'.'+str(sys.version_info.minor))
    if target.is_symlink():raise RuntimeError('Папка среды является ссылкой. Проверьте каталог программы.')
    python=target/'bin/python3'
    if python.is_file() and works(python):return python
    print('Подготовка PDF: библиотеки будут установлены в отдельную среду сметчика. Потребуется интернет.',file=sys.stderr)
    if not python.exists():
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        venv.EnvBuilder(with_pip=True).create(target)
    command=[str(python),'-B','-m','pip','--isolated','--disable-pip-version-check','install',
        '--only-binary=:all:','--index-url','https://pypi.org/simple','--timeout','25','--retries','2',
        'reportlab==4.4.9','pillow==12.3.0']
    subprocess.run(command,stdout=sys.stderr,stderr=sys.stderr,check=True,timeout=600)
    if not works(python):raise RuntimeError('Проверка библиотек PDF не пройдена.')
    return python


if __name__=='__main__':
    try:print(prepare())
    except (OSError,RuntimeError,subprocess.SubprocessError) as exc:
        print('Не удалось подготовить библиотеки PDF. Проверьте доступ к pypi.org и повторите запуск. '+str(exc),file=sys.stderr)
        raise SystemExit(1)
