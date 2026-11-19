"""The plan and the closing table of one `apply [N]` run (plan 3.4).

``pick`` chooses which ledger jobs a run opens: held jobs Chrome lost come first,
then up to N pending jobs a script driver supports. The ledger already drops a
job whose URL it holds; a pending job that matches a tracker row with no job URL
(a Gmail or imported history entry) by company and role is skipped. A job on an
ATS with no driver is never opened and goes to the submit-by-hand list. Job IDs
the user names are not deduplicated. ``table`` renders the end of
the run. Nothing here opens a browser or clicks anything.
"""

import re
from urllib.parse import urlsplit

SUPPORTED_ATS = ("greenhouse", "workday", "rippling", "ashby", "lever", "workable")
HELD_STATES = ("filled", "reviewed", "needs_manual")
ALREADY_APPLIED = "already in the tracker"
UNSUPPORTED = "no script driver for this ATS: submit by hand"


def supported(record):
    return str(record.get("ats") or "").casefold() in SUPPORTED_ATS


def _norm(text):
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", str(text or "").casefold()).split())


def _role_key(role):
    # Season, year and bracketed notes do not make a different role.
    role = re.sub(r"\(.*?\)|\[.*?\]", " ", str(role or ""))
    return " ".join(word for word in _norm(role).split()
                    if not re.fullmatch(r"20\d\d|spring|summer|fall|autumn|winter", word))


def _history(row):
    """A tracker row from a confirmation email or an imported file: no job posting URL to dedup by."""
    url = str(row.get("url") or "")
    host = (urlsplit(url).hostname or "").casefold() if url else ""
    return str(row.get("id", "")).startswith("mail-") or not host or host == "mail.google.com"


def _applied(record, rows):
    company, role = _norm(record.get("company")), _role_key(record.get("role"))
    if not company or not role:
        return False
    return any(
        str(row.get("id")) != str(record["id"])
        and _norm(row.get("company")) == company
        and _role_key(row.get("role")) == role
        for row in rows
        if _history(row)
    )


def _summary(record, reason=None):
    item = {"id": record["id"], "company": record.get("company"), "role": record.get("role"), "url": record.get("url")}
    return {**item, "reason": reason} if reason else item


def reopen(store, gone):
    """Held jobs whose tab Chrome lost go back to pending, to be opened at the same URL and filled again."""
    reopened = []
    for item in gone:
        record = store.get(item["job_id"])
        if record["state"] not in HELD_STATES or not record.get("tab_id"):
            continue
        store.update(record["id"], state="pending", tab_id=None, previous_tab_id=record["tab_id"], claim=None,
                     claimed_at=None, problem=None, reopened=True)
        reopened.append(record["id"])
    return reopened


def pick(store, count=None, *, ids=None, first=()):
    """``{"selected", "by_hand", "skipped"}``: the job IDs to fill and the jobs set aside with reasons."""
    rows = store.tracker_rows() if ids is None else []
    selected, by_hand, skipped = list(first), [], []

    def sort(record):
        if record["state"] != "pending":
            return True
        if rows and _applied(record, rows):
            store.update(record["id"], state="skipped", problem=ALREADY_APPLIED)
            skipped.append(_summary(record, ALREADY_APPLIED))
            return False
        if not supported(record):
            store.update(record["id"], state="by_hand", problem=UNSUPPORTED)
            by_hand.append(_summary(record))
            return False
        return True

    if ids is not None:
        for job_id in ids:
            record = store.get(job_id)
            if record["state"] == "by_hand":
                by_hand.append(_summary(record))
            elif job_id not in selected and sort(record):
                selected.append(job_id)
        return {"selected": selected, "by_hand": by_hand, "skipped": skipped}
    added = 0
    for record in store.jobs():
        if record["state"] != "pending" or record["id"] in selected or not sort(record):
            continue
        if added < count:
            selected.append(record["id"])
            added += 1
    return {"selected": selected, "by_hand": by_hand, "skipped": skipped}


