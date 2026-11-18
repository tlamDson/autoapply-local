"""Record forms the user submitted by hand; the tool itself never clicks Submit.

Recording frees the job's held-tab slot and writes its tracker row. It reads and
writes only the local ledger and tracker: no browser, engine or network call.

Each step leaves a marker this module recognises, so a recording interrupted
part-way (for example a tracker locked by a spreadsheet app) is resumed by
running `submitted ID` again instead of leaving the slot occupied. A job this
module already recorded is a no-op, so `submitted A,B` interrupted after A
resumes with the same command.
"""

from datetime import datetime, timezone

HELD_STATES = ("filled", "reviewed", "needs_manual")
REVIEW_ID = "user-confirmed-manual-submission"
CONFIRMATION_TEXT = "User confirmed manual submission"


def _recorded(record):
    """True when this module already finished recording the job."""
    confirmation = record.get("confirmation")
    if isinstance(confirmation, dict):
        confirmation = confirmation.get("text")
    return record["state"] == "submitted" and confirmation == CONFIRMATION_TEXT


def _started(record):
    """True when this module already began recording the job."""
    if record["state"] == "claimed":
        marker = record.get("manual_submission")
    elif record["state"] in ("submit_intent", "needs_reconciliation"):
        marker = record.get("submit_intent")
    else:
        return False
    return isinstance(marker, dict) and marker.get("review_id") == REVIEW_ID


def check(record):
    if _recorded(record):
        return
    if not record.get("tab_id") or (record["state"] not in HELD_STATES and not _started(record)):
        raise ValueError(
            f"Job {record['id']} has no held form to record as submitted (state {record['state']})"
        )


def record(store, job_id, clock=None):
    current = store.get(job_id)
    check(current)
    if _recorded(current):
        return {"job_id": str(job_id), "status": "submitted"}
    evidence = {"review_id": REVIEW_ID, "tab_id": current["tab_id"]}
    if current["state"] in ("filled", "needs_manual"):
        # The ledger's submission intent accepts claimed or reviewed jobs only;
        # the marker lets a retry tell this claim from an apply run's claim.
        current = store.update(job_id, state="claimed", manual_submission=evidence)
    if current["state"] in ("claimed", "reviewed"):
        store.record_submit_intent(job_id, evidence)
    store.mark_submitted(
        job_id,
        {
            "status": "confirmed",
            "job_id": str(job_id),
            "url": current["url"],
            "text": CONFIRMATION_TEXT,
            "confirmed_at": (clock or (lambda: datetime.now(timezone.utc).isoformat()))(),
        },
    )
    return {"job_id": str(job_id), "status": "submitted"}
