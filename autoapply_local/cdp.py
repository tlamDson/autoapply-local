"""Smallest Chrome DevTools Protocol client the tool needs: list tabs, open a tab, evaluate a script.

Only loopback endpoints of the tool's own Chrome are accepted. The WebSocket
part is a minimal RFC 6455 client in the standard library, so the terminal
setup can read the Gmail tab before any engine or MCP server exists.
"""

import base64
import hashlib
import json
import os
import socket
import struct
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


PAGE_CHANGED = "autoapply-local: the tab left the checked page"


class CDPError(RuntimeError):
    """Chrome refused the connection or the evaluated script threw."""


class PageChangedError(CDPError):
    """The tab shows another document than the one the caller is bound to; nothing was run or set."""


def _loopback(url, schemes):
    parsed = urlsplit(url)
    if parsed.scheme not in schemes or parsed.hostname not in _LOOPBACK or not parsed.port:
        raise ValueError("Chrome DevTools must be a loopback endpoint with an explicit port")
    return parsed


def wait_ready(debug_url, timeout=20, opener=urlopen, sleep=time.sleep, clock=time.monotonic, interval=0.25):
    """Poll ``/json/version`` until the tool's Chrome answers, and return its version.

    A freshly launched Chrome opens its debugging port some time after the
    process starts; callers that open a tab right after a launch wait here first.
    """
    _loopback(debug_url, ("http",))
    deadline = clock() + timeout
    while True:
        try:
            with opener(Request(debug_url.rstrip("/") + "/json/version"), timeout=2) as response:
                version = json.loads(response.read())
            if str(version.get("Browser", "")).startswith(("Chrome/", "Chromium/", "HeadlessChrome/")):
                return version
        except (OSError, ValueError, AttributeError):
            pass
        if clock() >= deadline:
            raise CDPError(f"Chrome's debugging endpoint did not answer within {timeout} s; close the tool's "
                           "Chrome window and run the command again")
        sleep(interval)


def pages(debug_url, opener=urlopen):
    """Open tabs as ``{"id", "url", "ws"}``."""
    _loopback(debug_url, ("http",))
    with opener(Request(debug_url.rstrip("/") + "/json/list"), timeout=5) as response:
        listed = json.loads(response.read())
    return [
        {"id": str(page["id"]), "url": page.get("url", ""), "ws": page.get("webSocketDebuggerUrl", "")}
        for page in listed
        if page.get("type") == "page"
    ]


def open_page(debug_url, url, opener=urlopen):
    """Open ``url`` in a new tab of the same Chrome and return it."""
    _loopback(debug_url, ("http",))
    request = Request(debug_url.rstrip("/") + "/json/new?" + url, method="PUT")
    with opener(request, timeout=5) as response:
        page = json.loads(response.read())
    return {"id": str(page["id"]), "url": page.get("url", url), "ws": page.get("webSocketDebuggerUrl", "")}


