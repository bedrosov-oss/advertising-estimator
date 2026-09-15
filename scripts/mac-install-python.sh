#!/bin/bash
# Explicitly invoked by START_MAC.command when no suitable Python is installed.
# Sourcing this file performs no downloads, installation, or configuration.
# Uses Apple's Bash 3.2 and macOS system tools; Python is not a prerequisite.

estimator_version_parts() {
    local version=$1 expression='^[0-9]{1,3}\.[0-9]{1,3}(\.[0-9]{1,3})?$'
    [[ "$version" =~ $expression ]] || return 1
    local major minor patch
    IFS=. read -r major minor patch <<EOF
$version
EOF
    [ -n "$patch" ] || patch=0
    printf '%s %s %s\n' "$((10#$major))" "$((10#$minor))" "$((10#$patch))"
}

# Return success if the first numeric version is at least the second version.
estimator_version_at_least() {
    local left right a b c x y z
    left=$(estimator_version_parts "$1") || return 1
    right=$(estimator_version_parts "$2") || return 1
    read -r a b c <<EOF
$left
EOF
    read -r x y z <<EOF
$right
EOF
    [ "$a" -gt "$x" ] || {
        [ "$a" -eq "$x" ] && { [ "$b" -gt "$y" ] || { [ "$b" -eq "$y" ] && [ "$c" -ge "$z" ]; }; }
    }
}

# Read-only helper: print one validated, compatible TSV record or fail closed.
# Arguments: bundled manifest path, macOS product version, machine architecture.
estimator_select_python_package() {
    local manifest=$1 macos_version=$2 machine=$3
    local header line version minimum architectures url digest python_path extra
    local major minor patch selected= selected_version= tab=$'\t'
    local digest_expression='^[0-9a-f]{64}$' package_expression='^3\.[0-9]{1,2}\.[0-9]{1,3}$'
    if ! estimator_version_parts "$macos_version" >/dev/null; then
        printf '%s\n' 'Не удалось определить корректную версию macOS. Автоматическая установка остановлена.' >&2
        return 1
    fi
    case "$machine" in arm64|x86_64) ;; *)
        printf 'Архитектура %s не поддерживается установщиком Python.\n' "$machine" >&2
        return 1 ;;
    esac
    if [ "$machine" = arm64 ] && ! estimator_version_at_least "$macos_version" 11.0; then
        printf '%s\n' 'Версия macOS не соответствует архитектуре Apple Silicon. Установка остановлена.' >&2
        return 1
    fi
    [ -f "$manifest" ] && [ -r "$manifest" ] || {
        printf '%s\n' 'Не найден список проверенных установщиков Python. Распакуйте архив полностью.' >&2
        return 1
    }
    {
        IFS= read -r header || return 1
        if [ "$header" != "version${tab}min_macos${tab}architectures${tab}url${tab}sha256${tab}python_path" ]; then
            printf '%s\n' 'Неверный заголовок списка установщиков Python.' >&2
            return 1
        fi
        while IFS= read -r line || [ -n "$line" ]; do
            [ -n "$line" ] || continue
            IFS="$tab" read -r version minimum architectures url digest python_path extra <<EOF
$line
EOF
            if [ "$line" != "${version}${tab}${minimum}${tab}${architectures}${tab}${url}${tab}${digest}${tab}${python_path}" ] ||
               [ -n "$extra" ] || ! [[ "$version" =~ $package_expression ]] ||
               ! [[ "$digest" =~ $digest_expression ]] ||
               ! estimator_version_parts "$minimum" >/dev/null ||
               ! estimator_version_at_least "$version" 3.10; then
                printf '%s\n' 'Повреждена запись в списке установщиков Python. Установка остановлена.' >&2
                return 1
            fi
            case "$architectures" in arm64|x86_64|arm64,x86_64|x86_64,arm64) ;; *)
                printf '%s\n' 'Неверная архитектура в списке установщиков Python.' >&2
                return 1 ;;
            esac
            IFS=. read -r major minor patch <<EOF
$version
EOF
            if [ "$url" != "https://www.python.org/ftp/python/$version/python-$version-macos11.pkg" ] ||
               [ "$python_path" != "/Library/Frameworks/Python.framework/Versions/$major.$minor/bin/python3" ]; then
                printf '%s\n' 'Адрес или путь установщика не соответствует проверенному формату python.org.' >&2
                return 1
            fi
            case ",$architectures," in *",$machine,"*) ;; *) continue ;; esac
            estimator_version_at_least "$macos_version" "$minimum" || continue
            if [ -z "$selected_version" ] || estimator_version_at_least "$version" "$selected_version"; then
                selected=$line
                selected_version=$version
            fi
        done
    } < "$manifest"
    if [ -z "$selected" ]; then
        printf 'Для macOS %s (%s) в комплекте нет совместимого проверенного установщика Python.\n' "$macos_version" "$machine" >&2
        printf '%s\n' 'Автоматическая установка остановлена; обновите macOS или используйте уже установленный совместимый Python.' >&2
        return 1
    fi
    printf '%s\n' "$selected"
}

