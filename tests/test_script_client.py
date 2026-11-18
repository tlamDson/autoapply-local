"""The script pass over Chrome DevTools, with no model call (plan aa-16).

``cdp.set_file_input`` sets the selected PDF on the one file input a selector
names, through the DevTools protocol (DOM.setFileInputFiles), the same native
file selection a user makes; it never fakes an attachment in JavaScript.
``CDPScriptClient`` runs a script driver's audit, fill and upload against the
held tab directly. Chrome is a fake socket speaking RFC 6455; nothing here
opens a network connection.
"""

import base64
import hashlib
import json
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))

from autoapply_local import cdp  # noqa: E402
from autoapply_local.script_client import CDPScriptClient  # noqa: E402

WS = "ws://127.0.0.1:9333/devtools/page/T1"
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FakeChrome:
    """One DevTools WebSocket: answers each command with ``handler(method, params)``."""

    def __init__(self, handler):
        self.handler, self.sent, self.out, self.open, self.closed = handler, [], b"", False, False

    def __call__(self, address, timeout):
        self.address = address
        return self

    def settimeout(self, timeout):
        pass

    def close(self):
        self.closed = True

    def recv(self, count):
        chunk, self.out = self.out[:count], self.out[count:]
        return chunk

    def sendall(self, data):
        if not self.open:
            key = re.search(rb"Sec-WebSocket-Key: (\S+)", data).group(1)
            accept = base64.b64encode(hashlib.sha1(key + GUID).digest())
            self.out += b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
            self.open = True
            return
        length, offset = data[1] & 0x7F, 2
        if length == 126:
            length, offset = struct.unpack(">H", data[2:4])[0], 4
        elif length == 127:
            length, offset = struct.unpack(">Q", data[2:10])[0], 10
        mask = data[offset : offset + 4]
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data[offset + 4 : offset + 4 + length]))
        message = json.loads(payload)
        self.sent.append(message)
        body = json.dumps({"id": message["id"], **self.handler(message["method"], message["params"])}).encode()
        if len(body) < 126:
            header = struct.pack(">BB", 0x81, len(body))
        elif len(body) < 1 << 16:
            header = struct.pack(">BBH", 0x81, 126, len(body))
        else:
            header = struct.pack(">BBQ", 0x81, 127, len(body))
        self.out += header + body


def found_input(method, params):
    if method == "Runtime.evaluate":
        return {"result": {"result": {"type": "object", "subtype": "node", "objectId": "obj-7"}}}
    return {"result": {}}


class SetFileInputTests(unittest.TestCase):
    def test_sets_the_pdf_on_the_one_input_the_selector_names(self):
        chrome = FakeChrome(found_input)
        status = cdp.set_file_input(WS, '[data-ui="resume"] input[type="file"]', "C:/r/Alex.pdf", connect=chrome)
        self.assertEqual(status, {"status": "uploaded"})
        self.assertEqual([item["method"] for item in chrome.sent], ["Runtime.evaluate", "DOM.setFileInputFiles"])
        lookup = chrome.sent[0]["params"]
        self.assertIs(lookup["returnByValue"], False)
        self.assertIn(json.dumps('[data-ui="resume"] input[type="file"]'), lookup["expression"])
        self.assertIn("length === 1", lookup["expression"])
        self.assertEqual(chrome.sent[1]["params"], {"files": ["C:/r/Alex.pdf"], "objectId": "obj-7"})
        self.assertTrue(chrome.closed)

    def test_no_single_file_input_needs_the_user_and_sets_nothing(self):
        chrome = FakeChrome(lambda method, params: {"result": {"result": {"type": "object", "subtype": "null"}}})
        status = cdp.set_file_input(WS, 'input[type="file"]', "C:/r/Alex.pdf", connect=chrome)
        self.assertEqual(status, {"status": "needs_manual"})
        self.assertEqual([item["method"] for item in chrome.sent], ["Runtime.evaluate"])

    def test_a_refused_file_selection_raises(self):
        def handler(method, params):
            if method == "DOM.setFileInputFiles":
                return {"error": {"code": -32000, "message": "Not allowed"}}
            return found_input(method, params)

        with self.assertRaisesRegex(cdp.CDPError, "Not allowed"):
            cdp.set_file_input(WS, 'input[type="file"]', "C:/r/Alex.pdf", connect=FakeChrome(handler))

    def test_only_a_loopback_tab_is_accepted(self):
        with self.assertRaises(ValueError):
            cdp.set_file_input("ws://192.0.2.4:9333/devtools/page/T1", "input", "C:/r/Alex.pdf", connect=FakeChrome(found_input))

    def test_evaluate_still_returns_the_script_value(self):
        chrome = FakeChrome(lambda method, params: {"result": {"result": {"type": "object", "value": {"ok": 1}}}})
        self.assertEqual(cdp.evaluate(WS, "({ok: 1})", connect=chrome), {"ok": 1})
        self.assertEqual(chrome.sent[0]["params"]["returnByValue"], True)


