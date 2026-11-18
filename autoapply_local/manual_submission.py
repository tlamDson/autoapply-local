"""Record forms the user submitted by hand; the tool itself never clicks Submit.

Recording frees the job's held-tab slot and writes its tracker row. It reads and
writes only the local ledger and tracker: no browser, engine or network call.
"""

from datetime import datetime, timezone

HELD_STATES = ("filled", "reviewed", "needs_manual")
REVIEW_ID = "user-confirmed-manual-submission"


def check(record):
    if record["state"] not in HELD_STATES or not record.get("tab_id"):
        raise ValueError(
            f"Job {record['id']} has no held form to record as submitted (state {record['state']})"
        )


def record(store, job_id, clock=None):
    current = store.get(job_id)
    check(current)
    if current["state"] != "reviewed":
        # The ledger's submission intent accepts claimed or reviewed jobs only.
        store.update(job_id, state="claimed")
    store.record_submit_intent(job_id, {"review_id": REVIEW_ID, "tab_id": current["tab_id"]})
    store.mark_submitted(
        job_id,
        {
            "status": "confirmed",
            "job_id": str(job_id),
            "url": current["url"],
            "text": "User confirmed manual submission",
            "confirmed_at": (clock or (lambda: datetime.now(timezone.utc).isoformat()))(),
        },
    )
    return {"job_id": str(job_id), "status": "submitted"}