estimator_download_python_package() (
    local url=$1 destination=$2
    # Limit output even with older curl versions that only inspect Content-Length.
    # The limit remains <= 240,000,000 bytes with either 512- or 1024-byte blocks.
    ulimit -f 234375 || return 1
    /usr/bin/curl --fail --location --proto '=https' --proto-redir '=https' \
        --tlsv1.2 --max-redirs 3 --connect-timeout 20 --max-time 180 \
        --retry 2 --retry-delay 1 --retry-max-time 540 --max-filesize 240000000 \
        --output "$destination" "$url" >&2
)

estimator_verify_python_package() {
    local package_path=$1 expected_hash=$2 actual_hash signature
    actual_hash=$(/usr/bin/shasum -a 256 "$package_path") || return 1
    actual_hash=${actual_hash%% *}
    if [ "$actual_hash" != "$expected_hash" ]; then
        printf '%s\n' 'Контрольная сумма Python не совпала. Файл не будет установлен.' >&2
        return 1
    fi
    if ! signature=$(LC_ALL=C /usr/sbin/pkgutil --check-signature "$package_path" 2>&1); then
        printf '%s\n' 'macOS не подтвердила подпись установщика Python. Установка остановлена.' >&2
        return 1
    fi
    if ! printf '%s\n' "$signature" | /usr/bin/grep -Eq \
        '^[[:space:]]*1[.] Developer ID Installer: Python Software Foundation [(][A-Z0-9]+[)][[:space:]]*$'; then
        printf '%s\n' 'Подпись установщика не принадлежит Python Software Foundation. Установка остановлена.' >&2
        return 1
    fi
}

estimator_install_python() (
    local app_dir=$1 platform macos_version machine record
    local version minimum architectures url digest python_path
    local temporary_dir= package_path bytes
    # Never create files or offer authentication outside macOS.
    platform=$(/usr/bin/uname -s) || return 1
    if [ "$platform" != Darwin ]; then
        printf '%s\n' 'Автоматическая установка Python предназначена только для macOS.' >&2
        return 1
    fi
    macos_version=$(SYSTEM_VERSION_COMPAT=0 /usr/bin/sw_vers -productVersion) || {
        printf '%s\n' 'Не удалось прочитать версию macOS. Установка остановлена.' >&2; return 1;
    }
    machine=$(/usr/bin/uname -m) || return 1
    record=$(estimator_select_python_package "$app_dir/scripts/python-packages.tsv" "$macos_version" "$machine") || return 1
    IFS=$'\t' read -r version minimum architectures url digest python_path <<EOF
$record
EOF
    if [ ! -f "$app_dir/scripts/install-python.applescript" ]; then
        printf '%s\n' 'Не найден системный сценарий установки Python. Распакуйте архив полностью.' >&2
        return 1
    fi
    temporary_dir=$(/usr/bin/mktemp -d /private/tmp/advertising-python.XXXXXX) || {
        printf '%s\n' 'Не удалось создать временную папку для установщика Python.' >&2; return 1;
    }
    estimator_cleanup_python_download() {
        case "$temporary_dir" in /private/tmp/advertising-python.??????)
            /bin/rm -rf -- "$temporary_dir" ;;
        esac
    }
    trap estimator_cleanup_python_download EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP
    /bin/chmod 700 "$temporary_dir" || return 1
    package_path="$temporary_dir/python.pkg"
    printf 'Подходящий Python не найден. Загружается Python %s с python.org для macOS %s (%s).\n' "$version" "$macos_version" "$machine" >&2
    if ! estimator_download_python_package "$url" "$package_path"; then
        printf '%s\n' 'Не удалось загрузить Python. Проверьте подключение к интернету и повторите START_MAC.command.' >&2
        return 1
    fi
    bytes=$(/usr/bin/wc -c < "$package_path") || return 1
    if [ "$bytes" -le 0 ] || [ "$bytes" -gt 240000000 ]; then
        printf '%s\n' 'Размер установщика Python не соответствует допустимому. Установка остановлена.' >&2
        return 1
    fi
    printf '%s\n' 'Проверка контрольной суммы и подписи Python Software Foundation.' >&2
    estimator_verify_python_package "$package_path" "$digest" || return 1
    printf '%s\n' 'Для установки Python macOS запросит подтверждение администратора в системном окне.' >&2
    if ! /usr/bin/osascript "$app_dir/scripts/install-python.applescript" "$package_path" "$digest" >&2; then
        printf '%s\n' 'Установка Python отменена или завершилась ошибкой. Программа не запущена; повторите запуск после устранения причины.' >&2
        return 1
    fi
    if [ ! -x "$python_path" ] || ! "$python_path" -I -B -c \
        'import sys, ssl, json, http.server, decimal, sqlite3; sys.exit(0 if sys.version_info[:2] >= (3, 10) and ".".join(map(str, sys.version_info[:3])) == sys.argv[1] else 1)' \
        "$version" >/dev/null 2>&1; then
        printf '%s\n' 'Проверка установленного Python не пройдена. Запуск программы остановлен.' >&2
        return 1
    fi
    printf 'Python %s установлен и проверен.\n' "$version" >&2
    printf '%s\n' "$python_path"
)
