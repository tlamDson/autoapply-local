"""`track`: turn forms the user submitted by hand into tracker rows. Runs first in `apply`.

Two kinds of evidence, both read-only:
- a held tab of the tool's own Chrome now shows the ATS's confirmation page;
- a confirmation email in the Gmail tab names the held job's company (and role).

A held tab still on its form is kept and listed as waiting; a tab that is gone
without evidence is listed so the user can reopen it. Confirmations that match
no held job join the applied history once (``mail_history``). The module only
lists tabs, closes a tab already on a confirmation page and reads Gmail rows;
it never runs a script in a job tab, so it cannot press any Submit control.
"""

import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from . import cdp as cdp_module
from .identity import canonical_url
from .gmail_tab import MAX_PAGES
from .mail_history import ACCOUNT, HISTORY_DAYS, classify, import_confirmations, parse
from .storage import Store, read_json

TRACK_DAYS = 14
HELD_STATES = ("filled", "reviewed", "needs_manual")
REVIEW_ID = "confirmation-page-detected"
PAGE_TEXT = "Confirmation page seen in the held tab"


def confirmation_page(url):
    """True when ``url`` is an ATS page shown only after an application was submitted."""
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return False
    host, path = (parts.hostname or "").lower(), parts.path.lower().rstrip("/")
    query = dict(parse_qsl(parts.query))
    if host == "greenhouse.io" or host.endswith(".greenhouse.io"):
        return path.endswith("/confirmation")
    if host.endswith(".myworkdayjobs.com"):
        return "/jobtasks/completed/application" in path or "job_application_id" in query
    if host == "ats.rippling.com":
        return query.get("step") == "confirmation"
    if host == "jobs.lever.co":
        return path.endswith("/thanks")
    return False


