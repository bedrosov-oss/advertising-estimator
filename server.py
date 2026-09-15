"""Local, session-only HTTP interface for the advertising estimator (Python 3.10+).

Tavily REST references verified 2026-09-11:
https://docs.tavily.com/documentation/api-reference/endpoint/search
https://docs.tavily.com/documentation/api-reference/endpoint/extract
No request logging or persistent authentication. Local workspace writes are explicit; network calls require user actions.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import re
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import engine
import reporting
import local_store
import production
import file_formats
import draft_assistant
import fns_registry
import supplier_prices
import price_monitor
import mixed_layout
import vector_geometry
import advanced_documents
import secret_store
import supply_hub
import stock
import full_backup
import catalog_match

VERSION = "1.0.11"
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
MAX_BODY_BYTES = 3 * 1024 * 1024
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_UPSTREAM_BYTES = 8 * 1024 * 1024
TAVILY_TIMEOUT = 35


class APIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward an authorization header through an upstream redirect."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global and not address.is_multicast and not address.is_reserved


def public_url(value: object, *, resolve: bool = False) -> str:
    """Validate a public website URL; optionally reject private DNS resolutions.

Only Tavily receives the URL. This program does not fetch the supplied page.
DNS validation is useful input screening, not a guarantee about Tavily's resolver.
"""
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("Укажите публичный адрес страницы длиной до 4096 символов.")
    if any(ord(char) < 33 or ord(char) == 127 for char in value) or "\\" in value:
        raise ValueError("В адресе страницы есть недопустимые символы.")
    try:
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Некорректный адрес страницы.") from exc
    if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None:
        raise ValueError("Нужен публичный адрес http:// или https:// без пароля в ссылке.")
    if port not in {None, 80, 443}:
        raise ValueError("Поддерживаются публичные веб-страницы на портах 80 и 443.")
    if "%" in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        raise ValueError("Локальные адреса для Tavily не поддерживаются.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Некорректное имя сайта.") from exc
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
            raise ValueError("Укажите полное публичное имя сайта.")
        labels = host.split(".")
        if any(not part or len(part) > 63 or part.startswith("-") or part.endswith("-") for part in labels):
            raise ValueError("Некорректное имя сайта.")
        # Disallow alternate integer/octal/hex IP spellings, e.g. 127.1 or 0x7f.0.0.1.
        if all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", part) for part in labels):
            raise ValueError("Сокращённые и числовые формы адресов не поддерживаются.")
    else:
        if not _is_public_ip(host):
            raise ValueError("Локальные и служебные IP-адреса для Tavily не поддерживаются.")
    if resolve:
        try:
            addresses = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("Не удалось проверить имя сайта. Проверьте адрес и подключение к интернету.") from exc
        if not addresses or any(not _is_public_ip(item[4][0]) for item in addresses):
            raise ValueError("Имя сайта указывает на локальный или служебный IP-адрес.")
    return value


def _plain(value: object, limit: int, secret: str = "") -> str:
    text = value if isinstance(value, str) else ""
    if secret:
        text = text.replace(secret, "[ключ скрыт]")
    return text[:limit]


def _result_url(value: object, secret: str = "") -> str:
    try:
        if secret and isinstance(value, str) and secret in value:
            return ""
        return public_url(value)
    except ValueError:
        return ""


def tavily_request(operation: str, payload: dict) -> dict:
    """An explicit, bounded REST request. The API key is never stored on disk."""
    key = payload.get("api_key")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", key):
        raise ValueError("Введите API-ключ Tavily в поле ключа. Ключ не сохраняется программой.")
    if operation == "search":
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 1500:
            raise ValueError("Введите поисковый запрос длиной от 1 до 1500 символов.")
        count = payload.get("max_results", 5)
        if type(count) is not int or not 1 <= count <= 10:
            raise ValueError("Количество результатов должно быть целым числом от 1 до 10.")
        domains_text = payload.get("domains", "")
        if not isinstance(domains_text, str) or len(domains_text) > 2000:
            raise ValueError("Домены нужно передать строкой длиной до 2000 символов.")
        domains = [entry for entry in re.split(r"[,;\s]+", domains_text.strip()) if entry]
        if len(domains) > 10:
            raise ValueError("Укажите не более 10 доменов.")
        normalized_domains = []
        for domain in domains:
            if "://" in domain or "/" in domain or ":" in domain:
                raise ValueError("В фильтре укажите домены без https:// и пути, например forda.ru.")
            public_url("https://" + domain)
            normalized_domains.append(domain.encode("idna").decode("ascii").lower())
        data = {"query": query.strip(), "search_depth": "basic", "max_results": count,
                "topic": "general", "include_answer": False, "include_raw_content": False,
                "include_images": False, "auto_parameters": False}
        if normalized_domains:
            data["include_domains"] = normalized_domains
    elif operation == "extract":
        url = public_url(payload.get("url"), resolve=True)
        data = {"urls": [url], "extract_depth": "basic", "format": "text",
                "include_images": False}
    else:
        raise ValueError("Неизвестная операция Tavily.")
    request = urllib.request.Request("https://api.tavily.com/" + operation,
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "AdvertisingEstimator/" + VERSION},
        method="POST")
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=TAVILY_TIMEOUT) as response:
            raw = response.read(MAX_UPSTREAM_BYTES + 1)
            if len(raw) > MAX_UPSTREAM_BYTES:
                raise APIError(502, "Ответ Tavily превышает допустимый размер. Уточните запрос.")
        upstream = json.loads(raw.decode("utf-8"))
        if not isinstance(upstream, dict) or not isinstance(upstream.get("results"), list):
            raise APIError(502, "Tavily вернул ответ неподдерживаемого формата.")
    except urllib.error.HTTPError as exc:
        # Do not read or expose the upstream body: it can contain sensitive input.
        if exc.code in {401, 403}:
            raise APIError(502, "Tavily отклонил авторизацию. Проверьте ключ и доступ в кабинете Tavily.") from None
        if exc.code in {429, 432, 433}:
            raise APIError(429, "Достигнут лимит Tavily. Проверьте ограничения и баланс в кабинете сервиса.") from None
        raise APIError(502, "Tavily не выполнил запрос. Повторите позднее или уточните параметры.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise APIError(502, "Нет ответа от Tavily. Проверьте подключение к интернету и доступность сервиса.") from None
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise APIError(502, "Tavily вернул ответ неподдерживаемого формата.") from None
    results = []
    for item in upstream["results"][:10]:
        if not isinstance(item, dict):
            continue
        url = _result_url(item.get("url"), key)
        if not url:
            continue
        if operation == "search":
            results.append({"title": _plain(item.get("title"), 500, key), "url": url,
                            "content": _plain(item.get("content"), 12000, key)})
        else:
            raw_content = _plain(item.get("raw_content"), 250001, key)
            results.append({"url": url, "raw_content": raw_content[:250000],
                            "truncated": len(raw_content) > 250000})
    output = {"results": results}
    if operation == "extract":
        failed = upstream.get("failed_results", [])
        output["failed_results"] = [{"url": _result_url(item.get("url"), key),
            "error": "Tavily не смог извлечь содержимое страницы."}
            for item in (failed if isinstance(failed, list) else [])[:10] if isinstance(item, dict)]
        if not results and not output["failed_results"]:
            output["failed_results"] = [{"url": url, "error": "Tavily не вернул содержимое страницы."}]
    return output


class EstimatorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address=("127.0.0.1", 0), root: Path = RESOURCE_ROOT, data_dir=None):
        if address[0] != "127.0.0.1":
            raise ValueError("Сервер доступен только на 127.0.0.1.")
        self.root = Path(root).resolve()
        self.token = secrets.token_urlsafe(32)
        self.data_dir = data_dir
        self._store = None
        self._fns = None
        self._prices = None
        self._monitor = None
        self._stock = None
        self._hub = None
        self.hosted = False
        self._store_lock = threading.RLock()
        self._workers = threading.BoundedSemaphore(16)
        super().__init__(address, EstimatorHandler)
        self.authority = "127.0.0.1:" + str(self.server_port)
        self.origin = "http://" + self.authority

    def store(self):
        with self._store_lock:
            if self._store is None:
                self._store = local_store.LocalStore(self.data_dir)
            return self._store

    def fns(self):
        with self._store_lock:
            if self._fns is None:
                directory=Path(self.data_dir) if self.data_dir is not None else local_store.data_directory()
                self._fns=fns_registry.RegistryService(directory/'fns-documents')
            return self._fns

    def prices(self):
        with self._store_lock:
            if self._prices is None:
                store=self.store()
                self._prices=supplier_prices.SupplierPrices(store,store.directory/'price-documents')
            return self._prices

    def stock(self):
        with self._store_lock:
            if self._stock is None:self._stock=stock.Stock(self.store())
            return self._stock

    def monitor(self):
        with self._store_lock:
            if self._monitor is None:self._monitor=price_monitor.PriceMonitor(self.prices())
            return self._monitor

    def hub(self):
        with self._store_lock:
            if self._hub is None:self._hub=supply_hub.SupplyHub(self.store())
            return self._hub

    def start_background(self):
        directory=Path(self.data_dir) if self.data_dir is not None else local_store.data_directory()
        if (directory/'supply-hub.sqlite').is_file():self.hub().start()
        if (directory/'price-monitor.sqlite').is_file():
            try:
                monitor=self.monitor()
                if any(row['enabled'] for row in monitor.status()['schedules']):monitor.start()
            except ValueError:print('Расписание не запущено: установите дополнения SETUP_FEATURES.')

    def server_close(self):
        if self._monitor:self._monitor.stop()
        if self._hub:self._hub.stop()
        super().server_close()

    def process_request(self, request, client_address):
        if not self._workers.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._workers.release()

    def handle_error(self, request, client_address):
        # Avoid stderr tracebacks containing requests, imported data or credentials.
        pass


class EstimatorHandler(BaseHTTPRequestHandler):
    server_version = "AdvertisingEstimator/" + VERSION
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self._json({"error": "Запрос не поддерживается."}, code)

    def _headers(self, status: int, content_type: str, length: int):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header("Connection", "close")
        self.end_headers()

    def _json(self, value: dict, status: int = 200):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _local_request(self):
        hosts = self.headers.get_all("Host", [])
        if hosts != [self.server.authority]:
            raise APIError(403, "Разрешён только локальный адрес программы.")
        origins = self.headers.get_all("Origin", [])
        if origins and origins != [self.server.origin]:
            raise APIError(403, "Запрос с другого сайта запрещён.")
        if self.headers.get("Sec-Fetch-Site", "") == "cross-site":
            raise APIError(403, "Запрос с другого сайта запрещён.")

    def _post_body(self) -> dict:
        self._local_request()
        tokens = self.headers.get_all("X-Estimator-Token", [])
        if len(tokens) != 1 or not tokens[0].isascii() or not secrets.compare_digest(tokens[0], self.server.token):
            raise APIError(403, "Сеанс недействителен. Откройте страницу программы заново.")
        content_types = self.headers.get_all("Content-Type", [])
        if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
            raise APIError(415, "Нужен запрос в формате application/json.")
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
            raise APIError(400, "Кодирование тела запроса не поддерживается.")
        if len(lengths) != 1 or not lengths[0].isdigit():
            raise APIError(411, "Не указан корректный размер запроса.")
        length = int(lengths[0])
        limit = 64*1024*1024 if self.path == '/api/workspace/restore' else 16*1024*1024 if self.path in ('/api/xlsx/read','/api/prices/read','/api/vector/measure','/api/export-docx','/api/hub/refresh') else MAX_BODY_BYTES
        if length > limit:
            raise APIError(413, "Размер запроса превышает допустимый предел.")
        if length == 0:
            raise APIError(400, "Нужно JSON-содержимое запроса.")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise APIError(400, "Запрос получен не полностью.")
        def invalid_constant(value):
            raise ValueError("Некорректное число JSON.")
        try:
            body = json.loads(raw.decode("utf-8-sig"), parse_constant=invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise APIError(400, "Не удалось прочитать JSON. Проверьте формат данных.") from None
        if not isinstance(body, dict):
            raise APIError(400, "В запросе ожидается объект JSON.")
        return body

    def do_GET(self):
        try:
            self._local_request()
            if self.path == "/api/bootstrap":
                value = {"token": self.server.token, "version": VERSION}
                for name in ("prices", "sources", "conditions", "websites"):
                    value[name] = json.loads((self.server.root / "data" / (name + ".json")).read_text(encoding="utf-8"))
                value["demo"] = json.loads((self.server.root / "examples" / "demo.json").read_text(encoding="utf-8"))
                self._json(value)
                return
            paths = {"/": "web/index.html", "/index.html": "web/index.html",
                     "/app.js": "web/app.js", "/style.css": "web/style.css", "/workspace.js": "web/workspace.js",
                     "/customer.js": "web/customer.js", "/monitor.js": "web/monitor.js", "/geometry.js": "web/geometry.js", "/advanced.js": "web/advanced.js",
                     "/ergonomic.js": "web/ergonomic.js", "/ergonomic.css": "web/ergonomic.css",
                     "/supply_hub.js": "web/supply_hub.js", "/supply_hub.css": "web/supply_hub.css",
                     "/guide.js": "web/guide.js", "/guide.json": "web/guide.json",
                     "/price_updates.js": "web/price_updates.js", "/price_updates.css": "web/price_updates.css",
                     "/web/app.js": "web/app.js", "/web/style.css": "web/style.css"}
            relative = paths.get(self.path)
            if not relative:
                raise APIError(404, "Страница не найдена.")
            path = (self.server.root / relative).resolve()
            if self.server.root not in path.parents or not path.is_file():
                raise APIError(404, "Файл интерфейса не найден. Распакуйте архив полностью.")
            body = path.read_bytes()
            mime = {".js": "text/javascript", ".html": "text/html", ".css": "text/css", ".json": "application/json"}.get(path.suffix, "application/octet-stream")
            self._headers(200, mime + "; charset=utf-8", len(body))
            self.wfile.write(body)
        except APIError as exc:
            self._json({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._json({"error": "Не удалось загрузить файлы программы. Распакуйте весь архив в одну папку."}, 500)

    def do_POST(self):
        try:
            body = self._post_body()
            if self.path == "/api/validate-project":
                result = local_store.normalize_project(body)
            elif self.path == '/api/secrets/status':
                result = secret_store.status()
            elif self.path == '/api/secrets/save':
                result = secret_store.save(body.get('service'),body.get('key'))
            elif self.path == '/api/secrets/delete':
                result = secret_store.remove(body.get('service'))
            elif self.path == '/api/stock/status':
                result = self.server.stock().status()
            elif self.path == '/api/stock/move':
                result = self.server.stock().move(body)
            elif self.path == '/api/catalog/suggest':
                result = catalog_match.suggest(self.server.store(),body)
            elif self.path == '/api/backup/full':
                data=full_backup.create(self.server.store(),self.server._stock,self.server._monitor)
                result={'content':base64.b64encode(data).decode('ascii')}
            elif self.path == '/api/export-docx':
                template=file_formats.decode_file(body['template'],2*1024*1024) if body.get('template') else None
                data=advanced_documents.export_docx(body.get('project'),body.get('profile'),template)
                result={'content':base64.b64encode(data).decode('ascii')}
            elif self.path == '/api/docx/template':
                result={'content':base64.b64encode(advanced_documents.default_template()).decode('ascii')}
            elif self.path == '/api/export-editable-xlsx':
                data=advanced_documents.export_editable_xlsx(body.get('project'))
                result={'content':base64.b64encode(data).decode('ascii')}
            elif self.path == '/api/monitor/status':
                result = self.server.monitor().status()
            elif self.path == '/api/monitor/configure':
                result = self.server.monitor().configure(body)
            elif self.path == '/api/monitor/check':
                result = self.server.monitor().check(body.get('source_id'))
            elif self.path == '/api/monitor/review':
                result = self.server.monitor().review(body.get('id'))
            elif self.path == '/api/prices/read':
                result = self.server.prices().read(body)
            elif self.path == '/api/prices/sheet':
                result = self.server.prices().sheet(body.get('run_id'),body.get('sheet'))
            elif self.path == '/api/prices/preview':
                result = self.server.prices().preview(body.get('run_id'),body.get('source'))
            elif self.path == '/api/prices/apply':
                result = self.server.prices().apply(body.get('preview_id'),body.get('keys'),body.get('acknowledged'))
            elif self.path == '/api/prices/document':
                result = self.server.prices().document(body.get('document'))
            elif self.path == '/api/customer/dadata':
                result = fns_registry.dadata_search(body.get('query'),secret_store.effective(body,'dadata'))
            elif self.path == '/api/fns/search':
                result = self.server.fns().search(body.get('query'))
            elif self.path == '/api/fns/search-result':
                result = self.server.fns().search_result(body.get('lookup_id'))
            elif self.path == '/api/fns/extract-start':
                result = self.server.fns().extract_start(body.get('lookup_id'),body.get('choice_id'))
            elif self.path == '/api/fns/extract-result':
                result = self.server.fns().extract_result(body.get('lookup_id'),body.get('choice_id'))
            elif self.path == '/api/fns/saved-document':
                raw,document = self.server.fns().saved_document(body.get('document'))
                result = {'content':base64.b64encode(raw).decode('ascii'),'document':document}
            elif self.path == '/api/workspace/list':
                result = {'items':self.server.store().list(body.get('kind'),body.get('query',''))}
            elif self.path == '/api/workspace/get':
                result = self.server.store().get(body.get('kind'),body.get('id'),body.get('revision'))
            elif self.path == '/api/workspace/save':
                result = self.server.store().save(body.get('kind'),body.get('name'),body.get('data'),body.get('id'),body.get('expected_revision'))
            elif self.path == '/api/workspace/delete':
                with self.server.store().lock:
                    if (self.server.store().directory/'stock.sqlite').exists():self.server.stock().assert_delete_allowed(body.get('kind'),body.get('id'))
                    result = self.server.store().delete(body.get('kind'),body.get('id'),body.get('expected_revision'))
            elif self.path == '/api/workspace/versions':
                result = {'versions':self.server.store().versions(body.get('id'))}
            elif self.path == '/api/workspace/backup':
                result = self.server.store().backup()
            elif self.path == '/api/workspace/restore':
                with self.server.store().lock:
                    if (self.server.store().directory/'stock.sqlite').exists() and any(float(i['on_hand']) or float(i['reserved']) for i in self.server.stock().status()['items']):
                        raise ValueError('Есть складские остатки. Восстанавливайте полную копию в новую папку данных по инструкции.')
                    result = self.server.store().restore(body)
            elif self.path == '/api/workspace/import-preview':
                result = {'changes':self.server.store().import_preview(body.get('rows'))}
            elif self.path == '/api/workspace/import-apply':
                result = self.server.store().import_apply(body.get('changes'))
            elif self.path == '/api/hub/status':
                result=self.server.hub().status()
            elif self.path == '/api/hub/save':
                result=self.server.hub().save(body)
                if body.get('enabled',True):self.server.hub().start()
            elif self.path == '/api/hub/delete':
                result=self.server.hub().remove(body.get('id'))
            elif self.path == '/api/hub/refresh':
                raw=file_formats.decode_file(body['content']) if body.get('content') else None
                result=self.server.hub().refresh(body.get('id'),raw=raw)
            elif self.path == '/api/hub/select':
                result=self.server.hub().select(body)
            elif self.path == '/api/layout/mixed':
                result = mixed_layout.layout(body)
            elif self.path == '/api/vector/measure':
                result = vector_geometry.measure(body)
            elif self.path == '/api/template':
                result = production.recipe(body)
            elif self.path == '/api/operation':
                result = production.operation(body)
            elif self.path == '/api/compare-offers':
                result = production.compare_offers(body)
            elif self.path == '/api/actuals':
                result = production.actuals(body)
            elif self.path == '/api/xlsx/read':
                result = file_formats.xlsx_read(body)
            elif self.path == '/api/xlsx/map':
                result = file_formats.map_prices(body)
            elif self.path in {'/api/export-pdf','/api/export-xlsx'}:
                exporter=file_formats.export_pdf if self.path.endswith('pdf') else file_formats.export_xlsx
                kwargs={'root':self.server.root} if self.path.endswith('pdf') else {}
                data=exporter(body.get('project'),body.get('profile'),body.get('client_view',False),**kwargs)
                result={'content':base64.b64encode(data).decode('ascii')}
            elif self.path == '/api/assistant/models':
                result = draft_assistant.models()
            elif self.path == '/api/assistant/draft':
                result = draft_assistant.extract(body)
            elif self.path == "/api/calculate":
                result = engine.calculate(local_store.normalize_project(body))
            elif self.path == "/api/geometry":
                result = engine.geometry(body)
            elif self.path == "/api/import-csv":
                text = body.get("text")
                if not isinstance(text, str):
                    raise ValueError("Нужно текстовое содержимое CSV в кодировке UTF-8.")
                if len(text.encode("utf-8")) > MAX_CSV_BYTES:
                    raise APIError(413, "CSV превышает допустимый размер 2 МБ.")
                rows = reporting.import_csv(text)
                if len(rows) > 500:
                    raise ValueError("CSV должен содержать не более 500 строк сметы.")
                result = {"rows": rows}
            elif self.path == "/api/export-html":
                if type(body.get("client_view", False)) is not bool:
                    raise ValueError("Параметр client_view должен быть логическим значением.")
                result = {"html": reporting.export_html(body.get("project"), client_view=body.get("client_view", False))}
            elif self.path == "/api/export-csv":
                result = {"text": reporting.export_csv(body)}
            elif self.path in {"/api/tavily/search", "/api/tavily/extract"}:
                result = tavily_request(self.path.rsplit("/", 1)[1], {**body,"api_key":secret_store.effective(body,"tavily")})
            elif self.path == "/api/shutdown":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                raise APIError(404, "Операция не найдена.")
            self._json(result)
        except APIError as exc:
            self._json({"error": str(exc)}, exc.status)
        except fns_registry.FNSFailure as exc:
            self._json({'error':str(exc),'code':exc.code},503)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except (TimeoutError, socket.timeout):
            self._json({"error": "Превышено время ожидания запроса."}, 408)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._json({"error": "Не удалось выполнить операцию. Проверьте введённые данные и комплектность программы."}, 500)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Локальный сметчик рекламы и полиграфии")
    parser.add_argument("--no-browser", action="store_true", help="Не открывать браузер автоматически")
    parser.add_argument("--port", type=int, default=0, help="Локальный порт; 0 выбирает свободный порт")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("Номер порта должен быть от 0 до 65535.")
    try:
        server = EstimatorServer(("127.0.0.1", args.port))
    except OSError:
        print("Cannot start the local server. Close another copy or choose a different --port.")
        return 1
    server.start_background()
    print("Advertising Estimator " + VERSION)
    print("Open: " + server.origin)
    print("Keep this window open. Use the Exit button or Ctrl+C to stop.")
    if not args.no_browser:
        try:
            webbrowser.open(server.origin)
        except Exception:
            print("Open the address above in your browser.")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
