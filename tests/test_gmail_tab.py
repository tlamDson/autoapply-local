"""Gmail tab in the tool's own Chrome profile, driven over loopback CDP.

Offline: CDP is a fake object or an in-process socket pair; Chrome is never
spawned and no mailbox is read.
"""

import base64
import hashlib
import json
import socket
import struct
import threading
from pathlib import Path
from unittest.mock import Mock

from test_support import ReleaseCase


def mail(thread, subject, name="Northwind Labs Recruiting", email="no-reply@northwind.example", snippet=""):
    return {"thread": thread, "from": name, "email": email, "subject": subject, "snippet": snippet,
            "date": "Tue, Nov 10, 2026, 9:14 AM", "unread": False}


class FakeCDP:
    """Pages and evaluate results scripted per test."""

    def __init__(self, pages=(), results=()):
        self.page_list = [dict(page) for page in pages]
        self.results = list(results)
        self.opened, self.expressions = [], []

    def pages(self, debug_url):
        return [dict(page) for page in self.page_list]

    def open_page(self, debug_url, url):
        page = {"id": f"T{len(self.page_list) + 1}", "url": url, "ws": f"ws://127.0.0.1:9333/devtools/page/T{len(self.page_list) + 1}"}
        self.page_list.append(page)
        self.opened.append(url)
        return dict(page)

    def evaluate(self, ws_url, expression, timeout=60):
        self.expressions.append((ws_url, expression))
        result = self.results.pop(0)
        return result(expression) if callable(result) else result


GMAIL = {"id": "G1", "url": "https://mail.google.com/mail/u/0/#inbox", "ws": "ws://127.0.0.1:9333/devtools/page/G1"}


