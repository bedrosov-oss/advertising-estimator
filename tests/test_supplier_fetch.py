"""Synthetic network fixtures only. No supplier is queried by these tests."""
from datetime import datetime
import hashlib
import io
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import supplier_fetch as fetch


PUBLIC = '93.184.216.34'
DNS = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (PUBLIC, 443))]


class Response:
    def __init__(self, content=b'price;100\n', status=200, headers=None):
        self.body = io.BytesIO(content)
        self.status = status
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.closed = False
        self.reads = 0

    def getheader(self, name):
        return self.headers.get(name.lower())

    def read1(self, count):
        self.reads += 1
        return self.body.read(count)

    def close(self):
        self.closed = True


class WireSocket:
    def __init__(self, content=b'price;100\n', after_read=None):
        head = b'HTTP/1.1 200 OK\r\nContent-Type: text/csv; charset=utf-8\r\nContent-Length: ' + str(len(content)).encode() + b'\r\nConnection: close\r\n\r\n'
        self.incoming = io.BytesIO(head + content)
        self.connected = None
        self.closed = False
        self.sent = []
        self.timeouts = []
        self.after_read = after_read

    def connect(self, address):
        self.connected = address

    def getpeername(self):
        return self.connected

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, value):
        self.sent.append(value)

    def recv_into(self, buffer):
        if self.closed:
            raise AssertionError('Response socket closed before its body was read')
        count = self.incoming.readinto(buffer)
        if self.after_read:
            self.after_read()
        return count

    def close(self):
        self.closed = True


