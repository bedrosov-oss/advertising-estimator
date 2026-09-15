"""Bounded public supplier downloads; direct sockets, no proxy or page execution.

DNS results are checked once per hop and the selected numeric address is pinned
to the socket. HTTPS still verifies the original hostname and sends it as SNI.
This module deliberately does not interpret a downloaded price or document.
"""
from datetime import datetime, timezone
import hashlib
import http.client
import io
import ipaddress
import queue
import re
import socket
import ssl
import threading
import time
import urllib.parse

MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 20
MAX_REDIRECTS = 3
USER_AGENT = 'AdvertisingEstimator/1.0.12'


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('supplier deadline')
    return min(TIMEOUT, remaining)


def _public_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped and not _public_ip(str(address.ipv4_mapped)):
            return False
        if address.sixtofour and not _public_ip(str(address.sixtofour)):
            return False
        if address.teredo:
            return False
    return True


def _url(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError('Укажите публичную ссылку поставщика длиной до 4096 символов.')
    if any(ord(char) < 33 or ord(char) == 127 for char in value) or '\\' in value:
        raise ValueError('В ссылке поставщика есть недопустимые символы.')
    try:
        parts = urllib.parse.urlsplit(value)
        host = (parts.hostname or '').rstrip('.').lower()
        port = parts.port
    except ValueError:
        raise ValueError('Не удалось прочитать ссылку поставщика.') from None
    if parts.scheme not in {'http', 'https'} or not host or parts.username is not None or parts.password is not None:
        raise ValueError('Нужна публичная ссылка HTTP или HTTPS без логина и пароля.')
    if port not in {None, 80, 443}:
        raise ValueError('Для сайта поставщика разрешены только веб-порты 80 и 443.')
    if '%' in host or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.lan', '.home.arpa')):
        raise ValueError('Локальные и служебные адреса поставщиков запрещены.')
    try:
        host = host.encode('idna').decode('ascii')
    except UnicodeError:
        raise ValueError('Некорректное имя сайта поставщика.') from None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if '.' not in host or not re.fullmatch(r'[a-z0-9.-]+', host):
            raise ValueError('Укажите полное публичное имя сайта поставщика.')
        labels = host.split('.')
        if any(not part or len(part) > 63 or part.startswith('-') or part.endswith('-') for part in labels) or len(host) > 253:
            raise ValueError('Некорректное имя сайта поставщика.')
        if all(re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)', part) for part in labels):
            raise ValueError('Сокращённые, восьмеричные и шестнадцатеричные IP-адреса запрещены.')
    else:
        if not _public_ip(str(address)):
            raise ValueError('Локальные и служебные IP-адреса поставщиков запрещены.')
        host = str(address)
    authority = '[' + host + ']' if ':' in host else host
    if port is not None:
        authority += ':' + str(port)
    # Unicode path/query text becomes a request-safe UTF-8 URL; existing escapes
    # remain intact. A fragment is browser-only and must not reach the supplier.
    try:
        path = urllib.parse.quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~")
        query = urllib.parse.quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
    except UnicodeError:
        raise ValueError('Ссылка поставщика содержит некорректный текст.') from None
    canonical = urllib.parse.urlunsplit((parts.scheme, authority, path, query, ''))
    if len(canonical) > 4096:
        raise ValueError('Закодированная ссылка поставщика превышает 4096 символов.')
    return urllib.parse.urlsplit(canonical)


def _dns_addresses(host, port, deadline):
    """Bound the caller's DNS wait; libc resolution itself has no timeout API."""
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put((True, socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)))
        except OSError as exc:
            result.put((False, exc))

    threading.Thread(target=resolve, daemon=True, name='supplier-dns').start()
    try:
        okay, values = result.get(timeout=_remaining(deadline))
    except queue.Empty:
        raise TimeoutError('supplier DNS deadline') from None
    if not okay:
        raise ValueError('Не удалось определить адрес сайта поставщика. Проверьте ссылку и подключение.') from None
    return values


