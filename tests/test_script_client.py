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
PDF_PATH = str(Path("resumes") / "Alex_Rivera.pdf")
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
        status = cdp.set_file_input(WS, '[data-ui="resume"] input[type="file"]', PDF_PATH, connect=chrome)
        self.assertEqual(status, {"status": "uploaded"})
        self.assertEqual([item["method"] for item in chrome.sent], ["Runtime.evaluate", "DOM.setFileInputFiles"])
        lookup = chrome.sent[0]["params"]
        self.assertIs(lookup["returnByValue"], False)
        self.assertIn(json.dumps('[data-ui="resume"] input[type="file"]'), lookup["expression"])
        self.assertIn("length === 1", lookup["expression"])
        self.assertEqual(chrome.sent[1]["params"], {"files": [PDF_PATH], "objectId": "obj-7"})
        self.assertTrue(chrome.closed)

    def test_no_single_file_input_needs_the_user_and_sets_nothing(self):
        chrome = FakeChrome(lambda method, params: {"result": {"result": {"type": "object", "subtype": "null"}}})
        status = cdp.set_file_input(WS, 'input[type="file"]', PDF_PATH, connect=chrome)
        self.assertEqual(status, {"status": "needs_manual"})
        self.assertEqual([item["method"] for item in chrome.sent], ["Runtime.evaluate"])

    def test_a_refused_file_selection_raises(self):
        def handler(method, params):
            if method == "DOM.setFileInputFiles":
                return {"error": {"code": -32000, "message": "Not allowed"}}
            return found_input(method, params)

        with self.assertRaisesRegex(cdp.CDPError, "Not allowed"):
            cdp.set_file_input(WS, 'input[type="file"]', PDF_PATH, connect=FakeChrome(handler))

    def test_only_a_loopback_tab_is_accepted(self):
        with self.assertRaises(ValueError):
            cdp.set_file_input("ws://192.0.2.4:9333/devtools/page/T1", "input", PDF_PATH, connect=FakeChrome(found_input))

    def test_evaluate_still_returns_the_script_value(self):
        chrome = FakeChrome(lambda method, params: {"result": {"result": {"type": "object", "value": {"ok": 1}}}})
        self.assertEqual(cdp.evaluate(WS, "({ok: 1})", connect=chrome), {"ok": 1})
        self.assertEqual(chrome.sent[0]["params"]["returnByValue"], True)


FORM_URL = "https://apply.workable.com/acme/j/1/apply/"
OTHER_URL = "https://apply.workable.com/acme/j/2/apply/"


def page_at(href, script=None):
    """A tab whose document is at ``href``; the resume input is obj-7 of that document."""

    def handler(method, params):
        if method == "Runtime.callFunctionOn":
            value = {"type": "string", "value": href} if href else {"type": "object", "subtype": "null"}
            return {"result": {"result": value}}
        if method == "Runtime.evaluate" and params["expression"] == "location.href":
            return {"result": {"result": {"type": "string", "value": href}}}
        if method == "Runtime.evaluate" and script is not None:
            return script(params)
        return found_input(method, params)

    return handler


