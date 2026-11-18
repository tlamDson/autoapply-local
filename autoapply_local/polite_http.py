"""Polite access to rate-limited public job hosts (Workday CXS).

One request at a time per host, a small spacing between requests to the same
host, Retry-After honoured, otherwise exponential backoff with jitter. Sleep,
clock and randomness are injected so tests never wait.
"""

import threading
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError

RETRYABLE_HTTP = (429, 500, 502, 503, 504)
BACKOFF_BASE = 2.0
BACKOFF_CAP = 30.0
# A server asking for a longer wait than this is not retried; the caller reports it.
MAX_RETRY_AFTER = 60.0
HOST_SPACING = 1.0
# What a browser sends for a JSON XHR; some hosts throttle clients that omit it.
BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def failure_status(error):
    """Status for a manual URL or board whose fetch raised ``error``."""
    code = error.code if isinstance(error, HTTPError) else None
    return "rate_limited" if code == 429 else "closed" if code in (404, 410) else "unknown"


def no_sleep(_seconds):
    return None


def retry_after(error, now=None):
    """Seconds the server asked us to wait, or None when absent or unreadable."""
    headers = getattr(error, "headers", None)
    value = headers.get("Retry-After") if headers is not None else None
    if value is None:
        return None
    value = str(value).strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (when - now).total_seconds())


def backoff(attempt, rand):
    """Exponential delay for retry number ``attempt`` (0-based), jittered to 50-100%."""
    return min(BACKOFF_CAP, BACKOFF_BASE * 2**attempt) * (0.5 + 0.5 * rand)


class HostGate:
    """Serializes requests per host and keeps them ``spacing`` seconds apart."""

    def __init__(self, spacing=HOST_SPACING):
        self.spacing = spacing
        self._registry = threading.Lock()
        self._locks, self._last = {}, {}

    def lock(self, host):
        with self._registry:
            return self._locks.setdefault(host, threading.Lock())

    def wait(self, host, clock, sleep):
        """Call with the host's lock held, before the first try of a request."""
        last = self._last.get(host)
        if last is not None:
            delay = min(self.spacing, max(0.0, self.spacing - (clock() - last)))
            if delay > 0:
                sleep(delay)

    def mark(self, host, clock):
        self._last[host] = clock()


GATE = HostGate()


def retryable(error):
    return error.code in RETRYABLE_HTTP if isinstance(error, HTTPError) else True


def call(send, *, host, tries, sleep, random, clock, gate=None):
    """Run ``send()`` politely: gated by host, at most ``tries`` attempts."""
    gate = gate or GATE
    with gate.lock(host):
        gate.wait(host, clock, sleep)
        for attempt in range(tries):
            try:
                gate.mark(host, clock)
                return send()
            except (TimeoutError, URLError, ConnectionError) as error:
                if not retryable(error) or attempt == tries - 1:
                    raise
                asked = retry_after(error)
                if asked is not None and asked > MAX_RETRY_AFTER:
                    raise
                delay = asked + random() if asked is not None else backoff(attempt, random())
                sleep(max(delay, gate.spacing))
