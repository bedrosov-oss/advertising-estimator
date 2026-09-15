-- Runs only after the launcher has downloaded and checked an official PSF package.
-- macOS manages administrator authentication; no password is read or stored here.
on run arguments
    if (count of arguments) is not 2 then error "Неверные параметры установки Python."
    set packagePath to item 1 of arguments
    set expectedHash to item 2 of arguments
    if packagePath does not start with "/private/tmp/advertising-python." then error "Неверный путь установщика Python."
    if (length of expectedHash) is not 64 then error "Неверная контрольная сумма Python."

    -- Copy into a fresh root-owned directory before verification so an unprivileged
    -- process cannot change the checked package between verification and installation.
    set installScript to "set -eu
umask 077
source_pkg=" & quoted form of packagePath & "
expected_hash=" & quoted form of expectedHash & "
case \"$expected_hash\" in *[!0-9a-f]*) exit 64 ;; esac
secured_dir=$(/usr/bin/mktemp -d /private/tmp/advertising-python-root.XXXXXX)
cleanup() {
    case \"$secured_dir\" in /private/tmp/advertising-python-root.??????) /bin/rm -rf -- \"$secured_dir\" ;; esac
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
/bin/chmod 700 \"$secured_dir\"
secured_pkg=\"$secured_dir/python.pkg\"
/bin/cp \"$source_pkg\" \"$secured_pkg\"
/usr/sbin/chown root:wheel \"$secured_pkg\"
/bin/chmod 600 \"$secured_pkg\"
actual_hash=$(/usr/bin/shasum -a 256 \"$secured_pkg\")
actual_hash=${actual_hash%% *}
if [ \"$actual_hash\" != \"$expected_hash\" ]; then
    printf '%s\\n' 'Контрольная сумма Python изменилась. Установка остановлена.' >&2
    exit 65
fi
signature=$(LC_ALL=C /usr/sbin/pkgutil --check-signature \"$secured_pkg\")
if ! printf '%s\\n' \"$signature\" | /usr/bin/grep -Eq '^[[:space:]]*1[.] Developer ID Installer: Python Software Foundation [(][A-Z0-9]+[)][[:space:]]*$'; then
    printf '%s\\n' 'Подпись Python Software Foundation не подтверждена.' >&2
    exit 66
fi
/usr/sbin/installer -pkg \"$secured_pkg\" -target /
"
    try
        do shell script installScript with administrator privileges
    on error errorMessage number errorNumber
        if errorNumber is -128 then error "Установка Python отменена пользователем в системном окне macOS." number -128
        error errorMessage number errorNumber
    end try
end run