class _Socket:
    def __init__(self, sock):
        self.sock, self.buffer = sock, b""

    def read(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(max(65536, count - len(self.buffer)))
            if not chunk:
                raise CDPError("Chrome closed the DevTools connection")
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def read_until(self, marker):
        while marker not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("Chrome closed the DevTools connection")
            self.buffer += chunk
        head, _, self.buffer = self.buffer.partition(marker)
        return head

    def send(self, opcode, payload):
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = struct.pack(">BB", 0x80 | opcode, 0x80 | length)
        elif length < 1 << 16:
            header = struct.pack(">BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack(">BBQ", 0x80 | opcode, 0x80 | 127, length)
        self.sock.sendall(header + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))

    def message(self):
        parts = []
        while True:
            first, second = self.read(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self.read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self.read(8))[0]
            mask = self.read(4) if second & 0x80 else b""
            data = self.read(length)
            if mask:
                data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
            opcode = first & 0x0F
            if opcode == 0x8:
                raise CDPError("Chrome closed the DevTools connection")
            if opcode == 0x9:
                self.send(0xA, data)
                continue
            if opcode in (0x0, 0x1, 0x2):
                parts.append(data)
                if first & 0x80:
                    return b"".join(parts).decode("utf-8")


def _handshake(stream, parsed):
    key = base64.b64encode(os.urandom(16)).decode()
    path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
    stream.sock.sendall(
        (
            f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
    )
    head = stream.read_until(b"\r\n\r\n").decode("latin-1").split("\r\n")
    headers = {line.split(":", 1)[0].strip().lower(): line.split(":", 1)[1].strip() for line in head[1:] if ":" in line}
    accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
    if " 101 " not in head[0] + " " or headers.get("sec-websocket-accept") != accept:
        raise CDPError("Chrome refused the DevTools WebSocket: " + head[0])


class _Session:
    """One DevTools WebSocket to one tab; ``call`` sends a command and returns its result."""

    def __init__(self, ws_url, timeout, connect):
        parsed = _loopback(ws_url, ("ws",))
        self.sock, self.next_id = connect((parsed.hostname, parsed.port), timeout=timeout), 0
        try:
            self.sock.settimeout(timeout)
            self.stream = _Socket(self.sock)
            _handshake(self.stream, parsed)
        except OSError as error:
            self.sock.close()
            raise CDPError(f"Chrome DevTools connection failed: {error}") from error
        except BaseException:
            self.sock.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.sock.close()

    def call(self, method, params):
        self.next_id += 1
        try:
            self.stream.send(0x1, json.dumps({"id": self.next_id, "method": method, "params": params}).encode("utf-8"))
            while True:
                reply = json.loads(self.stream.message())
                if reply.get("id") == self.next_id:
                    break
        except OSError as error:
            raise CDPError(f"Chrome DevTools connection failed: {error}") from error
        if "error" in reply:
            raise CDPError(str(reply["error"].get("message", reply["error"])))
        result = reply.get("result", {})
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            raise CDPError(details.get("exception", {}).get("description") or details.get("text") or "Script failed")
        return result


def _accepted(page, href):
    if not isinstance(href, str) or not page(href):
        raise PageChangedError("The tab shows another page than the bound one")
    return href


def evaluate(ws_url, expression, timeout=60, connect=socket.create_connection, page=None):
    """Evaluate ``expression`` in the tab, await a promise, and return its JSON value.

    With ``page`` (a predicate on the document URL) the expression runs only in
    a document whose URL ``page`` accepted: the URL is read first, and the
    expression itself starts by checking the document is still at exactly that
    URL, so a navigation in between runs nothing.
    """
    with _Session(ws_url, timeout, connect) as session:
        if page is not None:
            read = session.call("Runtime.evaluate", {"expression": "location.href", "returnByValue": True})
            href = _accepted(page, read.get("result", {}).get("value"))
            expression = (
                f"(() => {{ if (location.href !== {json.dumps(href)}) throw new Error({json.dumps(PAGE_CHANGED)}); "
                f"return {expression}; }})()"
            )
        params = {"expression": expression, "awaitPromise": True, "returnByValue": True}
        try:
            return session.call("Runtime.evaluate", params).get("result", {}).get("value")
        except CDPError as error:
            if page is not None and PAGE_CHANGED in str(error):
                raise PageChangedError("The tab shows another page than the bound one") from error
            raise


# URL of the top-level document an element lives in; null once that document was navigated away.
_OWN_PAGE = (
    "function () { const view = this.ownerDocument && this.ownerDocument.defaultView; "
    "return view && view === view.top && this.isConnected ? view.location.href : null; }"
)


def set_file_input(ws_url, selector, path, timeout=60, connect=socket.create_connection, page=None):
    """Select ``path`` in the one file input ``selector`` names, as a user's file choice would.

    DOM.setFileInputFiles is Chrome's native file selection (it fires the
    input's own change events); no attachment is faked in JavaScript. No
    single matching file input means the user picks the file. With ``page``
    (a predicate on the document URL) the file is set only on an input whose
    own live top-level document is at a URL ``page`` accepts; the check and
    the selection address the same element, so a navigation cannot move the
    file to another page.
    """
    expression = (
        f"(() => {{ const found = document.querySelectorAll({json.dumps(selector)}); "
        "return found.length === 1 && found[0].type === 'file' ? found[0] : null; })()"
    )
    with _Session(ws_url, timeout, connect) as session:
        handle = session.call("Runtime.evaluate", {"expression": expression, "returnByValue": False}).get("result", {})
        if handle.get("subtype") == "null" or not handle.get("objectId"):
            return {"status": "needs_manual"}
        if page is not None:
            own = session.call(
                "Runtime.callFunctionOn",
                {"objectId": handle["objectId"], "functionDeclaration": _OWN_PAGE, "returnByValue": True},
            )
            _accepted(page, own.get("result", {}).get("value"))
        session.call("DOM.setFileInputFiles", {"files": [str(path)], "objectId": handle["objectId"]})
    return {"status": "uploaded"}
