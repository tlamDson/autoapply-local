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
from datetime import date, datetime, timedelta, timezone
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
NO_JOB_NOTE = (
    "This confirmation page does not name the job; it is recorded when its Gmail confirmation arrives, "
    "or run `submitted <id>` if you submitted this form"
)
UNDO = "track --undo {}"


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
_LOCALE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$", re.IGNORECASE)


def _workday_posting(host, segments):
    """Workday: site and requisition path before ``apply``, without the locale; None when no job is named."""
    if segments and _LOCALE.match(segments[0]):
        segments = segments[1:]
    lowered = [segment.lower() for segment in segments]
    if "apply" in lowered:
        segments = segments[:lowered.index("apply")]
    if not {"job", "details"} & {segment.lower() for segment in segments[:-1]}:
        return None
    return host, "/".join(segments)


def _posting(url):
    """The job posting a form or confirmation URL belongs to (host and path without the form step)."""
    try:
        parts = urlsplit(canonical_url(url))
    except ValueError:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if (parts.hostname or "").lower().endswith(".myworkdayjobs.com"):
        return _workday_posting(parts.netloc.lower(), segments)
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
        and _posting(url) is None
        and (tab.hostname or "").lower().endswith(".myworkdayjobs.com")
        and (tab.hostname or "").lower() == (job.hostname or "").lower()
    )


def _names_job(record, url):
    wanted = _posting(record["url"])
    return wanted is not None and _posting(url) == wanted


def _belongs(record, tab):
    """The held tab still shows this job, or a confirmation page that names no job on its site."""
    return _names_job(record, tab["url"]) or _job_neutral_confirmation(record, tab["url"])


def _tab(record, tabs):
    """The tab that still shows this job: its own target if it did not move to another job, else by posting."""
    for tab in tabs:
        if tab["id"] == str(record["tab_id"]):
            return tab if _belongs(record, tab) else None
    return next((tab for tab in tabs if _names_job(record, tab["url"])), None)


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
        "confirmed_at": entry["date"] or clock(), "previous_state": record["state"],
    })
    store.record_mail_event({"account": ACCOUNT, "message_id": entry["key"], "kind": "confirm",
                             "job_id": record["id"], "company": entry["company"], "role": entry["role"],
                             "date": entry["date"]})


def _fits(record, entry, sent, today, tracked):
    """Whether a confirmation sent on ``sent`` can be this held job's.

    With a held day: the same role when the email names one, sent on or after
    that day. A legacy record (held before ``held_at`` existed) has no such day,
    so the email must name the exact role and be sent within TRACK_DAYS, and the
    tracker must not already hold that company and role (it may be that one's).
    """
    if sent is None:
        return False
    held = _day(record.get("held_at"))
    if held is not None:
        return (not entry["role"] or _same_role(record["role"], entry["role"])) and sent >= held
    return (
        bool(entry["role"]) and _same_role(record["role"], entry["role"])
        and today is not None and 0 <= (today - sent).days <= TRACK_DAYS
        and not any(_same_company(row.get("company"), record["company"]) and _same_role(row.get("role"), record["role"])
                    for row in tracked)
    )


def _could_send(record, entry, sent):
    """Whether this job, held or already recorded, could be the application an email confirms.

    Broader than ``_fits`` on purpose: a job that cannot be matched by email
    (a legacy record, a held job without a tab, one being recorded by hand)
    still makes an email of its company ambiguous. Only a named other role, an
    email sent before the form's known held day (or, for a recorded job without
    one, before the job was created), or a submission recorded more
    than TRACK_DAYS before the email rules a job out.
    """
    if entry["role"] and not _same_role(record["role"], entry["role"]):
        return False
    held = _day(record.get("held_at"))
    confirmation = record.get("confirmation")
    if not isinstance(confirmation, dict):
        return sent is None or held is None or sent >= held
    done = _day(confirmation.get("confirmed_at"))
    if sent is None or done is None:
        return False
    # Without a held day the record's own creation day is the earliest the form could be sent; with
    # neither, nothing dates the submission: the page or row may be seen days after it.
    start = held or _day(record.get("created_at"))
    return (start is None or start <= sent) and sent <= done + timedelta(days=TRACK_DAYS)


