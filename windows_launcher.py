"""Windows entry point; pass Unicode paths directly, never via cmd FOR /F."""
from pathlib import Path
import subprocess
import sys
import feature_setup

ROOT = Path(__file__).resolve().parent

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv.pop(0) if argv else 'start'
    if action not in ('start', 'check'):
        raise ValueError('Неизвестное действие запуска.')
    python = feature_setup.ready_python() or Path(sys.executable)
    script = 'desktop.py' if action == 'start' else 'windows_check.py'
    return subprocess.run([str(python), '-X', 'utf8', '-B', str(ROOT/script), *argv], cwd=ROOT).returncode

if __name__ == '__main__':
    try:raise SystemExit(main())
    except OSError:
        print('Не удалось запустить Python. Закройте программу и повторите SETUP_FEATURES.bat.',file=sys.stderr)
        raise SystemExit(1)
