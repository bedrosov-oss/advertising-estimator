#!/bin/bash
# Shared by the macOS launchers. Compatible with Apple's /bin/bash 3.2.

estimator_python_candidates() {
    local candidate version
    # Respect an existing user-selected environment first. Never run a shell profile.
    candidate=$(type -P python3 2>/dev/null) || candidate=
    if [ -n "$candidate" ]; then printf '%s\n' "$candidate"; fi
    for version in 3.15 3.14 3.13 3.12 3.11 3.10; do
        candidate=$(type -P "python$version" 2>/dev/null) || candidate=
        if [ -n "$candidate" ]; then printf '%s\n' "$candidate"; fi
    done
    printf '%s\n' /opt/homebrew/bin/python3 /usr/local/bin/python3
    for version in 3.15 3.14 3.13 3.12 3.11 3.10; do
        printf '%s\n' "/Library/Frameworks/Python.framework/Versions/$version/bin/python3"
        printf '%s\n' "/opt/homebrew/opt/python@$version/bin/python$version"
        printf '%s\n' "/usr/local/opt/python@$version/bin/python$version"
    done
    printf '%s\n' /Library/Frameworks/Python.framework/Versions/Current/bin/python3
}

estimator_resolve_executable() {
    local candidate=$1 directory target count=0
    case "$candidate" in /*) ;; *) candidate="$PWD/$candidate" ;; esac
    while [ "$count" -lt 40 ]; do
        directory=${candidate%/*}
        directory=$(cd -P -- "$directory" 2>/dev/null && pwd) || return 1
        candidate="$directory/${candidate##*/}"
        if [ ! -L "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
        target=$(/usr/bin/readlink "$candidate") || return 1
        case "$target" in
            /*) candidate=$target ;;
            *) candidate="$directory/$target" ;;
        esac
        count=$((count + 1))
    done
    return 1
}

estimator_find_python() {
    local candidates candidate resolved
    # Optional positional candidates allow read-only verification of this helper.
    if [ "$#" -gt 0 ]; then
        candidates=$(printf '%s\n' "$@")
    else
        candidates=$(estimator_python_candidates)
    fi
    while IFS= read -r candidate; do
        [ -n "$candidate" ] && [ -x "$candidate" ] || continue
        resolved=$(estimator_resolve_executable "$candidate") || continue
        # Apple's developer-tool launchers can offer to install Xcode on invocation.
        case "$resolved" in
            /usr/bin/python*|/Library/Developer/*|*/Xcode.app/Contents/Developer/*) continue ;;
        esac
        if "$candidate" -B -c 'import sys, ssl, json, http.server, decimal; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done <<EOF
$candidates
EOF
    return 1
}

estimator_python_error() {
    printf '%s\n' \
        'Не найден установленный Python 3.10 или новее.' \
        'Сначала откройте START_MAC.command: он подберёт и установит Python автоматически.' \
        'Подробная инструкция: README_RU.txt в папке программы.' >&2
}