class SupplierFetchTests(unittest.TestCase):
    def test_blocks_local_credentials_ambiguous_ips_before_network(self):
        bad = ['http://localhost/a', 'http://127.1/a', 'http://2130706433/',
               'http://0x7f.0.0.1/', 'http://0177.0.0.1/', 'http://192.168.1.1/',
               'http://10.0.0.1/', 'http://169.254.169.254/', 'http://[::1]/',
               'http://[::ffff:127.0.0.1]/', 'http://[2002:7f00:1::]/',
               'http://224.0.0.1/', 'http://240.0.0.1/', 'http://192.0.2.1/',
               'https://u:secret@example.com/', 'http://example.com:8080/',
               'http://router.local/', 'http://x.lan/', 'file:///etc/passwd',
               'http://example.com\\@127.0.0.1/', 'http://example.com/a\r\nb',
               'http://[fe80::1%25en0]/', 'http://example.com/%\n0a',
               'http://example.com/\ud800', 'http://example.com/' + 'я' * 1000]
        with patch.object(fetch, '_open_response') as network:
            for value in bad:
                with self.subTest(url=value), self.assertRaises(ValueError):
                    fetch.fetch_public(value)
            network.assert_not_called()

    def test_dns_mixed_public_private_rejected_without_connection(self):
        values = DNS + [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', 443))]
        with patch.object(fetch.socket, 'getaddrinfo', return_value=values), patch.object(fetch.socket, 'socket') as opening:
            with self.assertRaisesRegex(ValueError, 'локальный'):
                fetch.fetch_public('https://price.example.com/file.csv')
            opening.assert_not_called()

    def test_http_pins_ip_keeps_host_and_ignores_environment_proxy(self):
        raw = WireSocket()
        with patch.dict('os.environ', {'HTTP_PROXY': 'http://127.0.0.1:9999', 'HTTPS_PROXY': 'http://127.0.0.1:9999'}), \
             patch.object(fetch.socket, 'getaddrinfo', return_value=DNS) as dns, \
             patch.object(fetch.socket, 'socket', return_value=raw):
            result = fetch.fetch_public('http://price.example.com/items.csv?size=3#fragment')
        self.assertEqual(dns.call_count, 1)
        self.assertEqual(raw.connected, (PUBLIC, 80))
        request = b''.join(raw.sent)
        self.assertIn(b'GET /items.csv?size=3 HTTP/1.1', request)
        self.assertIn(b'Host: price.example.com', request)
        self.assertIn(('User-Agent: AdvertisingEstimator/'+__import__('server').VERSION).encode(), request)
        self.assertNotIn(b'fragment', request)
        self.assertNotIn(b'Authorization:', request)
        self.assertNotIn(b'Cookie:', request)
        self.assertEqual(result['content'], b'price;100\n')
        self.assertEqual(result['content_type'], 'text/csv; charset=utf-8')
        self.assertEqual(result['sha256'], hashlib.sha256(result['content']).hexdigest())
        self.assertIsNotNone(datetime.fromisoformat(result['retrieved_at']).utcoffset())
        self.assertTrue(raw.closed)
        self.assertTrue(all(0 < item <= 20 for item in raw.timeouts))

    def test_https_uses_default_verifying_context_and_original_sni(self):
        raw = WireSocket()
        real_context = ssl.create_default_context()
        context = Mock(wraps=real_context)
        context.wrap_socket.return_value = raw
        with patch.object(fetch.socket, 'getaddrinfo', return_value=DNS) as dns, \
             patch.object(fetch.socket, 'socket', return_value=raw), \
             patch.object(fetch.ssl, 'create_default_context', return_value=context) as create:
            result = fetch.fetch_public('https://price.example.com/list')
        create.assert_called_once_with()
        self.assertTrue(real_context.check_hostname)
        self.assertEqual(real_context.verify_mode, ssl.CERT_REQUIRED)
        context.wrap_socket.assert_called_once_with(raw, server_hostname='price.example.com')
        self.assertEqual(raw.connected, (PUBLIC, 443))
        self.assertEqual(dns.call_count, 1)
        self.assertEqual(result['url'], 'https://price.example.com/list')

    def test_public_ipv6_literal_connects_without_resolving_again(self):
        raw = WireSocket()
        with patch.object(fetch.socket, 'getaddrinfo') as dns, patch.object(fetch.socket, 'socket', return_value=raw):
            result = fetch.fetch_public('http://[2606:4700:4700::1111]/price')
        dns.assert_not_called()
        self.assertEqual(raw.connected, ('2606:4700:4700::1111', 80, 0, 0))
        self.assertIn(b'Host: [2606:4700:4700::1111]', b''.join(raw.sent))
        self.assertTrue(result['content'])

    def test_unicode_url_is_encoded_without_changing_escape_sequences(self):
        parts = fetch._url('https://пример.рф/прайс%20лист.csv?цвет=белый#local')
        self.assertEqual(parts.hostname, 'xn--e1afmkfd.xn--p1ai')
        self.assertIn('%20', parts.path)
        self.assertNotIn('%2520', parts.path)
        self.assertNotIn('прайс', parts.geturl())
        self.assertFalse(parts.fragment)

    def test_relative_redirect_closes_each_response_and_returns_final_url(self):
        first = Response(status=302, headers={'Location': '/latest.csv'})
        second = Response(content=b'sku;price\nA;100')
        connections = [Mock(), Mock()]
        with patch.object(fetch, '_open_response', side_effect=list(zip(connections, [first, second]))) as opened:
            result = fetch.fetch_public('https://price.example.com/old')
        self.assertEqual(opened.call_args_list[1].args[0].geturl(), 'https://price.example.com/latest.csv')
        self.assertEqual(result['url'], 'https://price.example.com/latest.csv')
        self.assertTrue(first.closed and second.closed)
        for connection in connections:
            connection.close.assert_called_once()

    def test_redirect_dns_is_revalidated_before_following(self):
        first_raw = WireSocket()
        first_raw.incoming = io.BytesIO(b'HTTP/1.1 302 Found\r\nLocation: https://other.example.com/prices\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        context = Mock()
        context.wrap_socket.return_value = first_raw
        private = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('10.0.0.1', 443))]
        with patch.object(fetch.socket, 'getaddrinfo', side_effect=[DNS, private]) as dns, \
             patch.object(fetch.socket, 'socket', return_value=first_raw) as sockets, \
             patch.object(fetch.ssl, 'create_default_context', return_value=context):
            with self.assertRaisesRegex(ValueError, 'локальный'):
                fetch.fetch_public('https://price.example.com/old')
        self.assertEqual(dns.call_count, 2)
        sockets.assert_called_once()
        self.assertTrue(first_raw.closed)

    def test_rejects_private_and_downgrade_and_malformed_redirects(self):
        values = ['http://price.example.com/a', 'https://127.0.0.1/a',
                  'https://u:secret@example.com/a', '//localhost/a', '/a\r\nb', 'http://127.1/']
        for location in values:
            first = Response(status=302, headers={'Location': location})
            with self.subTest(location=location), patch.object(fetch, '_open_response', return_value=(Mock(), first)) as opened:
                with self.assertRaises(ValueError):
                    fetch.fetch_public('https://price.example.com/old')
                self.assertEqual(opened.call_count, 1)
                self.assertTrue(first.closed)

    def test_at_most_three_redirects_and_loop_rejected(self):
        responses = [(Mock(), Response(status=302, headers={'Location': '/hop' + str(i)})) for i in range(4)]
        with patch.object(fetch, '_open_response', side_effect=responses) as opened:
            with self.assertRaisesRegex(ValueError, 'перенаправлений'):
                fetch.fetch_public('https://price.example.com/start')
        self.assertEqual(opened.call_count, 4)
        with patch.object(fetch, '_open_response', return_value=(Mock(), Response(status=302, headers={'Location': '/start'}))) as opened:
            with self.assertRaisesRegex(ValueError, 'повторяет'):
                fetch.fetch_public('https://price.example.com/start')
            self.assertEqual(opened.call_count, 1)

    def test_three_redirects_can_finish_successfully(self):
        responses = [(Mock(), Response(status=302, headers={'Location': '/hop' + str(i)})) for i in range(3)]
        responses.append((Mock(), Response(content=b'done')))
        with patch.object(fetch, '_open_response', side_effect=responses) as opened:
            result = fetch.fetch_public('https://price.example.com/start')
        self.assertEqual(opened.call_count, 4)
        self.assertEqual(result['content'], b'done')
        self.assertEqual(result['url'], 'https://price.example.com/hop2')

    def test_size_limit_before_read_and_while_reading(self):
        for response in [Response(headers={'Content-Length': str(fetch.MAX_BYTES + 1)}),
                         Response(content=b'x' * (fetch.MAX_BYTES + 1))]:
            with self.subTest(headers=response.headers), patch.object(fetch, '_open_response', return_value=(Mock(), response)):
                with self.assertRaisesRegex(ValueError, '8 МБ'):
                    fetch.fetch_public('https://price.example.com/file')
            self.assertTrue(response.closed)
        self.assertEqual(response.body.tell(), fetch.MAX_BYTES + 1)

    def test_incomplete_empty_compressed_and_conflicting_response_rejected(self):
        responses = [Response(headers={'Content-Length': '100'}), Response(content=b''),
                     Response(headers={'Content-Encoding': 'gzip'}),
                     Response(headers={'Content-Length': '1, 1'}),
                     Response(headers={'Content-Length': '1', 'Transfer-Encoding': 'chunked'}),
                     Response(headers={'Transfer-Encoding': 'gzip, chunked'}),
                     Response(status=206), Response(headers={'Content-Range': 'bytes 0-3/100'})]
        for response in responses:
            with self.subTest(headers=response.headers, status=response.status), patch.object(fetch, '_open_response', return_value=(Mock(), response)):
                with self.assertRaises(ValueError):
                    fetch.fetch_public('https://price.example.com/file')
            self.assertTrue(response.closed)

    def test_upstream_errors_are_understandable_and_do_not_echo_body(self):
        for status, word in [(401, 'браузере'), (403, 'браузере'), (429, 'частоту'), (404, 'не найден'), (502, 'полный файл')]:
            response = Response(content=b'secret backend body', status=status)
            with self.subTest(status=status), patch.object(fetch, '_open_response', return_value=(Mock(), response)):
                with self.assertRaisesRegex(ValueError, word) as caught:
                    fetch.fetch_public('https://price.example.com/file')
            self.assertNotIn('secret', str(caught.exception))
            self.assertEqual(response.reads, 0)
        with patch.object(fetch, '_open_response', return_value=(Mock(), Response(headers={'cf-mitigated': 'challenge'}))):
            with self.assertRaisesRegex(ValueError, 'капчу'):
                fetch.fetch_public('https://price.example.com/file')

    def test_network_tls_and_timeout_errors_do_not_expose_raw_details(self):
        errors = [(ConnectionRefusedError('secret'), 'подключиться'),
                  (ssl.SSLCertVerificationError('secret'), 'сертификат'),
                  (ssl.SSLError('secret'), 'защищённое'),
                  (TimeoutError('secret'), '20 секунд')]
        for error, word in errors:
            with self.subTest(error=type(error)), patch.object(fetch, '_open_response', side_effect=error):
                with self.assertRaisesRegex(ValueError, word) as caught:
                    fetch.fetch_public('https://price.example.com/file')
            self.assertNotIn('secret', str(caught.exception))

    def test_total_deadline_includes_header_and_body_reads(self):
        clock = [100.0]
        raw = WireSocket(after_read=lambda: clock.__setitem__(0, clock[0] + 21))
        with patch.object(fetch.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(fetch.socket, 'getaddrinfo', return_value=DNS), \
             patch.object(fetch.socket, 'socket', return_value=raw):
            with self.assertRaisesRegex(ValueError, '20 секунд'):
                fetch.fetch_public('http://price.example.com/file')
        self.assertTrue(raw.closed)


if __name__ == '__main__':
    unittest.main()
