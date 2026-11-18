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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from . import cdp as cdp_module
from .identity import canonical_url
from .mail_history import ACCOUNT, classify, import_confirmations, parse
from .storage import Store, read_json

TRACK_DAYS = 14
TRACK_PAGES = 3
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


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())


def _same(wanted, found):
    wanted, found = _norm(wanted), _norm(found)
    return bool(wanted and found) and (wanted in found or found in wanted)


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


def _tab(record, tabs):
    for tab in tabs:
        if tab["id"] == str(record["tab_id"]):
            return tab
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

    A confirmation without a role that names the company of several held jobs
    is held back: it is neither matched nor added to the history.
    """
    matches, ambiguous = {}, set()
    for row in rows:
        if classify(row) != "confirm":
            continue
        entry = parse(row)
        if entry["key"] in seen or not entry["company"]:
            continue
        hits = [r for r in held if r["id"] not in matches and _same(r["company"], entry["company"])
                and (not entry["role"] or _same(r["role"], entry["role"]))]
        if len(hits) == 1:
            matches[hits[0]["id"]] = (row, entry)
        elif hits:
            ambiguous.add(entry["key"])
    return matches, ambiguous


def _gmail_rows(debug_url):
    from .gmail_tab import GmailTab

    return GmailTab(debug_url).history(days=TRACK_DAYS, max_pages=TRACK_PAGES)


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
            rows = list(gmail(gmail_url) or [])
            report["gmail"] = f"checked {len(rows)} rows from the last {TRACK_DAYS} days"
        except (OSError, ValueError, RuntimeError) as error:
            report["gmail"] = f"Gmail unavailable: {error}"
    seen = {event.get("message_id") for event in store.mail_events() if event.get("account") == ACCOUNT}
    mails, ambiguous = _mail_matches(held, rows, seen)
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
    used = {entry["key"] for _, entry in mails.values()} | ambiguous
    leftover = [row for row in rows if parse(row)["key"] not in used]
    report["ambiguous_emails"] = len(ambiguous)
    history = import_confirmations(store, leftover)
    report["history"] = {key: history[key] for key in ("added", "duplicates", "ignored")}
    report["history_entries"] = [
        {"company": item["company"], "role": item["role"], "date": item["date"]} for item in history["entries"]
    ]
    return report
