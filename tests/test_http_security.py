"""Regression coverage for hostile sites targeting the real local HTTP handler."""
import http.client
import secrets
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

from paper_pdf_renamer.gui import Handler


class LocalHttpSecurityTests(unittest.TestCase):
    def setUp(self):
        self.mutations = []
        self.payload = b"{}"
        self.mutation_path = "/api/show-window"
        state = SimpleNamespace(show_window=lambda: self.mutations.append("shown"))
        handler = type("TestHandler", (Handler,), {"state": state, "log_message": lambda *args: None})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.csrf_token = secrets.token_urlsafe(32)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method="POST", path=None, *, headers=None, body=b"{}"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.putrequest(method, path or self.mutation_path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers if headers is not None else self.valid_headers():
            connection.putheader(name, value)
        connection.endheaders(body if method == "POST" else None)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def valid_headers(self):
        port = self.server.server_port
        return [("Host", f"127.0.0.1:{port}"), ("Origin", f"http://127.0.0.1:{port}"),
                ("X-CSRF-Token", self.server.csrf_token),
                ("Content-Type", "application/json"), ("Content-Length", str(len(self.payload)))]

    def test_foreign_origins_and_rebinding_cannot_read_or_mutate(self):
        for name, value in [("Origin", "https://attacker.example"), ("Origin", "null"),
                            ("Host", f"attacker.example:{self.server.server_port}")]:
            with self.subTest(name=name, value=value):
                headers = [(key, value if key == name else original) for key, original in self.valid_headers()]
                self.assertEqual(self.request(headers=headers, body=b"")[0], 403)
                self.assertEqual(self.request("GET", "/", headers=headers)[0], 403)
        self.assertEqual(self.mutations, [])

    def test_missing_wrong_stale_and_non_ascii_tokens_are_rejected(self):
        headers = [(key, value) for key, value in self.valid_headers() if key != "X-CSRF-Token"]
        self.assertEqual(self.request(headers=headers, body=b"")[0], 403)
        for token in ["wrong-token", "previous-session-token", "é"]:
            with self.subTest(token=token):
                headers = [(key, token if key == "X-CSRF-Token" else value) for key, value in self.valid_headers()]
                self.assertEqual(self.request(headers=headers, body=b"")[0], 403)
        self.assertEqual(self.mutations, [])

    def test_ambiguous_headers_and_unsupported_bodies_are_rejected(self):
        for duplicate in ["Host", "Origin", "X-CSRF-Token", "Content-Length", "Content-Type"]:
            with self.subTest(duplicate=duplicate):
                headers = self.valid_headers()
                headers.append(next(item for item in headers if item[0] == duplicate))
                expected = 403 if duplicate in {"Host", "Origin", "X-CSRF-Token"} else 400 if duplicate == "Content-Length" else 415
                self.assertEqual(self.request(headers=headers, body=b"")[0], expected)
        for name, value, status in [("Content-Type", "text/plain", 415), ("Content-Length", "-1", 400),
                                    ("Content-Length", "1" * 100, 413), ("Content-Length", "1000001", 413)]:
            headers = [(key, value if key == name else original) for key, original in self.valid_headers()]
            self.assertEqual(self.request(headers=headers, body=b"")[0], status)
        headers = self.valid_headers() + [("Transfer-Encoding", "chunked")]
        self.assertEqual(self.request(headers=headers, body=b"")[0], 400)
        self.assertEqual(self.mutations, [])

    def test_browser_and_native_requests_keep_working(self):
        for host in ["127.0.0.1", "localhost"]:
            port = self.server.server_port
            headers = [(key, f"{host}:{port}" if key == "Host" else f"http://{host}:{port}" if key == "Origin" else value)
                       for key, value in self.valid_headers()]
            self.assertEqual(self.request(headers=headers, body=self.payload)[0], 200)
        headers = [(key, value) for key, value in self.valid_headers() if key != "Origin"]
        self.assertEqual(self.request(headers=headers, body=self.payload)[0], 200)
        self.assertEqual(len(self.mutations), 3)

    def test_page_supplies_current_token_without_caching(self):
        status, headers, body = self.request("GET", "/", headers=[("Host", f"127.0.0.1:{self.server.server_port}")])
        self.assertEqual(status, 200)
        self.assertIn(self.server.csrf_token.encode(), body)
        self.assertNotIn(b"__LOCAL_CSRF_TOKEN__", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")


if __name__ == "__main__":
    unittest.main()
