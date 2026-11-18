"""Applied history from Gmail-tab rows: classify, read company/role, add to the tracker once.

Rows come from the read-only Gmail adapter (sender, subject, snippet, date,
thread id). Only application confirmations become tracker rows; verification
mails are never stored, so no code or link from them is kept. A message is
recorded once (by thread id) and an application once (by company and role, or
company and day when the mail names no role).
"""

import hashlib
import re
from urllib.parse import quote

HISTORY_DAYS = 183
ACCOUNT = "gmail-tab"
UNKNOWN_ROLE = "(role not in email)"

_VERIFY = re.compile(r"verif|activate|candidate account|security code|one-time|passcode|confirm your email", re.I)
_UNFINISHED = re.compile(r"step was started|(?:complete|finish|continue) your application", re.I)
_REJECT = re.compile(
    r"unfortunately|not (?:be )?mov(?:e|ing) forward|move forward with other|other candidates|decided not to"
    r"|no longer under consideration|not been selected|will not be proceeding",
    re.I,
)
_CONFIRM = re.compile(r"\bappl(?:y|ied|ying|ications?)\b|thank you for (?:your )?interest", re.I)

_END = r"(?=\s*[!.,:;|–—]|\s+-\s|\s*$)"
_ROLE_AT_COMPANY = re.compile(
    r"\b(?:applying|applied|application|interest)\s+(?:for|in)\s+(?:the\s+)?(?P<role>.+?)"
    r"(?:\s+(?:position|role|opening|job))?\s+(?:at|with)\s+(?P<company>.+?)" + _END,
    re.I,
)
_COMPANY = re.compile(
    r"\b(?:applying|applied|application|interest)\s+(?:to|with|at)\s+(?P<company>.+?)(?=\s+for\s|\s*[!.,:;|–—]|\s+-\s|\s*$)",
    re.I,
)
_ROLE_WORD = re.compile(
    r"\b(?:applying|applied|application|interest)\s+(?:for|to|in)\s+(?:the\s+)?(?P<role>.+?)\s+(?:position|role|opening|job)\b",
    re.I,
)
_ROLE_AFTER_COLON = re.compile(r"^[^:]*\bappl\w*\b[^:]*:\s*(?P<role>.+)$", re.I)

_SENDER_WORDS = re.compile(
    r"\b(?:university\s+)?(?:recruiting|recruitment|recruiter|careers?|talent(?:\s+acquisition)?|hiring|jobs|hr"
    r"|people|team|notifications?|no-?reply|do\s+not\s+reply)\b",
    re.I,
)
_PLATFORMS = {
    "ashbyhq", "greenhouse", "greenhouse-mail", "icims", "jobvite", "lever", "myworkday", "rippling", "smartrecruiters",
    "successfactors", "taleo", "workable", "workablemail", "workday", "gmail", "googlemail", "google", "outlook",
    "hotmail", "yahoo", "icloud",
}
_MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _name(text):
    return _clean(text).strip(" \"'!.,:;-–—")[:80]


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip()


def classify(row):
    """One of ``verify``, ``reject``, ``confirm`` or ``other`` for a Gmail list row."""
    subject, snippet = _clean(row.get("subject")), _clean(row.get("snippet"))
    text = subject + " " + snippet
    if _VERIFY.search(subject):
        return "verify"
    if _UNFINISHED.search(subject):
        return "other"
    if _REJECT.search(text):
        return "reject"
    if _CONFIRM.search(text):
        return "confirm"
    return "other"


def _sender_company(row):
    name = _name(_SENDER_WORDS.sub(" ", _clean(row.get("from"))))
    label = _norm(name).replace(" ", "")
    if name and label not in _PLATFORMS:
        return name
    domain = str(row.get("email") or "").rpartition("@")[2].lower()
    parts = [part for part in domain.split(".") if part]
    if len(parts) < 2 or any(part in _PLATFORMS or part.startswith(tuple(_PLATFORMS)) for part in parts[:-1]):
        return ""
    return parts[-2].replace("-", " ").title()


