"""Waiting for a freshly launched Chrome's DevTools endpoint (plan aa-09 demo).

Chrome answers ``/json/version`` only some time after the process starts, so a
caller that opens a tab right after launching must wait for it. The opener,
sleep and clock are fakes: no network, no real Chrome, no real waiting.
"""

import json
from urllib.error import URLError

from test_support import ReleaseCase

DEBUG_URL = "http://127.0.0.1:9333"


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()


class Clock:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class WaitReadyTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.cdp, self.clock, self.calls = self.module("cdp"), Clock(), []

    def opener_with(self, replies):
        replies = list(replies)

        def opener(request, timeout=None):
            self.calls.append(request.full_url)
            reply = replies.pop(0) if len(replies) > 1 else replies[0]
            if isinstance(reply, Exception):
                raise reply
            return Response(reply)

        return opener

    def wait(self, opener, timeout=10):
        return self.cdp.wait_ready(DEBUG_URL, timeout=timeout, opener=opener, sleep=self.clock.sleep, clock=self.clock)

    def test_waits_until_chrome_answers_then_returns_its_version(self):
        refused = URLError(ConnectionRefusedError(10061, "refused"))
        version = {"Browser": "Chrome/131.0.0.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/x"}
        result = self.wait(self.opener_with([refused, ConnectionResetError(), b"not json", version]))
        self.assertEqual(result["Browser"], "Chrome/131.0.0.0")
        self.assertEqual(self.calls, [DEBUG_URL + "/json/version"] * 4)
        self.assertEqual(len(self.clock.slept), 3)

    def test_returns_at_once_when_chrome_is_already_up(self):
        self.wait(self.opener_with([{"Browser": "Chromium/130.0.0.0"}]))
        self.assertEqual((len(self.calls), self.clock.slept), (1, []))

    def test_gives_up_with_a_clear_error_after_the_timeout(self):
        with self.assertRaises(self.cdp.CDPError) as raised:
            self.wait(self.opener_with([URLError("refused")]), timeout=5)
        self.assertIn("did not answer", str(raised.exception))
        self.assertGreaterEqual(self.clock.now, 5)
        self.assertLess(self.clock.now, 6)

    def test_an_endpoint_that_is_not_chrome_never_counts_as_ready(self):
        with self.assertRaises(self.cdp.CDPError):
            self.wait(self.opener_with([{"Browser": "SomethingElse/1.0"}]), timeout=2)

    def test_refuses_a_non_loopback_endpoint_without_any_request(self):
        with self.assertRaises(ValueError):
            self.cdp.wait_ready("http://192.0.2.10:9333", opener=self.opener_with([{}]), sleep=self.clock.sleep,
                                clock=self.clock)
        self.assertEqual(self.calls, [])
