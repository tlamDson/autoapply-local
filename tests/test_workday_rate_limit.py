"""Workday CXS rate limits (HTTP 429): polite client, Retry-After, backoff, reporting.

All responses are synthetic; the transport, sleep, clock and randomness are injected.
No network call is made.
"""

import threading
import time
from email.message import Message
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from test_support import ReleaseCase

HOST = "synthetic.wd1.myworkdayjobs.com"
BOARD = {"tenant": "synthetic", "host": HOST, "site": "Careers", "company": "Example Labs"}
JOB_URL = f"https://{HOST}/en-US/Careers/job/Remote/Software-Engineering-Intern_R123"
PAYLOAD = {
    "total": 1,
    "jobPostings": [
        {
            "title": "Software Engineering Intern",
            "externalPath": "/job/Remote/Software-Engineering-Intern_R123",
            "locationsText": "Remote, US",
            "bulletFields": ["R123"],
            "postedOn": "Posted Today",
        }
    ],
}


def too_many(retry_after=None, code=429):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return HTTPError(f"https://{HOST}/wday/cxs/synthetic/Careers/jobs", code, "Too Many Requests", headers, None)


class RateLimitTests(ReleaseCase):
    def fetcher(self, *responses, rand=0.0, gate=None):
        fetch = self.module("fetch")
        polite = self.module("polite_http")
        self.transport = Mock(spec=["get_json", "post_json"])
        self.transport.post_json.side_effect = list(responses)
        self.sleeps = []
        return fetch.Fetcher(
            transport=self.transport,
            timeout=5,
            retries=2,
            sleep=self.sleeps.append,
            random=lambda: rand,
            clock=lambda: 1000.0,
            gate=gate or polite.HostGate(),
        )

    def test_retry_after_seconds_is_honoured_before_the_next_try(self):
        fetcher = self.fetcher(too_many(retry_after=7), PAYLOAD)
        jobs = fetcher.fetch("workday", BOARD)
        self.assertEqual([job["role"] for job in jobs], ["Software Engineering Intern"])
        self.assertEqual(self.transport.post_json.call_count, 2)
        self.assertEqual(self.sleeps, [7.0])

    def test_retry_after_http_date_is_honoured(self):
        polite = self.module("polite_http")
        now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        error = too_many(retry_after=format_datetime(now + timedelta(seconds=12), usegmt=True))
        self.assertEqual(polite.retry_after(error, now=now), 12.0)
        self.assertEqual(polite.retry_after(too_many(retry_after="soon"), now=now), None)
        self.assertEqual(polite.retry_after(too_many(), now=now), None)
        past = too_many(retry_after=format_datetime(now - timedelta(seconds=30), usegmt=True))
        self.assertEqual(polite.retry_after(past, now=now), 0.0)

    def test_backoff_without_retry_after_is_exponential_with_jitter_and_three_tries(self):
        for rand, expected in ((0.0, [1.0, 2.0]), (1.0, [2.0, 4.0])):
            with self.subTest(rand=rand):
                fetcher = self.fetcher(too_many(), too_many(), PAYLOAD, rand=rand)
                self.assertEqual(len(fetcher.fetch("workday", BOARD)), 1)
                self.assertEqual(self.transport.post_json.call_count, 3)
                self.assertEqual(self.sleeps, expected)

    def test_three_failed_tries_report_rate_limited_not_unknown(self):
        fetcher = self.fetcher(too_many(), too_many(), too_many(), too_many())
        result = fetcher.import_url(JOB_URL)
        self.assertEqual(result["status"], "rate_limited")
        self.assertEqual(result["url"], JOB_URL)
        self.assertIn("429", result["reason"])
        self.assertEqual(self.transport.post_json.call_count, 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_retry_after_longer_than_the_wait_cap_stops_without_hammering(self):
        fetcher = self.fetcher(too_many(retry_after=3600), PAYLOAD)
        result = fetcher.import_url(JOB_URL)
        self.assertEqual(result["status"], "rate_limited")
        self.assertEqual(self.transport.post_json.call_count, 1)
        self.assertEqual(self.sleeps, [])

    def test_other_http_errors_keep_their_status(self):
        fetch = self.module("fetch")
        self.assertEqual(fetch.failure_status(too_many()), "rate_limited")
        self.assertEqual(fetch.failure_status(too_many(code=404)), "closed")
        self.assertEqual(fetch.failure_status(too_many(code=410)), "closed")
        self.assertEqual(fetch.failure_status(too_many(code=403)), "unknown")
        self.assertEqual(fetch.failure_status(TimeoutError("synthetic")), "unknown")
        fetcher = self.fetcher(too_many(code=404))
        self.assertEqual(fetcher.import_url(JOB_URL)["status"], "closed")
        self.assertEqual(self.transport.post_json.call_count, 1)

    def test_pages_on_one_workday_host_are_spaced_apart(self):
        first = {"total": 2, "jobPostings": PAYLOAD["jobPostings"]}
        second = {
            "total": 2,
            "jobPostings": [{**PAYLOAD["jobPostings"][0], "bulletFields": ["R124"], "externalPath": "/job/R124"}],
        }
        fetcher = self.fetcher(first, second)
        self.assertEqual(len(fetcher.fetch("workday", BOARD)), 2)
        self.assertEqual(self.sleeps, [1.0])

    def test_one_request_at_a_time_per_workday_host(self):
        fetch, polite = self.module("fetch"), self.module("polite_http")
        gate = polite.HostGate()
        state = {"active": 0, "peak": 0}
        guard = threading.Lock()

        def post_json(url, timeout, data):
            with guard:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.05)
            with guard:
                state["active"] -= 1
            return PAYLOAD

        def run():
            transport = Mock(spec=["get_json", "post_json"])
            transport.post_json.side_effect = post_json
            fetch.Fetcher(transport=transport, sleep=lambda _: None, gate=gate).fetch("workday", BOARD)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(state["peak"], 1)

    def test_transport_sends_a_browser_like_accept_header(self):
        fetch = self.module("fetch")
        seen = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _limit):
                return b"{}"

        def fake_urlopen(request, timeout):
            seen.append(request)
            return Response()

        with patch.object(fetch, "urlopen", fake_urlopen):
            fetch.Transport().post_json(f"https://{HOST}/wday/cxs/synthetic/Careers/jobs", 5, {"limit": 20})
        accept = seen[0].get_header("Accept")
        self.assertIn("application/json", accept)
        self.assertIn("*/*", accept)
        self.assertTrue(seen[0].get_header("Accept-language"))
        self.assertEqual(seen[0].get_header("Content-type"), "application/json")
