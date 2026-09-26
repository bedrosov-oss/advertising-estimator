"""Reject a Mac build tied to the runner's dynamic OpenSSL installation."""
import subprocess
import sys


def check():
    if sys.platform != 'darwin':
        raise RuntimeError('This check must run on macOS.')
    from cryptography.hazmat.bindings import _rust
    result = subprocess.run(['otool', '-L', _rust.__file__], check=True,
                            capture_output=True, text=True)
    dependencies = result.stdout.splitlines()[1:]
    if any('libssl' in line or 'libcrypto' in line for line in dependencies):
        raise RuntimeError('cryptography still depends on dynamic OpenSSL; rebuild with OPENSSL_STATIC=1 and clear its wheel cache.')
    print('cryptography has no dynamic OpenSSL dependency.')


if __name__ == '__main__':
    check()