def _date(text):
    text = _clean(text)
    month_first = re.search(r"\b([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4})\b", text)
    day_first = re.search(r"\b(\d{1,2}) ([A-Z][a-z]{2})[a-z]*\.? (\d{4})\b", text)
    if month_first:
        month, day, year = month_first.groups()
    elif day_first:
        day, month, year = day_first.groups()
    else:
        return None
    month = _MONTHS.get(month.lower())
    if month is None or not 1 <= int(day) <= 31:
        return None
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def parse(row):
    """Key, company, role, date and evidence fields of one Gmail list row."""
    subject, snippet = _clean(row.get("subject")), _clean(row.get("snippet"))
    company = role = ""
    for text in (subject, snippet):
        match = _ROLE_AT_COMPANY.search(text)
        if match:
            company, role = company or _name(match["company"]), role or _name(match["role"])
        match = _COMPANY.search(text)
        if match and not company:
            company = _name(match["company"])
    for text in (subject, snippet):
        match = _ROLE_WORD.search(text) or _ROLE_AFTER_COLON.search(text)
        if match and not role:
            role = _name(re.split(r"\s+(?:at|with)\s+", match["role"], maxsplit=1, flags=re.I)[0])
    company = company or _sender_company(row)
    thread = _clean(row.get("thread"))
    if thread:
        key = "thread:" + thread
    else:
        digest = hashlib.sha256("\n".join((str(row.get("email")), subject, str(row.get("date")))).encode()).hexdigest()
        key = "row:" + digest[:24]
    return {
        "key": key,
        "thread": thread,
        "company": company,
        "role": role,
        "date": _date(row.get("date")),
        "subject": subject,
        "sender": _clean(row.get("email")),
    }


def _same(known, company, role, date):
    for other_role, other_date in known.get(company, ()):
        if role and other_role == role:
            return True
        if (not role or not other_role) and other_date == date:
            return True
    return False


def import_confirmations(store, rows):
    """Add each new confirmation to the tracker as submitted; report added, duplicates and ignored."""
    known = {}
    for tracked in store.tracker_rows():
        role = "" if tracked.get("role") == UNKNOWN_ROLE else _norm(tracked.get("role"))
        known.setdefault(_norm(tracked.get("company")), []).append((role, tracked.get("updated_at")))
    seen = {event.get("message_id") for event in store.mail_events() if event.get("account") == ACCOUNT}
    report = {"added": 0, "duplicates": 0, "ignored": 0, "entries": []}
    for row in rows:
        entry = parse(row) if classify(row) == "confirm" else None
        if entry is None or not entry["company"]:
            report["ignored"] += 1
            continue
        company, role = _norm(entry["company"]), _norm(entry["role"])
        if entry["key"] in seen or _same(known, company, role, entry["date"]):
            report["duplicates"] += 1
            continue
        identity = "mail-" + hashlib.sha256(entry["key"].encode()).hexdigest()[:16]
        anchor = quote(entry["thread"] or identity, safe="")
        url = f"https://mail.google.com/mail/u/0/?th={anchor}#all/{anchor}"
        store.enqueue({"id": identity, "company": entry["company"], "role": entry["role"] or UNKNOWN_ROLE,
                       "url": url, "source": "gmail-history"})
        store.mark_submitted(identity, {
            "status": "confirmed", "job_id": identity, "channel": "gmail", "account": ACCOUNT,
            "message_id": entry["key"], "url": url, "text": "Gmail: " + entry["subject"],
            "confirmed_at": entry["date"],
        })
        store.record_mail_event({"account": ACCOUNT, "message_id": entry["key"], "kind": "confirm", "job_id": identity,
                                 "company": entry["company"], "role": entry["role"], "date": entry["date"]})
        seen.add(entry["key"])
        known.setdefault(company, []).append((role, entry["date"]))
        report["added"] += 1
        report["entries"].append({**entry, "id": identity})
    return report
