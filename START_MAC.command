#!/bin/bash
# Start the application; bootstrap an official Python package if none is usable.

launcher_dir=${BASH_SOURCE[0]%/*}
[ "$launcher_dir" != "${BASH_SOURCE[0]}" ] || launcher_dir=.
if ! cd -P -- "$launcher_dir"; then
    printf '%s\n' 'Не удалось открыть папку программы. Распакуйте архив полностью.' >&2
    exit 1
fi
app_dir=$PWD

for resource in scripts/mac-python.sh scripts/mac-install-python.sh \
    scripts/install-python.applescript scripts/python-packages.tsv server.py engine.py reporting.py supply_hub.py shipping_quotes.py \
    local_store.py production.py file_formats.py draft_assistant.py fns_registry.py supplier_fetch.py supplier_prices.py runtime_setup.py web/workspace.js web/customer.js web/price_updates.js web/price_updates.css \
    web/index.html web/app.js web/style.css data/prices.json data/sources.json \
    data/conditions.json data/websites.json examples/demo.json; do
    if [ ! -f "$app_dir/$resource" ]; then
        printf 'Отсутствует файл: %s\n' "$resource" >&2
        printf '%s\n' 'Распакуйте архив полностью и прочитайте README_RU.txt.' >&2
        exit 1
    fi
done

. "$app_dir/scripts/mac-python.sh"
if ! estimator_python=$(estimator_find_python); then
    printf '%s\n' 'Подходящий Python не найден. Подбираем официальный установщик для этого Mac.' >&2
    . "$app_dir/scripts/mac-install-python.sh"
    if ! estimator_python=$(estimator_install_python "$app_dir"); then
        printf '%s\n' 'Автоматическая установка не завершена. Причина указана выше; инструкция: README_RU.txt.' >&2
        exit 1
    fi
fi

# Use the macOS CA bundle for this process if a fresh python.org install has no
# default CA file yet. Preserve explicit user CA settings and TLS verification.
if [ -z "${SSL_CERT_FILE:-}" ] && [ -z "${SSL_CERT_DIR:-}" ] && [ -r /etc/ssl/cert.pem ]; then
    if "$estimator_python" -B -c 'import ssl, sys; sys.exit(0 if ssl.get_default_verify_paths().cafile is None else 1)' >/dev/null 2>&1; then
        export SSL_CERT_FILE=/etc/ssl/cert.pem
    fi
fi

if "$estimator_python" -B -c 'import sys; sys.exit(0 if sys.platform == "darwin" else 1)' >/dev/null 2>&1; then
    if ! document_python=$("$estimator_python" -B "$app_dir/runtime_setup.py"); then
        printf '%s\n' 'Подготовка PDF не завершена. Открываем сметчик с доступными функциями; PDF потребует повторной подготовки.' >&2
    else
        estimator_python=$document_python
    fi
fi

if [ "${1:-}" = "--install-features" ]; then
    exec "$estimator_python" -B "$app_dir/feature_setup.py"
fi
if [ -f "$app_dir/feature_setup.py" ]; then
    if feature_python=$("$estimator_python" -B "$app_dir/feature_setup.py" --find 2>/dev/null); then
        estimator_python=$feature_python
    fi
fi

printf '%s\n' 'Запуск сметчика. Интерфейс откроется в браузере.' \
    'Для завершения используйте кнопку выхода в программе или Ctrl+C в этом окне.'
if [ "${1:-}" = "--desktop" ]; then
    shift
    exec "$estimator_python" -B "$app_dir/desktop.py" "$@"
fi
exec "$estimator_python" -B "$app_dir/server.py" "$@"
