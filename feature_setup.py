"""Install optional features in the user's own venv, without changing system Python."""
from pathlib import Path
import subprocess
import sys
import venv
from local_store import data_directory

ROOT=Path(__file__).resolve().parent

def runtime():
    target=data_directory()/('features-python-'+str(sys.version_info.major)+'.'+str(sys.version_info.minor))
    return target, target/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')

def ready_python():
    target, python = runtime()
    try:
        if python.is_file() and (target/'estimator-ready').read_text(encoding='utf-8').strip() == (ROOT/'requirements-features.txt').read_text(encoding='utf-8').strip():
            return python
    except (OSError, UnicodeError):
        pass
    return None

def main():
    target,python=runtime()
    if '--find' in sys.argv:
        ready = ready_python()
        if ready:print(ready);return 0
        return 1
    print('Установка дополнений в отдельное окружение. Требуется интернет; системный Python не изменяется.',flush=True)
    target.mkdir(parents=True,exist_ok=True)
    (target/'estimator-ready').unlink(missing_ok=True)
    venv.EnvBuilder(with_pip=True).create(target)
    subprocess.run([str(python),'-m','pip','--isolated','install','--disable-pip-version-check','-r',str(ROOT/'requirements-features.txt')],check=True,timeout=900)
    (target/'estimator-ready').write_text((ROOT/'requirements-features.txt').read_text(encoding='utf-8'), encoding='utf-8')
    print('Готово. Снова запустите программу.')
    return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,subprocess.SubprocessError) as exc:
        if '--find' not in sys.argv:print('Подготовка не завершена. Проверьте интернет и свободное место.',file=sys.stderr)
        raise SystemExit(1)
