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
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class CDPError(RuntimeError):
    """Chrome refused the connection or the evaluated script threw."""


def _loopback(url, schemes):
    parsed = urlsplit(url)
    if parsed.scheme not in schemes or parsed.hostname not in _LOOPBACK or not parsed.port:
        raise ValueError("Chrome DevTools must be a loopback endpoint with an explicit port")
    return parsed


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


def close_page(debug_url, tab_id, opener=urlopen):
    """Close one tab of the same Chrome by its target id."""
    _loopback(debug_url, ("http",))
    target = "".join(ch for ch in str(tab_id) if ch.isalnum() or ch in "-_")
    if not target or target != str(tab_id):
        raise ValueError("Chrome tab id is malformed")
    with opener(Request(debug_url.rstrip("/") + "/json/close/" + target), timeout=5) as response:
        response.read()


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


def evaluate(ws_url, expression, timeout=60, connect=socket.create_connection):
    """Evaluate ``expression`` in the tab, await a promise, and return its JSON value."""
    parsed = _loopback(ws_url, ("ws",))
    sock = connect((parsed.hostname, parsed.port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        stream = _Socket(sock)
        _handshake(stream, parsed)
        params = {"expression": expression, "awaitPromise": True, "returnByValue": True}
        stream.send(0x1, json.dumps({"id": 1, "method": "Runtime.evaluate", "params": params}).encode("utf-8"))
        while True:
            reply = json.loads(stream.message())
            if reply.get("id") == 1:
                break
    except OSError as error:
        raise CDPError(f"Chrome DevTools connection failed: {error}") from error
    finally:
        sock.close()
    if "error" in reply:
        raise CDPError(str(reply["error"].get("message", reply["error"])))
    result = reply.get("result", {})
    if "exceptionDetails" in result:
        details = result["exceptionDetails"]
        raise CDPError(details.get("exception", {}).get("description") or details.get("text") or "Script failed")
    return result.get("result", {}).get("value")
