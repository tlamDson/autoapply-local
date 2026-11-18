"""Quota guard for one `apply` run (plan 3.4 step 9).

Before each job the guard knows the engine's 5-hour and weekly usage from the
small probe call (``quota.probe``: Claude ``rate_limit_event``, Codex
``rate_limits``). When either window reaches the user's stop percent, or the
engine reports its usage limit, the run stops: held tabs stay open, the jobs not
started stay pending and the next run continues them. Unknown usage or a failed
probe never stops a run. Far below the stop the probe runs every ``EVERY`` jobs;
within ``NEAR`` points of it, before every job.
"""

import math

DEFAULT_STOP = 90
EVERY = 3
NEAR = 10
WINDOWS = (("five_hour_pct", "5-hour"), ("seven_day_pct", "weekly"))
EXHAUSTED = "quota_exhausted"


def stop_percent(config):
    """The saved quota stop percent (setup step 3), 1-100; default 90."""
    value = (config.get("runtime") or {}).get("quota_stop_percent", DEFAULT_STOP)
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 1 <= value <= 100
    ):
        raise ValueError("quota_stop_percent must be a number from 1 to 100; rerun setup")
    return value


def _peak(usage):
    found = [(usage[key], name) for key, name in WINDOWS if usage.get(key) is not None]
    return max(found) if found else None


class Guard:
    def __init__(self, engine, stop, probe):
        self.engine, self.stop, self.probe = engine, stop, probe
        self.usage = {key: None for key, _ in WINDOWS}
        self.status, self.reason = None, None
        self.since, self.checks = None, 0

    def _due(self):
        if self.since is None or self.since >= EVERY:
            return True
        peak = _peak(self.usage)
        return peak is not None and peak[0] >= self.stop - NEAR

    def check(self):
        """None while the run may open another job; else the stop reason (kept once set)."""
        if self.reason or not self._due():
            return self.reason
        result = self.probe(self.engine) or {}
        self.checks, self.since = self.checks + 1, 0
        self.status = result.get("status")
        for key, _ in WINDOWS:
            if result.get(key) is not None:
                self.usage[key] = result[key]
        if self.status == EXHAUSTED:
            self.reason = self._limit()
            return self.reason
        peak = _peak(self.usage)
        if peak is not None and peak[0] >= self.stop:
            self.reason = (
                f"{self.engine} {peak[1]} usage is {peak[0]:g}%, at or over your stop of {self.stop:g}%; "
                "held tabs are kept. Run apply again after the usage resets to continue."
            )
        return self.reason

    def after(self, result):
        """Count one finished job; an engine usage-limit error stops the run before the next."""
        self.since = (self.since or 0) + 1
        for item in (result or {}).get("jobs") or []:
            if str(item.get("reason") or "").startswith(EXHAUSTED):
                self.status = EXHAUSTED
                self.reason = self.reason or self._limit()

    def _limit(self):
        return (
            f"{self.engine} usage limit reached; held tabs are kept. "
            "Run apply again after the usage resets to continue."
        )

    def report(self):
        return {
            "engine": self.engine,
            "stop_percent": self.stop,
            **self.usage,
            "status": self.status,
            "checks": self.checks,
            "stopped": bool(self.reason),
        }