def _unknown(record):
    return list((record.get("fill_result") or {}).get("unknown") or [])


def questions(records):
    """Required questions no confirmed fact answers, collected over the whole run."""
    return [
        {"job_id": record["id"], **{key: item.get(key) for key in ("id", "label", "type", "options")}}
        for record in records
        for item in _unknown(record)
        if item.get("required") and item.get("reason") == "no_confirmed_fact"
    ]


def fields_to_check(record):
    """Labels of required fields the run could not set, then fields the driver blocked on."""
    labels = [str(item.get("label") or item.get("id")) for item in _unknown(record) if item.get("required")]
    for item in (record.get("fill_result") or {}).get("blocked") or []:
        label = str(item.get("label") or item.get("id") or item.get("reason") or "page")
        if label not in labels:
            labels.append(label)
    if not labels and record.get("problem") and record.get("state") != "filled":
        labels.append(str(record["problem"]))
    return labels


def _cell(value, width):
    text = " ".join(str(value if value is not None else "-").split()) or "-"
    return text if len(text) <= width else text[: width - 1] + "…"


WIDTHS = (("company", "Company", 20), ("role", "Role", 32), ("resume", "Resume", 20), ("tab", "Tab", 12))


def table(report):
    """The end-of-run text: one row per job, then confirmations, skipped jobs and the by-hand list."""
    lines = []
    rows = report.get("rows") or []
    if rows:
        heads = [_cell(title, width).ljust(width) for _, title, width in WIDTHS]
        lines.append(" | ".join(heads + ["Fields to check"]))
        lines.append("-+-".join(["-" * width for _, _, width in WIDTHS] + ["-" * 15]))
        for row in rows:
            cells = [_cell(row.get(key), width).ljust(width) for key, _, width in WIDTHS]
            check = ", ".join(row.get("check") or []) or ("none" if row.get("state") == "filled" else row.get("state"))
            lines.append(" | ".join(cells + [check]))
        lines.append("Never submitted: review each held tab and press Submit yourself.")
    else:
        lines.append("No form was opened in this run.")
    track = report.get("track") or {}
    found = [f"{item.get('company')} | {item.get('role')}" for item in track.get("submitted") or []]
    found += [f"{item.get('company')} | {item.get('role')} | {item.get('date')}"
              for item in track.get("history_entries") or []]
    if found:
        lines += ["", "Confirmations found in Gmail:", *("  " + item for item in found)]
    check = [f"{item.get('job_id')} | {item.get('company')} | {item.get('role')} | {item.get('email_date')}"
             f" | undo: {item.get('undo')}" for item in track.get("check") or []]
    if check:
        lines += ["", "Recorded from email only, check it:", *("  " + item for item in check),
                  "  Not your application from the tool's tab? Run the undo command to hold the job again."]
    skipped = [f"{item.get('company')} | {item.get('role')} | {item.get('reason')}"
               for item in report.get("skipped") or []]
    summary = ((report.get("fetch") or {}).get("report") or {}).get("summary")
    if skipped or summary:
        lines += ["", "Skipped:", *("  " + item for item in skipped)]
        if summary:
            lines.append("  " + summary)
    if report.get("by_hand"):
        lines += ["", "Submit by hand (no script driver for this ATS; not opened):"]
        lines += [f"  {item.get('company')} | {item.get('role')} | {item.get('url')} | resume: {item.get('resume') or '-'}"
                  for item in report["by_hand"]]
    # The run's batch of unanswered required questions (unanswered.Questions.collect).
    asked = report.get("questions") or {}
    count = asked.get("pending") or 0
    if count:
        noun = "1 question needs" if count == 1 else f"{count} questions need"
        lines += ["", f"{noun} your answer before those forms are complete."]
    if asked.get("next"):
        lines.append(str(asked["next"]))
    if report.get("reason"):
        lines += ["", "Stopped: " + str(report["reason"])]
        left = len(report.get("left") or [])
        if left:
            lines.append(f"{left} job{'s' if left != 1 else ''} left for the next run.")
    return "\n".join(lines)
