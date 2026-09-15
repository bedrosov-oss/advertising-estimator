#!/bin/bash
# Optional local macOS build. Installs build tools only in this project folder.

launcher_dir=${BASH_SOURCE[0]%/*}
[ "$launcher_dir" != "${BASH_SOURCE[0]}" ] || launcher_dir=.
if ! cd -P -- "$launcher_dir"; then
    printf '%s\n' 'Не удалось открыть папку программы.' >&2
    exit 1
fi
app_dir=$PWD

if [ "$(/usr/bin/uname -s)" != Darwin ]; then
    printf '%s\n' 'Сборка AdvertisingEstimator.app выполняется только на macOS.' >&2
    exit 1
fi

for resource in scripts/mac-python.sh server.py engine.py reporting.py \
    web data docs examples supporting-skill fonts local_store.py production.py file_formats.py draft_assistant.py fns_registry.py supplier_fetch.py supplier_prices.py runtime_setup.py LICENSE THIRD_PARTY_NOTICES.md; do
    if [ ! -e "$app_dir/$resource" ]; then
        printf 'Отсутствует файл или папка: %s\n' "$resource" >&2
        printf '%s\n' 'Распакуйте архив полностью. Инструкция: README_RU.txt.' >&2
        exit 1
    fi
done

. "$app_dir/scripts/mac-python.sh"
if ! estimator_python=$(estimator_find_python); then
    estimator_python_error
    exit 1
fi

if ! document_python=$("$estimator_python" -B "$app_dir/runtime_setup.py"); then
    printf '%s\n' 'Сначала завершите подготовку библиотек через START_MAC.command.' >&2
    exit 1
fi
estimator_python=$document_python

build_venv="$app_dir/.build-venv-mac"
build_python="$build_venv/bin/python"
if [ -L "$build_venv" ]; then
    printf '%s\n' 'Папка .build-venv-mac является ссылкой. Используйте отдельную папку проекта.' >&2
    exit 1
fi
if [ -e "$build_venv" ] && [ ! -x "$build_python" ]; then
    printf '%s\n' 'Папка .build-venv-mac неполная. Переименуйте её и повторите сборку.' >&2
    exit 1
fi

printf '%s\n' \
    'Сборка установит PyInstaller 6.22.3 в .build-venv-mac внутри папки программы.' \
    'Потребуется интернет. Созданные ранее файлы в build-native и dist-native могут быть заменены.'
if [ ! -x "$build_python" ]; then
    "$estimator_python" -B -m venv "$build_venv" || exit "$?"
fi
if ! "$build_python" -B -c \
    'import pathlib, sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) and pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.argv[1]).resolve() and sys.prefix != sys.base_prefix else 1)' \
    "$build_venv"; then
    printf '%s\n' 'Среда .build-venv-mac не подходит. Переименуйте её и повторите сборку с Python 3.10+.' >&2
    exit 1
fi

export PYINSTALLER_CONFIG_DIR="$app_dir/.build-cache-mac"
"$build_python" -B -m pip --disable-pip-version-check --no-cache-dir install --require-hashes -r "$app_dir/requirements-lock.txt" || exit "$?"
"$build_python" -B "$app_dir/scripts/build_desktop.py" || exit "$?"

printf '%s\n' \
    "Создано: $app_dir/dist-native/darwin/AdvertisingEstimator.app" \
    'Архитектура приложения соответствует Python, использованному для сборки.' \
    'Подпись Developer ID и нотариальное заверение Apple этим сценарием не выполняются.' \
    'Проверьте запуск и расчёты на целевом Mac перед передачей приложения.'