class FakeCDP:
    """The cdp module seen by the script client."""

    def __init__(self, pages):
        self.listed, self.evaluated, self.files = pages, [], []

    def pages(self, debug_url):
        self.debug_url = debug_url
        return list(self.listed)

    def evaluate(self, ws, expression, timeout=60):
        self.evaluated.append((ws, expression))
        return {"fields": []}

    def set_file_input(self, ws, selector, path, timeout=60):
        self.files.append((ws, selector, path))
        return {"status": "uploaded"}


TAB = {"id": "tab-1", "url": "https://apply.workable.com/acme/j/1/apply/", "ws": WS}


class CDPScriptClientTests(unittest.TestCase):
    def client(self, pages=(TAB,), **options):
        self.cdp = FakeCDP(list(pages))
        return CDPScriptClient("http://127.0.0.1:9333", cdp=self.cdp, **options)

    def pdf(self, data=b"%PDF-1.4\n% synthetic\n"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "Alex_Rivera.pdf"
        path.write_bytes(data)
        return path

    def test_evaluates_the_driver_function_in_the_held_tab(self):
        client = self.client()
        result = client.evaluate_script("tab-1", "async () => { return {fields: []}; }", operation="audit")
        self.assertEqual(result, {"fields": []})
        self.assertEqual(self.cdp.evaluated, [(WS, "(async () => { return {fields: []}; })()")])
        self.assertEqual(self.cdp.debug_url, "http://127.0.0.1:9333")

    def test_only_audit_and_fill_scripts_run(self):
        with self.assertRaises(ValueError):
            self.client().evaluate_script("tab-1", "() => 1", operation="submit")
        self.assertEqual(self.cdp.evaluated, [])

    def test_a_closed_or_ambiguous_tab_is_refused(self):
        for pages in ((), (TAB, dict(TAB))):
            with self.subTest(count=len(pages)):
                with self.assertRaisesRegex(ValueError, "closed or ambiguous"):
                    self.client(pages).evaluate_script("tab-1", "() => 1", operation="audit")

    def test_upload_sets_the_resolved_pdf_on_the_driver_resume_input(self):
        path = self.pdf()
        client = self.client(resume_input='[data-ui="resume"] input[type="file"]')
        self.assertEqual(client.upload_file("tab-1", str(path)), {"status": "uploaded"})
        self.assertEqual(self.cdp.files, [(WS, '[data-ui="resume"] input[type="file"]', str(path.resolve()))])

    def test_without_a_driver_selector_the_page_must_have_one_file_input(self):
        path = self.pdf()
        self.client().upload_file("tab-1", str(path))
        self.assertEqual(self.cdp.files[0][1], 'input[type="file"]')

    def test_only_a_pdf_is_uploaded(self):
        path = self.pdf(b"not a pdf")
        with self.assertRaisesRegex(ValueError, "PDF"):
            self.client().upload_file("tab-1", str(path))
        self.assertEqual(self.cdp.files, [])

    def test_only_the_local_chrome_is_accepted(self):
        with self.assertRaises(ValueError):
            CDPScriptClient("http://192.0.2.4:9333", cdp=FakeCDP([TAB]))


if __name__ == "__main__":
    unittest.main()