class Tab(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.gt = self.module("gmail_tab")

    def tab(self, cdp):
        return self.gt.GmailTab("http://127.0.0.1:9333", cdp=cdp)

    def test_non_loopback_debug_url_is_refused(self):
        with self.assertRaises(ValueError):
            self.gt.GmailTab("http://192.0.2.10:9333", cdp=FakeCDP())

    def test_existing_gmail_tab_is_reused(self):
        cdp = FakeCDP([{"id": "A", "url": "about:blank", "ws": "ws://127.0.0.1:9333/devtools/page/A"}, GMAIL])
        self.assertEqual(self.tab(cdp).page()["id"], "G1")
        self.assertEqual(cdp.opened, [])

    def test_missing_gmail_tab_is_opened(self):
        cdp = FakeCDP([{"id": "A", "url": "about:blank", "ws": "ws://127.0.0.1:9333/devtools/page/A"}])
        self.assertTrue(self.tab(cdp).page()["url"].startswith("https://mail.google.com/"))
        self.assertEqual(cdp.opened, ["https://mail.google.com/mail/u/0/"])

    def test_sign_in_page_is_not_signed_in(self):
        cdp = FakeCDP([{"id": "S", "url": "https://accounts.google.com/v3/signin/identifier?continue=x",
                        "ws": "ws://127.0.0.1:9333/devtools/page/S"}])
        self.assertFalse(self.tab(cdp).signed_in())
        self.assertEqual(cdp.expressions, [])

    def test_mail_list_present_means_signed_in(self):
        cdp = FakeCDP([GMAIL], [True])
        self.assertTrue(self.tab(cdp).signed_in())
        self.assertIn("div[role=main]", cdp.expressions[0][1])

    def test_search_runs_the_adapter_in_the_gmail_tab(self):
        cdp = FakeCDP([GMAIL], [{"q": "q", "page": 2, "empty": False, "rows": [mail("1", "Thank you for applying")]}])
        result = self.tab(cdp).search("in:anywhere newer_than:2d", page=2)
        self.assertEqual(result["rows"][0]["thread"], "1")
        ws, expression = cdp.expressions[0]
        self.assertEqual(ws, GMAIL["ws"])
        self.assertIn("globalThis.AutoApplyGmail.search(window, \"in:anywhere newer_than:2d\"", expression)
        self.assertIn('"page": 2', expression)
        self.assertNotIn(".click(", expression)

    def test_history_pages_until_an_empty_page(self):
        pages = [
            {"page": 1, "empty": False, "rows": [mail("1", "a"), mail("2", "b")]},
            {"page": 2, "empty": False, "rows": [mail("3", "c")]},
            {"page": 3, "empty": True, "rows": []},
        ]
        cdp = FakeCDP([GMAIL], pages)
        seen = []
        rows = self.tab(cdp).history(progress=seen.append)
        self.assertEqual([row["thread"] for row in rows], ["1", "2", "3"])
        self.assertEqual(seen, [1, 2, 3])
        first = cdp.expressions[0][1]
        self.assertIn("newer_than:183d", first)

    def test_history_stops_when_gmail_repeats_the_last_page(self):
        same = {"page": 1, "empty": False, "rows": [mail("1", "a")]}
        cdp = FakeCDP([GMAIL], [same, {**same, "page": 2}, {"page": 3, "empty": True, "rows": []}])
        rows = self.tab(cdp).history()
        self.assertEqual([row["thread"] for row in rows], ["1"])
        self.assertEqual(len(cdp.expressions), 2)

    def test_history_caps_pages(self):
        results = [{"page": index, "empty": False, "rows": [mail(str(index), "x")]} for index in range(1, 10)]
        cdp = FakeCDP([GMAIL], results)
        rows = self.tab(cdp).history(max_pages=3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(cdp.expressions), 3)

    def test_confirmation_search_returns_the_matching_company_mail(self):
        rows = [
            mail("9", "Verify your candidate account", snippet="security code"),
            mail("8", "Thank you for applying", name="Contoso Careers", email="careers@contoso.example"),
            mail("7", "Thank you for applying to Northwind Labs"),
        ]
        cdp = FakeCDP([GMAIL], [{"page": 1, "empty": False, "rows": rows}])
        hit = self.tab(cdp).find_confirmation("Northwind Labs", days=3)
        self.assertEqual(hit["thread"], "7")
        expression = cdp.expressions[0][1]
        self.assertIn("newer_than:3d", expression)
        self.assertIn("Northwind Labs", expression)

    def test_confirmation_search_without_match_is_none(self):
        cdp = FakeCDP([GMAIL], [{"page": 1, "empty": True, "rows": []}])
        self.assertIsNone(self.tab(cdp).find_confirmation("Northwind Labs"))


class Session(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.gt = self.module("gmail_tab")

    def test_open_launches_lane_one_of_the_clean_profile_and_opens_gmail(self):
        launches = []

        def launcher(root, **kwargs):
            launches.append((Path(root), kwargs))
            return [{"lane": 1, "profile_dir": str(Path(root) / "chrome" / "lane-1"), "debug_url": "http://127.0.0.1:9333"}]

        cdp = FakeCDP([{"id": "A", "url": "about:blank", "ws": "ws://127.0.0.1:9333/devtools/page/A"}], [True, {"page": 1, "empty": True, "rows": []}])
        session = self.gt.GmailSession(self.root, memory_mb=8192, launcher=launcher, cdp=cdp)
        profile = session.open()
        self.assertEqual(Path(profile), self.root / "chrome" / "lane-1")
        self.assertEqual(launches[0][1]["lanes"], 1)
        self.assertEqual(launches[0][1]["available_memory_mb"], 8192)
        self.assertEqual(cdp.opened, ["https://mail.google.com/mail/u/0/"])
        self.assertTrue(session.signed_in())
        self.assertEqual(session.history(), [])

    def test_browser_lanes_live_under_chrome(self):
        browser = self.module("browser")
        spawn = Mock(return_value=Mock(pid=4242))
        lane = browser.launch_chrome(self.root, executable=Path(__file__), available_memory_mb=8192, spawn=spawn)[0]
        self.assertEqual(Path(lane["profile_dir"]), (self.root / "chrome" / "lane-1").resolve())


class Websocket(ReleaseCase):
    """Minimal RFC 6455 client against a scripted in-process peer."""

    def setUp(self):
        super().setUp()
        self.cdp = self.module("cdp")

    @staticmethod
    def _read_frame(peer):
        head = peer.recv(2)
        length = head[1] & 0x7F
        masked = head[1] & 0x80
        if length == 126:
            length = struct.unpack(">H", peer.recv(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", peer.recv(8))[0]
        mask = peer.recv(4) if masked else b""
        data = b""
        while len(data) < length:
            data += peer.recv(length - len(data))
        return bool(masked), bytes(byte ^ mask[index % 4] for index, byte in enumerate(data)) if masked else data

    @staticmethod
    def _frame(text):
        data = text.encode()
        if len(data) < 126:
            return bytes([0x81, len(data)]) + data
        return bytes([0x81, 126]) + struct.pack(">H", len(data)) + data

    def peer(self, reply):
        client, server = socket.socketpair()
        seen = {}

        def serve():
            request = b""
            while b"\r\n\r\n" not in request:
                request += server.recv(1024)
            seen["request"] = request.decode()
            key = next(line.split(":", 1)[1].strip() for line in seen["request"].split("\r\n")
                       if line.lower().startswith("sec-websocket-key"))
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            server.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                            f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            seen["masked"], body = self._read_frame(server)
            seen["message"] = json.loads(body)
            server.sendall(self._frame(json.dumps({"method": "Runtime.consoleAPICalled", "params": {}})))
            server.sendall(self._frame(json.dumps(reply(seen["message"]["id"]))))

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(server.close)
        return (lambda address, timeout=None: client), seen, thread

    def test_evaluate_returns_the_value_by_value(self):
        connect, seen, thread = self.peer(lambda ident: {"id": ident, "result": {"result": {"type": "object", "value": {"rows": [1, 2]}}}})
        value = self.cdp.evaluate("ws://127.0.0.1:9333/devtools/page/G1", "1 + 1", connect=connect)
        thread.join(5)
        self.assertEqual(value, {"rows": [1, 2]})
        self.assertTrue(seen["masked"])
        self.assertIn("GET /devtools/page/G1 HTTP/1.1", seen["request"])
        self.assertEqual(seen["message"]["method"], "Runtime.evaluate")
        self.assertEqual(seen["message"]["params"]["expression"], "1 + 1")
        self.assertTrue(seen["message"]["params"]["awaitPromise"])
        self.assertTrue(seen["message"]["params"]["returnByValue"])

    def test_long_expression_uses_extended_length(self):
        connect, seen, thread = self.peer(lambda ident: {"id": ident, "result": {"result": {"type": "boolean", "value": True}}})
        expression = "x" * 70000
        self.assertIs(self.cdp.evaluate("ws://127.0.0.1:9333/devtools/page/G1", expression, connect=connect), True)
        thread.join(5)
        self.assertEqual(seen["message"]["params"]["expression"], expression)

    def test_script_exception_raises(self):
        connect, _, thread = self.peer(lambda ident: {"id": ident, "result": {
            "result": {"type": "object"}, "exceptionDetails": {"text": "Uncaught", "exception": {"description": "Error: boom"}}}})
        with self.assertRaises(self.cdp.CDPError) as raised:
            self.cdp.evaluate("ws://127.0.0.1:9333/devtools/page/G1", "boom()", connect=connect)
        thread.join(5)
        self.assertIn("boom", str(raised.exception))

    def test_non_loopback_websocket_is_refused(self):
        with self.assertRaises(ValueError):
            self.cdp.evaluate("ws://192.0.2.10:9333/devtools/page/G1", "1", connect=Mock())

    def test_pages_and_open_use_the_loopback_json_endpoints(self):
        calls = []

        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(self.body).encode()

        def opener(request, timeout=None):
            calls.append((request.get_method(), request.full_url))
            if request.full_url.endswith("/json/list"):
                return Response([
                    {"id": "G1", "type": "page", "url": "https://mail.google.com/mail/u/0/", "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/page/G1"},
                    {"id": "W", "type": "service_worker", "url": "https://mail.google.com/sw.js"},
                ])
            return Response({"id": "N", "type": "page", "url": "https://mail.google.com/mail/u/0/", "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/page/N"})

        pages = self.cdp.pages("http://127.0.0.1:9333", opener=opener)
        self.assertEqual(pages, [{"id": "G1", "url": "https://mail.google.com/mail/u/0/", "ws": "ws://127.0.0.1:9333/devtools/page/G1"}])
        opened = self.cdp.open_page("http://127.0.0.1:9333", "https://mail.google.com/mail/u/0/", opener=opener)
        self.assertEqual(opened["id"], "N")
        self.assertEqual(calls[1][0], "PUT")
        self.assertTrue(calls[1][1].startswith("http://127.0.0.1:9333/json/new?https://mail.google.com/mail/u/0/"))
        with self.assertRaises(ValueError):
            self.cdp.pages("http://192.0.2.10:9333", opener=opener)
