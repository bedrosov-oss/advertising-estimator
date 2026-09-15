"""Local HTTP integration/security checks; Tavily is mocked, never contacted."""
from __future__ import annotations

import http.client
import io
import json
import socket
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


ROOT = Path(__file__).resolve().parents[1]


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.EstimatorServer(root=ROOT)
        cls.worker = threading.Thread(target=cls.httpd.serve_forever,
                                      kwargs={"poll_interval": 0.05}, daemon=True)
        cls.worker.start()
        cls.demo = json.loads((ROOT / "examples" / "demo.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.worker.join(timeout=2)

    def request(self, path="/api/bootstrap", *, method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        request_headers = {"Origin": self.httpd.origin}
        if method == "POST":
            request_headers.update({"X-Estimator-Token": self.httpd.token, "Content-Type": "application/json"})
        if headers:
            request_headers.update(headers)
        if body is not None and not isinstance(body, (str, bytes)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        status, response_headers = response.status, dict(response.getheaders())
        connection.close()
        content = json.loads(raw.decode("utf-8")) if raw and "application/json" in response_headers.get("Content-Type", "") else raw
        return status, content, response_headers

    def test_bootstrap_uses_packaged_dataset_and_random_token(self):
        status, data, headers = self.request()
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(data["token"]), 32)
        self.assertEqual(data["version"], __import__("server").VERSION)
        self.assertEqual(data["demo"], self.demo)
        self.assertGreater(len(data["prices"]), 0)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_incomplete_real_demo_keeps_unknown_customer_total(self):
        status, data, _ = self.request("/api/calculate", method="POST", body=self.demo)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["totals"]["direct"], "12130.00")
        self.assertIsNone(data["totals"]["total"])
        self.assertFalse(data["complete"])

    def test_validate_project_normalizes_missing_sections_and_discards_unknown_fields(self):
        for payload in [{}, {"unexpected": "discard", "project": {"title": "Импорт", "extra": "discard"},
                             "settings": {"extra": "discard"}, "rows": [{"name": "ПВХ", "extra": "discard"}]}]:
            with self.subTest(payload=payload):
                status, data, _ = self.request("/api/validate-project", method="POST", body=payload)
                self.assertEqual(status, 200, data)
                self.assertEqual(set(data), {"schema_version", "project", "rows", "settings", "extensions"})
                self.assertEqual(data["schema_version"], 1)
                self.assertEqual(data["settings"]["tax_mode"], "unknown")
                self.assertIn("quantity", data["project"])
                self.assertNotIn("extra", data["project"])
                self.assertNotIn("extra", data["settings"])
                for row in data["rows"]:
                    self.assertNotIn("extra", row)
                    self.assertIn("id", row)

    def test_geometry_delegates_to_real_engine(self):
        status, data, _ = self.request("/api/geometry", method="POST", body={
            "width_mm": "600", "height_mm": "400", "quantity": "20",
            "sheet_width_mm": "2030", "sheet_height_mm": "3050", "edge_mm": "25",
            "gap_mm": "10", "bleed_mm": "0", "waste_percent": "0", "allow_rotate": True})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["pieces_per_sheet"], 21)
        self.assertEqual(data["sheets"], 1)

    def test_export_and_import_use_real_reporting_module(self):
        status, exported, _ = self.request("/api/export-csv", method="POST", body=self.demo)
        self.assertEqual(status, 200, exported)
        status, imported, _ = self.request("/api/import-csv", method="POST", body={"text": exported["text"]})
        self.assertEqual(status, 200, imported)
        self.assertEqual(len(imported["rows"]), len(self.demo["rows"]))
        self.assertEqual(Decimal(imported["rows"][0]["price"]), Decimal(self.demo["rows"][0]["price"]))
        status, report, _ = self.request("/api/export-html", method="POST", body={"project": self.demo, "client_view": True})
        self.assertEqual(status, 200, report)
        self.assertIn("html", report)
        self.assertNotIn("<script", report["html"].lower())

    def test_wrong_host_is_rejected(self):
        status, _, _ = self.request(headers={"Host": "attacker.example"})
        self.assertEqual(status, 403)

    def test_cross_origin_bootstrap_is_rejected(self):
        status, _, _ = self.request(headers={"Origin": "https://attacker.example"})
        self.assertEqual(status, 403)

    def test_cross_site_metadata_is_rejected(self):
        status, _, _ = self.request(headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)

    def test_wrong_token_is_rejected_before_calculation(self):
        status, _, _ = self.request("/api/calculate", method="POST", body=self.demo,
                                    headers={"X-Estimator-Token": "invalid-session"})
        self.assertEqual(status, 403)

    def test_workspace_access_is_blocked_before_storage_for_wrong_origin_or_token(self):
        with patch.object(self.httpd, 'store') as store:
            for headers in [{'X-Estimator-Token':'invalid-session'}, {'Origin':'https://attacker.example'}]:
                for path in ['/api/workspace/save', '/api/workspace/backup']:
                    status, _, _ = self.request(path, method='POST', body={}, headers=headers)
                    self.assertEqual(status, 403)
            store.assert_not_called()

    def test_registry_calls_require_local_origin_and_token_before_network(self):
        with patch.object(self.httpd,'fns') as fns,patch.object(server.fns_registry,'dadata_search') as dadata:
            for headers in [{'X-Estimator-Token':'invalid-session'}, {'Origin':'https://attacker.example'}]:
                for path in ['/api/fns/search','/api/fns/search-result','/api/fns/extract-start','/api/fns/extract-result','/api/fns/saved-document','/api/customer/dadata']:
                    status,_,_=self.request(path,method='POST',body={},headers=headers)
                    self.assertEqual(status,403)
            fns.assert_not_called();dadata.assert_not_called()

    def test_supplier_routes_require_origin_and_token_before_fetch(self):
        with patch.object(self.httpd,'prices') as prices:
            for headers in [{'X-Estimator-Token':'wrong-token'},{'Origin':'https://attacker.example'}]:
                for path in ['/api/prices/read','/api/prices/preview','/api/prices/sheet','/api/prices/apply','/api/prices/document']:
                    status,_,_=self.request(path,method='POST',body={},headers=headers)
                    self.assertEqual(status,403)
            prices.assert_not_called()

    def test_supplier_public_assets_and_failure_response(self):
        for path in ['/price_updates.js','/price_updates.css']:
            status,_,_=self.request(path);self.assertEqual(status,200)
        with patch.object(self.httpd,'prices') as prices:
            prices.return_value.read.side_effect=ValueError('Поставщик вернул HTTP 502')
            status,value,_=self.request('/api/prices/read',method='POST',body={'source':{}})
            self.assertEqual(status,400);self.assertIn('502',value['error'])

    def test_registry_routes_return_classified_errors_without_credentials(self):
        with patch.object(server.fns_registry,'dadata_search',side_effect=server.fns_registry.FNSFailure('key_rejected','Проверьте ключ')):
            status,data,_=self.request('/api/customer/dadata',method='POST',body={'query':'7730588444','api_key':'synthetic-secret'})
            self.assertEqual(status,503);self.assertEqual(data['code'],'key_rejected')
            self.assertNotIn('synthetic-secret',json.dumps(data))
        with patch.object(self.httpd,'fns') as service:
            service.return_value.search.return_value={'status':'pending','lookup_id':'local-lookup'}
            status,data,_=self.request('/api/fns/search',method='POST',body={'query':'7730588444'})
            self.assertEqual(status,200);self.assertEqual(data['lookup_id'],'local-lookup')
            service.return_value.search.assert_called_once_with('7730588444')
        status,_,_=self.request('/customer.js');self.assertEqual(status,200)

    def test_cross_origin_post_is_rejected_even_with_token(self):
        status, _, _ = self.request("/api/calculate", method="POST", body=self.demo,
                                    headers={"Origin": "http://localhost:1"})
        self.assertEqual(status, 403)

    def test_form_post_is_rejected(self):
        status, _, _ = self.request("/api/calculate", method="POST", body="title=x",
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)

    def test_malformed_json_is_reported_without_exception_details(self):
        for body in [b"{", b'{"x": NaN}', b"[]", b'\xff']:
            with self.subTest(body=body):
                status, data, _ = self.request("/api/calculate", method="POST", body=body)
                self.assertEqual(status, 400)
                self.assertEqual(set(data), {"error"})
                self.assertNotIn("Traceback", data["error"])

    def test_oversized_body_rejected_from_declared_length(self):
        status, _, _ = self.request("/api/calculate", method="POST", body=b"{}",
            headers={"Content-Length": str(server.MAX_BODY_BYTES + 1)})
        self.assertEqual(status, 413)

    def test_csv_size_limit(self):
        status, _, _ = self.request("/api/import-csv", method="POST", body={"text": "x" * (server.MAX_CSV_BYTES + 1)})
        self.assertEqual(status, 413)

    def test_static_traversal_and_source_exposure_are_blocked(self):
        for path in ["/../server.py", "/%2e%2e/server.py", "/web/../../server.py", "/server.py", "/data/prices.json"]:
            with self.subTest(path=path):
                status, data, _ = self.request(path)
                self.assertEqual(status, 404)
                self.assertEqual(set(data), {"error"})

    def test_unsupported_method_is_json(self):
        status, data, headers = self.request("/api/calculate", method="PUT", body="{}")
        self.assertEqual(status, 501)
        self.assertEqual(set(data), {"error"})

    def test_invalid_export_flag_rejected(self):
        status, _, _ = self.request("/api/export-html", method="POST", body={"project": self.demo, "client_view": "false"})
        self.assertEqual(status, 400)

    def test_local_extract_does_not_make_a_network_request(self):
        with patch.object(server.urllib.request, "build_opener") as opener:
            status, _, _ = self.request("/api/tavily/extract", method="POST",
                body={"api_key": "test-api-key-123456", "url": "http://127.0.0.1/secret"})
        self.assertEqual(status, 400)
        opener.assert_not_called()


class TavilyTests(unittest.TestCase):
    def test_private_and_ambiguous_urls_blocked(self):
        urls = ["http://localhost/x", "http://192.168.1.1", "https://10.0.0.1",
                "http://169.254.169.254/latest/", "http://[::1]/", "http://127.1/",
                "http://2130706433/", "http://0x7f.0.0.1/", "file:///c:/secret",
                "https://user:password@example.com/", "https://example.com:8080/x",
                "https://router.local/", "https://example.com\\@127.0.0.1/",
                "http://[::ffff:127.0.0.1]/"]
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    server.public_url(url)

    def test_dns_private_alias_blocked(self):
        with patch.object(server.socket, "getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValueError):
                server.public_url("https://price.example.com/", resolve=True)

    def test_public_url_accepted(self):
        self.assertEqual(server.public_url("https://www.forda.ru/list?size=3"), "https://www.forda.ru/list?size=3")

    def test_search_uses_fixed_endpoint_and_bearer_only(self):
        key = "test-api-key-123456"
        response = io.BytesIO(json.dumps({"results": [{"title": "Цена", "url": "https://example.com/product", "content": "Value " + key},
                                                      {"title": "Bad", "url": "javascript:alert(1)", "content": "x"}]}).encode())
        with patch.object(server.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = response
            result = server.tavily_request("search", {"api_key": key, "query": "ПВХ 3 мм", "domains": "forda.ru, doublev.ru", "max_results": 5})
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.tavily.com/search")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + key)
        self.assertNotIn(key.encode(), request.data)
        self.assertEqual(json.loads(request.data)["include_domains"], ["forda.ru", "doublev.ru"])
        self.assertEqual(len(result["results"]), 1)
        self.assertNotIn(key, json.dumps(result))

    def test_auth_error_does_not_echo_upstream_body_or_key(self):
        key = "test-api-key-123456"
        error = urllib.error.HTTPError("https://api.tavily.com/search", 401, key, {}, io.BytesIO(key.encode()))
        with patch.object(server.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(server.APIError) as caught:
                server.tavily_request("search", {"api_key": key, "query": "ПВХ"})
        self.assertNotIn(key, str(caught.exception))
        self.assertEqual(caught.exception.status, 502)

    def test_redirect_is_not_followed(self):
        self.assertIsNone(server.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example.com"))

    def test_extract_failed_results_are_preserved_without_raw_error(self):
        key = "test-api-key-123456"
        response = io.BytesIO(json.dumps({"results": [], "failed_results": [
            {"url": "https://example.com/x", "error": key + " raw server detail"}]}).encode())
        with patch.object(server.socket, "getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]), patch.object(server.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = response
            result = server.tavily_request("extract", {"api_key": key, "url": "https://example.com/x"})
        self.assertEqual(len(result["failed_results"]), 1)
        self.assertNotIn(key, json.dumps(result))

    def test_bind_on_all_network_interfaces_is_rejected(self):
        with self.assertRaises(ValueError):
            server.EstimatorServer(("0.0.0.0", 0))


if __name__ == "__main__":
    unittest.main()