class PageBoundTests(unittest.TestCase):
    """With ``page``, a script or file reaches only the document of the accepted URL."""

    def accepts(self, href):
        return href == FORM_URL

    def test_the_file_is_set_only_after_the_input_document_is_the_bound_page(self):
        chrome = FakeChrome(page_at(FORM_URL))
        status = cdp.set_file_input(WS, "input", PDF_PATH, connect=chrome, page=self.accepts)
        self.assertEqual(status, {"status": "uploaded"})
        self.assertEqual(
            [item["method"] for item in chrome.sent],
            ["Runtime.evaluate", "Runtime.callFunctionOn", "DOM.setFileInputFiles"],
        )
        check = chrome.sent[1]["params"]
        self.assertEqual(check["objectId"], "obj-7")
        self.assertIn("ownerDocument", check["functionDeclaration"])
        self.assertEqual(chrome.sent[2]["params"]["objectId"], "obj-7")

    def test_an_input_on_another_page_of_the_tab_gets_no_file(self):
        for href in (OTHER_URL, None):
            with self.subTest(href=href):
                chrome = FakeChrome(page_at(href))
                with self.assertRaises(cdp.PageChangedError):
                    cdp.set_file_input(WS, "input", PDF_PATH, connect=chrome, page=self.accepts)
                self.assertNotIn("DOM.setFileInputFiles", [item["method"] for item in chrome.sent])

    def test_a_script_is_not_sent_to_another_page_of_the_tab(self):
        chrome = FakeChrome(page_at(OTHER_URL))
        with self.assertRaises(cdp.PageChangedError):
            cdp.evaluate(WS, "(() => 1)()", connect=chrome, page=self.accepts)
        self.assertEqual([item["params"]["expression"] for item in chrome.sent], ["location.href"])

    def test_a_script_runs_only_while_the_document_is_still_the_checked_page(self):
        chrome = FakeChrome(page_at(FORM_URL, lambda params: {"result": {"result": {"type": "number", "value": 1}}}))
        self.assertEqual(cdp.evaluate(WS, "(() => 1)()", connect=chrome, page=self.accepts), 1)
        guarded = chrome.sent[1]["params"]["expression"]
        self.assertIn("location.href !== " + json.dumps(FORM_URL), guarded)
        self.assertLess(guarded.index("location.href !=="), guarded.index("(() => 1)()"))

    def test_a_navigation_between_the_check_and_the_script_is_a_page_change(self):
        def moved(params):
            text = "Error: " + cdp.PAGE_CHANGED
            return {"result": {"result": {"type": "object"}, "exceptionDetails": {"exception": {"description": text}}}}

        with self.assertRaises(cdp.PageChangedError):
            cdp.evaluate(WS, "(() => 1)()", connect=FakeChrome(page_at(FORM_URL, moved)), page=self.accepts)


class FakeCDP:
    """The cdp module seen by the script client."""

    def __init__(self, pages):
        self.listed, self.evaluated, self.files, self.href = pages, [], [], FORM_URL

    def pages(self, debug_url):
        self.debug_url = debug_url
        return list(self.listed)

    def evaluate(self, ws, expression, timeout=60, page=None):
        self.accepts = page
        if not page(self.href):
            raise cdp.PageChangedError("moved")
        self.evaluated.append((ws, expression))
        return {"fields": []}

    def set_file_input(self, ws, selector, path, timeout=60, page=None):
        self.accepts = page
        if not page(self.href):
            raise cdp.PageChangedError("moved")
        self.files.append((ws, selector, path))
        return {"status": "uploaded"}


TAB = {"id": "tab-1", "url": FORM_URL, "ws": WS}
BOUND = {"tab-1": "https://apply.workable.com/acme/j/1/apply"}


class CDPScriptClientTests(unittest.TestCase):
    def client(self, pages=(TAB,), **options):
        self.cdp = FakeCDP(list(pages))
        return CDPScriptClient("http://127.0.0.1:9333", BOUND.__getitem__, cdp=self.cdp, **options)

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
            CDPScriptClient("http://192.0.2.4:9333", BOUND.__getitem__, cdp=FakeCDP([TAB]))

    def test_the_bound_page_is_the_job_application_url(self):
        client = self.client()
        client.evaluate_script("tab-1", "() => 1", operation="audit")
        self.assertTrue(self.cdp.accepts(FORM_URL))
        self.assertTrue(self.cdp.accepts("https://apply.workable.com/acme/j/1/apply"))
        for href in (OTHER_URL, "https://apply.workable.com/acme/", "about:blank", "", None):
            with self.subTest(href=href):
                self.assertFalse(self.cdp.accepts(href))

    def test_the_resume_is_not_uploaded_to_another_page_in_the_held_tab(self):
        path = self.pdf()
        client = self.client()
        self.cdp.href = OTHER_URL
        with self.assertRaisesRegex(ValueError, "another page"):
            client.upload_file("tab-1", str(path))
        self.assertEqual(self.cdp.files, [])

    def test_scripts_do_not_run_on_another_page_in_the_held_tab(self):
        client = self.client()
        self.cdp.href = OTHER_URL
        for operation in ("audit", "fill"):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ValueError, "another page"):
                    client.evaluate_script("tab-1", "() => 1", operation=operation)
        self.assertEqual(self.cdp.evaluated, [])

    def test_a_tab_without_a_job_binding_is_refused(self):
        client = CDPScriptClient("http://127.0.0.1:9333", {}.__getitem__, cdp=FakeCDP([TAB]))
        with self.assertRaisesRegex(ValueError, "binding"):
            client.upload_file("tab-1", str(self.pdf()))
        self.assertEqual(client.cdp.files, [])


if __name__ == "__main__":
    unittest.main()
