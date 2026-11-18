"""One small engine call to check login and read 5-hour / weekly usage.

Claude reports usage in ``rate_limit_event`` rows of its stream-json output
(``rate_limit_info.utilization`` 0-1, the other window under ``unifiedWindows``).
Codex reports ``rate_limits.primary/secondary`` with ``used_percent`` and
``window_minutes`` (300 = 5 hours, 10080 = 7 days). Missing numbers stay None:
unknown usage is never shown as zero. The prompt goes on stdin: Claude Code's
``--tools`` takes several values and would swallow a trailing prompt argument.
Engine output is not kept; a failed check keeps only a short ``detail`` line.
"""

import json
import math
import subprocess

from .engines import EngineError, resolve_executable

PROMPT = "Reply with the single word OK."
DETAIL_LIMIT = 300
WINDOWS = {"five_hour": "five_hour_pct", "seven_day": "seven_day_pct"}
MINUTES = {300: "five_hour_pct", 10080: "seven_day_pct"}
LOGIN_TERMS = ("/login", "invalid api key", "codex login", "not logged in", "login required", "sign in",
               "unauthorized", "authentication")
QUOTA_TERMS = ("usage limit", "rate limit", "quota", "credits", "insufficient_quota")
HINTS = {
    "claude": "Run `claude` in a terminal, type /login and sign in, then come back here.",
    "codex": "Run `codex login` in a terminal and sign in, then come back here.",
}


def login_hint(engine):
    return HINTS[engine]


def _rows(text):
    for line in str(text or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _find(value, key):
    """Every value stored under ``key`` anywhere inside a decoded JSON row."""
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                yield item
            yield from _find(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from _find(item, key)


def parse_usage(engine, text):
    usage = {"five_hour_pct": None, "seven_day_pct": None}
    for row in _rows(text):
        if engine == "claude" and row.get("type") == "rate_limit_event":
            info = row.get("rate_limit_info") or {}
            windows = dict(info.get("unifiedWindows") or {})
            if _number(info.get("utilization")):
                windows[info.get("rateLimitType", "five_hour")] = info
            for name, window in windows.items():
                if name in WINDOWS and isinstance(window, dict) and _number(window.get("utilization")):
                    usage[WINDOWS[name]] = round(window["utilization"] * 100, 1)
        elif engine == "codex":
            for limits in _find(row, "rate_limits"):
                for window in (limits or {}).values() if isinstance(limits, dict) else ():
                    if not isinstance(window, dict):
                        continue
                    key = MINUTES.get(window.get("window_minutes"))
                    if key and _number(window.get("used_percent")):
                        usage[key] = round(float(window["used_percent"]), 1)
    return usage


def _category(text):
    text = text.casefold()
    if any(term in text for term in QUOTA_TERMS):
        return "quota_exhausted"
    if any(term in text for term in LOGIN_TERMS):
        return "login_required"
    return "engine_failure"


def _error_result(text):
    for row in _rows(text):
        if row.get("type") == "result" and row.get("is_error"):
            return str(row.get("result") or "")
    return None


def _detail(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:DETAIL_LIMIT]


def probe(engine, runner=subprocess.run, cwd=None, timeout=120):
    """Return ``{"engine", "status", "five_hour_pct", "seven_day_pct"}``; status ``ready`` when signed in.

    A failed call also returns ``detail``: the engine's last error line, capped at ``DETAIL_LIMIT``.
    """
    if engine not in HINTS:
        raise ValueError("Select one supported engine")
    report = {"engine": engine, "status": "ready", "five_hour_pct": None, "seven_day_pct": None}
    try:
        prefix = resolve_executable(engine)
    except EngineError as error:
        return {**report, "status": error.category}
    stdin = {"stdin": subprocess.DEVNULL}
    if engine == "claude":
        argv = prefix + ["--print", "--output-format", "stream-json", "--verbose", "--tools", ""]
        stdin = {"input": PROMPT}
    else:
        argv = prefix + ["exec", "--json", "--skip-git-repo-check", PROMPT]
    try:
        done = runner(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                      shell=False, timeout=timeout, cwd=cwd, **stdin)
    except subprocess.TimeoutExpired:
        return {**report, "status": "timeout"}
    except OSError:
        return {**report, "status": "configuration"}
    stdout, stderr = str(done.stdout or ""), str(done.stderr or "")
    failure = _error_result(stdout)
    if done.returncode or failure is not None:
        text = failure if failure is not None else (stderr if stderr.strip() else stdout)
        return {**report, "status": _category(stderr + " " + (failure if failure is not None else stdout)),
                "detail": _detail(text)}
    return {**report, **parse_usage(engine, stdout)}