def _resolve_public(host, port, deadline):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        candidates = _dns_addresses(host, port, deadline)
    else:
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        sockaddr = (host, port, 0, 0) if address.version == 6 else (host, port)
        candidates = [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', sockaddr)]
    if not candidates:
        raise ValueError('У сайта поставщика не найден публичный сетевой адрес.')
    checked = []
    for family, socktype, protocol, _name, sockaddr in candidates:
        if family not in {socket.AF_INET, socket.AF_INET6} or socktype != socket.SOCK_STREAM:
            raise ValueError('Сайт поставщика вернул неподдерживаемый сетевой адрес.')
        if not _public_ip(sockaddr[0]) or (family == socket.AF_INET6 and sockaddr[3] != 0):
            raise ValueError('Сайт поставщика указывает на локальный или служебный адрес. Загрузка остановлена.')
        # Use the requested port, never an untrusted resolver-provided port.
        pinned = (sockaddr[0], port, 0, 0) if family == socket.AF_INET6 else (sockaddr[0], port)
        checked.append((family, pinned))
    return checked[0]


class _DeadlineReader(io.RawIOBase):
    def __init__(self, owner):
        self.owner = owner
        super().__init__()

    def readable(self):
        return True

    def readinto(self, buffer):
        self.owner.raw.settimeout(_remaining(self.owner.deadline))
        return self.owner.raw.recv_into(buffer)

    def close(self):
        if not self.closed:
            try:
                super().close()
            finally:
                self.owner._release_reader()


class _DeadlineSocket:
    """Reset timeout before every read, including HTTP header and chunk reads.

Retain the socket while a response file is open, matching socket.makefile()
ownership when HTTPConnection closes a Connection: close response early.
"""
    def __init__(self, raw, deadline):
        self.raw = raw
        self.deadline = deadline
        self.readers = 0
        self.closing = False

    def sendall(self, data):
        self.raw.settimeout(_remaining(self.deadline))
        self.raw.sendall(data)

    def makefile(self, mode):
        if mode != 'rb':
            raise ValueError('Неподдерживаемый режим чтения ответа поставщика.')
        self.readers += 1
        return io.BufferedReader(_DeadlineReader(self))

    def _release_reader(self):
        self.readers -= 1
        if self.closing and self.readers == 0:
            self.raw.close()

    def close(self):
        self.closing = True
        if self.readers == 0:
            self.raw.close()


def _open_response(parts, deadline):
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    family, sockaddr = _resolve_public(host, port, deadline)
    raw = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    connection = None
    try:
        raw.settimeout(_remaining(deadline))
        raw.connect(sockaddr)
        if ipaddress.ip_address(raw.getpeername()[0]) != ipaddress.ip_address(sockaddr[0]):
            raise ValueError('Сайт поставщика изменил сетевой адрес. Загрузка остановлена.')
        if parts.scheme == 'https':
            context = ssl.create_default_context()
            raw.settimeout(_remaining(deadline))
            raw = context.wrap_socket(raw, server_hostname=host)
        connection = http.client.HTTPConnection(host, port, timeout=_remaining(deadline))
        connection.sock = _DeadlineSocket(raw, deadline)
        target = urllib.parse.urlunsplit(('', '', parts.path or '/', parts.query, ''))
        connection.request('GET', target, headers={
            'Host': parts.netloc, 'User-Agent': USER_AGENT,
            'Accept': 'text/html,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream;q=0.8,*/*;q=0.5',
            'Accept-Encoding': 'identity', 'Connection': 'close'})
        return connection, connection.getresponse()
    except BaseException:
        if connection is not None:
            connection.close()
        else:
            raw.close()
        raise


def _read_content(response, deadline):
    encoding = (response.getheader('Content-Encoding') or 'identity').strip().lower()
    if encoding != 'identity':
        raise ValueError('Поставщик прислал сжатый ответ неподдерживаемого вида. Скачайте прайс в браузере и импортируйте файл.')
    transfer = (response.getheader('Transfer-Encoding') or '').strip().lower()
    if transfer and transfer != 'chunked':
        raise ValueError('Поставщик прислал неподдерживаемый формат передачи файла. Скачайте прайс в браузере и импортируйте файл.')
    length = response.getheader('Content-Length')
    if length is not None:
        if not re.fullmatch(r'[0-9]{1,12}', length.strip()):
            raise ValueError('Поставщик прислал некорректный размер файла.')
        length = int(length)
        if length > MAX_BYTES:
            raise ValueError('Прайс поставщика превышает допустимый размер 8 МБ.')
        if response.getheader('Transfer-Encoding'):
            raise ValueError('Поставщик прислал противоречивые параметры размера файла.')
    parts = []
    size = 0
    while True:
        _remaining(deadline)
        block = response.read1(min(65536, MAX_BYTES + 1 - size))
        if not block:
            break
        parts.append(block)
        size += len(block)
        if size > MAX_BYTES:
            raise ValueError('Прайс поставщика превышает допустимый размер 8 МБ.')
    if length is not None and size != length:
        raise ValueError('Файл поставщика получен не полностью. Повторите загрузку.')
    if not size:
        raise ValueError('Поставщик вернул пустой файл.')
    return b''.join(parts)


def fetch_public(url):
    """Download public bytes within 20 seconds and at most three redirects.

Only the final URL and downloaded bytes are returned; no authentication,
proxy settings, browser cookies or customer/project details are sent.
"""
    deadline = time.monotonic() + TIMEOUT
    parts = _url(url)
    visited = set()
    try:
        for hop in range(MAX_REDIRECTS + 1):
            current = parts.geturl()
            if current in visited:
                raise ValueError('Сайт поставщика повторяет перенаправление. Укажите прямую ссылку на прайс.')
            visited.add(current)
            connection, response = _open_response(parts, deadline)
            try:
                status = response.status
                if status in {301, 302, 303, 307, 308}:
                    location = response.getheader('Location')
                    if not location or hop >= MAX_REDIRECTS:
                        raise ValueError('У сайта поставщика слишком много перенаправлений или отсутствует новая ссылка. Укажите прямую ссылку.')
                    if len(location) > 4096 or any(ord(char) < 33 or ord(char) == 127 for char in location) or '\\' in location:
                        raise ValueError('Поставщик прислал некорректную ссылку перенаправления.')
                    following = _url(urllib.parse.urljoin(current, location))
                    if parts.scheme == 'https' and following.scheme != 'https':
                        raise ValueError('Поставщик переводит защищённую ссылку HTTPS на HTTP. Укажите прямую защищённую ссылку.')
                    parts = following
                    continue
                if status in {401, 403} or (response.getheader('cf-mitigated') or '').lower() == 'challenge':
                    raise ValueError('Поставщик требует вход или проверку в браузере (возможно, капчу). Откройте сайт, скачайте прайс и импортируйте файл.')
                if status == 429:
                    raise ValueError('Поставщик ограничил частоту запросов. Подождите и повторите обновление позднее.')
                if status == 404:
                    raise ValueError('Прайс по этой ссылке не найден. Проверьте адрес страницы поставщика.')
                if status != 200 or response.getheader('Content-Range'):
                    raise ValueError('Поставщик не выдал полный файл. Проверьте ссылку или повторите обновление позднее.')
                content = _read_content(response, deadline)
                content_type = (response.getheader('Content-Type') or 'application/octet-stream').strip()[:200]
                return {'url': current, 'content_type': content_type, 'content': content,
                        'sha256': hashlib.sha256(content).hexdigest(),
                        'retrieved_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
            finally:
                response.close()
                connection.close()
    except ssl.SSLCertVerificationError:
        raise ValueError('Не удалось проверить сертификат сайта поставщика. Проверьте ссылку и дату на компьютере.') from None
    except ssl.SSLError:
        raise ValueError('Не удалось установить защищённое соединение с поставщиком. Проверьте ссылку или повторите позднее.') from None
    except (TimeoutError, socket.timeout):
        raise ValueError('Поставщик не ответил за 20 секунд. Повторите обновление позднее.') from None
    except (http.client.IncompleteRead, http.client.RemoteDisconnected, ConnectionResetError):
        raise ValueError('Соединение с поставщиком прервалось. Файл получен не полностью; повторите загрузку.') from None
    except (OSError, http.client.HTTPException):
        raise ValueError('Не удалось подключиться к поставщику. Проверьте интернет, ссылку и доступность сайта.') from None