_SUFFIXES = {"inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "plc", "gmbh", "lp", "llp"}


def _words(text):
    return re.findall(r"[a-z0-9]+", str(text or "").casefold())


def _same_company(wanted, found):
    """Same company name, ignoring case, punctuation and legal suffixes; never a partial name."""
    wanted = [word for word in _words(wanted) if word not in _SUFFIXES]
    found = [word for word in _words(found) if word not in _SUFFIXES]
    return bool(wanted) and wanted == found


def _same_role(wanted, found):
    return bool(_words(wanted)) and _words(wanted) == _words(found)


def _day(text):
    try:
        return date.fromisoformat(str(text or "")[:10])
    except ValueError:
        return None


_FORM_STEPS = ("apply", "application", "thanks", "confirmation")


def _posting(url):
    """The job posting a form or confirmation URL belongs to (host and path without the form step)."""
    try:
        parts = urlsplit(canonical_url(url))
    except ValueError:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    while segments and segments[-1].lower() in _FORM_STEPS:
        segments.pop()
    return (parts.netloc, "/".join(segments)) if segments else None


def _started(record):
    marker = record.get("track") if record["state"] == "claimed" else record.get("submit_intent")
    return record["state"] in ("claimed", "submit_intent") and isinstance(marker, dict) and (
        marker.get("review_id") == REVIEW_ID)


def held_jobs(store):
    return [r for r in store.jobs() if r.get("tab_id") and (r["state"] in HELD_STATES or _started(r))]


def _endpoints(root, held):
    path = Path(root) / "browser/active-lanes.json"
    try:
        lanes = read_json(path) if path.exists() else []
    except (OSError, ValueError):
        lanes = []
    by_lane = {str(lane.get("lane", 1)): lane["debug_url"] for lane in lanes if lane.get("debug_url")}
    urls = list(dict.fromkeys(by_lane.values()))
    for record in held:
        url = record.get("debug_url") or by_lane.get(str(record.get("lane", 1)))
        if url and url not in urls:
            urls.append(url)
    return by_lane, urls


def _job_neutral_confirmation(record, url):
    """A confirmation page that names no job (Workday) on the held job's own site."""
    try:
        tab, job = urlsplit(str(url)), urlsplit(str(record["url"]))
    except ValueError:
        return False
    return (
        confirmation_page(url)
        and (tab.hostname or "").lower().endswith(".myworkdayjobs.com")
        and (tab.hostname or "").lower() == (job.hostname or "").lower()
    )


def _belongs(record, tab):
    wanted = _posting(record["url"])
    return (wanted is not None and _posting(tab["url"]) == wanted) or _job_neutral_confirmation(record, tab["url"])


def _tab(record, tabs):
    """The tab that still shows this job: its own target if it did not move to another job, else by posting."""
    for tab in tabs:
        if tab["id"] == str(record["tab_id"]):
            return tab if _belongs(record, tab) else None
    wanted = _posting(record["url"])
    if wanted is None:
        return None
    for tab in tabs:
        if _posting(tab["url"]) == wanted:
            return tab
    return None


def _summary(record, **extra):
    return {"job_id": record["id"], "company": record["company"], "role": record["role"], **extra}


def _record_page(store, record, tab, clock):
    evidence = {"review_id": REVIEW_ID, "tab_id": record["tab_id"], "page_url": tab["url"]}
    if record["state"] in ("filled", "needs_manual"):
        record = store.update(record["id"], state="claimed", track=evidence)
    if record["state"] in ("claimed", "reviewed"):
        store.record_submit_intent(record["id"], evidence)
    store.mark_submitted(record["id"], {
        "status": "confirmed", "job_id": record["id"], "url": record["url"], "page_url": tab["url"],
        "text": PAGE_TEXT, "confirmed_at": clock(),
    })


def _record_mail(store, record, row, entry, clock):
    store.mark_submitted(record["id"], {
        "status": "confirmed", "job_id": record["id"], "channel": "gmail", "account": ACCOUNT,
        "message_id": entry["key"], "url": record["url"], "text": "Gmail: " + entry["subject"],
        "confirmed_at": entry["date"] or clock(),
    })
    store.record_mail_event({"account": ACCOUNT, "message_id": entry["key"], "kind": "confirm",
                             "job_id": record["id"], "company": entry["company"], "role": entry["role"],
                             "date": entry["date"]})


def _mail_matches(held, rows, seen):
    """{job id: (row, entry)} for each unseen confirmation naming exactly one held job, and held-back keys.

    A match needs the same company, the same role when the email names one, and
    an email dated on or after the day the form was held. Any other email naming
    a held job's company is held back: neither matched nor added to the history,
    so it cannot become a second application for the same job.
    """
    matches, held_back = {}, set()
    for row in rows:
        if classify(row) != "confirm":
            continue
        entry = parse(row)
        if entry["key"] in seen or not entry["company"]:
            continue
        company = [r for r in held if _same_company(r["company"], entry["company"])]
        sent = _day(entry["date"])
        hits = [r for r in company if r["id"] not in matches
                and (not entry["role"] or _same_role(r["role"], entry["role"]))
                and sent is not None and _day(r.get("held_at")) is not None and sent >= _day(r["held_at"])]
        if len(hits) == 1:
            matches[hits[0]["id"]] = (row, entry)
        elif company:
            held_back.add(entry["key"])
    return matches, held_back


def lookback_days(held, today):
    """Gmail days to search: back to the oldest held form, at least TRACK_DAYS, at most HISTORY_DAYS."""
    days = [(today - _day(r.get("held_at"))).days + 1 for r in held if _day(r.get("held_at")) and today]
    return min(HISTORY_DAYS, max([TRACK_DAYS, *days]))


def _gmail_rows(debug_url, days):
    from . import gmail_tab

    return gmail_tab.GmailTab(debug_url).history(days=days, max_pages=MAX_PAGES)


def run(root, *, cdp=cdp_module, gmail=None, clock=None):
    """Reconcile held tabs and Gmail confirmations into the tracker; return what was found."""
    clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
    gmail = gmail or _gmail_rows
    store = Store(root)
    held = held_jobs(store)
    by_lane, endpoints = _endpoints(root, held)
    tabs, reachable = {}, []
    for url in endpoints:
        try:
            tabs[url] = cdp.pages(url)
            reachable.append(url)
        except (OSError, ValueError, RuntimeError):
            continue
    report = {"submitted": [], "waiting": [], "gone": [], "unknown": [], "problems": [],
              "history": {"added": 0, "duplicates": 0, "ignored": 0}, "gmail": "not checked"}
    rows = []
    gmail_url = by_lane.get("1") if by_lane.get("1") in reachable else (reachable[0] if reachable else None)
    if gmail_url is None:
        report["gmail"] = "Chrome is not reachable; run apply to start it, then track again"
    else:
        try:
            days = lookback_days(held, _day(clock()) or datetime.now(timezone.utc).date())
            rows = list(gmail(gmail_url, days) or [])
            report["gmail"] = f"checked {len(rows)} rows from the last {days} days"
        except (OSError, ValueError, RuntimeError) as error:
            report["gmail"] = f"Gmail unavailable: {error}"
    seen = {event.get("message_id") for event in store.mail_events() if event.get("account") == ACCOUNT}
    mails, held_back = _mail_matches(held, rows, seen)
    for record in held:
        endpoint = record.get("debug_url") or by_lane.get(str(record.get("lane", 1)))
        tab = _tab(record, tabs[endpoint]) if endpoint in tabs else None
        try:
            if record["id"] in mails:
                row, entry = mails[record["id"]]
                _record_mail(store, record, row, entry, clock)
                report["submitted"].append(_summary(record, via="email", evidence="Gmail: " + entry["subject"]))
            elif tab is not None and confirmation_page(tab["url"]):
                _record_page(store, record, tab, clock)
                report["submitted"].append(_summary(record, via="page", evidence=tab["url"]))
            elif endpoint not in tabs:
                report["unknown"].append(_summary(record, url=record["url"]))
                continue
            elif tab is None:
                report["gone"].append(_summary(record, url=record["url"]))
                continue
            else:
                report["waiting"].append(_summary(record, tab_id=tab["id"], state=record["state"]))
                continue
        except (OSError, ValueError, KeyError) as error:
            report["problems"].append(_summary(record, problem=str(error) or type(error).__name__))
            continue
        if tab is not None and confirmation_page(tab["url"]):
            try:
                cdp.close_page(endpoint, tab["id"])
            except (OSError, ValueError, RuntimeError):
                pass
    used = {entry["key"] for _, entry in mails.values()} | held_back
    leftover = [row for row in rows if parse(row)["key"] not in used]
    report["held_back_emails"] = len(held_back)
    history = import_confirmations(store, leftover)
    report["history"] = {key: history[key] for key in ("added", "duplicates", "ignored")}
    report["history_entries"] = [
        {"company": item["company"], "role": item["role"], "date": item["date"]} for item in history["entries"]
    ]
    return report