def _mail_matches(held, rows, seen, today=None, tracked=(), others=()):
    """{job id: (row, entry)} for each unseen confirmation only one job could have sent, and held-back keys.

    ``others`` are the jobs outside ``held`` that may have been submitted. An
    email is matched only when one held job of its company ``_fits`` it and no
    other job of that company, held or not, matched or not, ``_could_send`` it.
    Any other email naming a held job's company is held back: neither matched
    nor added to the history, so it cannot mark a second job submitted or
    become a separate application.
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
        senders = [r for r in [*company, *others]
                   if _same_company(r["company"], entry["company"]) and _could_send(r, entry, sent)]
        fits = [r for r in company if _fits(r, entry, sent, today, tracked)]
        if len(fits) == 1 and senders == fits and fits[0]["id"] not in matches:
            matches[fits[0]["id"]] = (row, entry)
        elif company:
            held_back.add(entry["key"])
    return matches, held_back


def possible_senders(store, held, tracked):
    """Jobs outside ``held`` that may have been submitted: tracker rows and filled forms without a tab.

    A job in the tracker carries the day it was recorded, from its confirmation
    or, once its stage changed, from its tracker row.
    """
    ids = {record["id"] for record in held}
    rows = {str(row["id"]): row for row in tracked}
    senders = []
    for record in store.jobs():
        if record["id"] in ids:
            continue
        confirmation = record.get("confirmation")
        if record["id"] in rows or isinstance(confirmation, dict):
            day = confirmation.get("confirmed_at") if isinstance(confirmation, dict) else None
            senders.append({**record, "confirmation": {
                "confirmed_at": day or rows.get(record["id"], {}).get("updated_at")}})
        elif record["state"] in (*HELD_STATES, "submit_intent", "needs_reconciliation"):
            senders.append(record)
    return senders


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
    report = {"submitted": [], "check": [], "waiting": [], "gone": [], "unknown": [], "problems": [],
              "history": {"added": 0, "duplicates": 0, "ignored": 0}, "gmail": "not checked"}
    rows = []
    today = _day(clock()) or datetime.now(timezone.utc).date()
    gmail_url = by_lane.get("1") if by_lane.get("1") in reachable else (reachable[0] if reachable else None)
    if gmail_url is None:
        report["gmail"] = "Chrome is not reachable; run apply to start it, then track again"
    else:
        try:
            days = lookback_days(held, today)
            rows = list(gmail(gmail_url, days) or [])
            report["gmail"] = f"checked {len(rows)} rows from the last {days} days"
        except (OSError, ValueError, RuntimeError) as error:
            report["gmail"] = f"Gmail unavailable: {error}"
    seen = {event.get("message_id") for event in store.mail_events() if event.get("account") == ACCOUNT}
    tracked = store.tracker_rows()
    mails, held_back = _mail_matches(held, rows, seen, today, tracked, possible_senders(store, held, tracked))
    for record in held:
        endpoint = record.get("debug_url") or by_lane.get(str(record.get("lane", 1)))
        tab = _tab(record, tabs[endpoint]) if endpoint in tabs else None
        try:
            if record["id"] in mails:
                row, entry = mails[record["id"]]
                _record_mail(store, record, row, entry, clock)
                report["submitted"].append(_summary(record, via="email", evidence="Gmail: " + entry["subject"]))
                if tab is None or not confirmation_page(tab["url"]):
                    # Nothing in the job's own tab backs the email: it may confirm a form sent outside the tool.
                    report["check"].append(_summary(record, email_date=entry["date"], undo=UNDO.format(record["id"])))
            elif tab is not None and confirmation_page(tab["url"]) and _names_job(record, tab["url"]):
                _record_page(store, record, tab, clock)
                report["submitted"].append(_summary(record, via="page", evidence=tab["url"]))
            elif endpoint not in tabs:
                report["unknown"].append(_summary(record, url=record["url"]))
                continue
            elif tab is None:
                report["gone"].append(_summary(record, url=record["url"]))
                continue
            elif confirmation_page(tab["url"]):
                report["waiting"].append(_summary(record, tab_id=tab["id"], state=record["state"],
                                                  page_url=tab["url"], note=NO_JOB_NOTE))
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


def undo(root, job_id):
    """Undo a job ``run`` recorded from a confirmation email alone: back to held, tracker row removed.

    The email stays in the seen mail events, so the next run does not match it again.
    """
    record = Store(root).undo_email_submission(job_id)
    return {"job_id": record["id"], "state": record["state"]}
