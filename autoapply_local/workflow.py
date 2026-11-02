"""Fill, hold, review and explicitly submit using fresh browser observations.

Giả định của người thực thi: review records live in the locked job ledger, not
an arbitrary approval file; all browser calls use an injected MCP bridge.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .storage import Store, lock, read_json


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Workflow:
    def __init__(self, root, browser, engines, default_engine="codex", clock=None, *, lane="1", debug_url=None):
        self.root = Path(root).resolve()
        self.browser = browser
        self.engines = engines
        self.default_engine = default_engine
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.store = Store(self.root)
        self.lane, self.debug_url = str(lane), debug_url

    def _profile(self):
        from .profile import validate_profile

        return validate_profile(read_json(self.root / "profile.json"))

    def _operation_lock(self, job_id):
        safe = hashlib.sha256(str(job_id).encode()).hexdigest()
        return lock(self.root / "locks" / f"{safe}.lock", timeout=30)

    def _selection(self, selection, operation):
        if operation == "apply" and isinstance(selection, int) and not isinstance(selection, bool):
            if selection < 1:
                raise ValueError("Quick count must be positive")
            return [job["id"] for job in self.store.jobs() if job["state"] == "pending"][:selection]
        if selection == "all" and operation == "review":
            return [job["id"] for job in self.store.jobs() if job["state"] in ("filled", "reviewed")]
        if selection == "all-reviewed" and operation == "submit":
            return [job["id"] for job in self.store.jobs() if job["state"] == "reviewed"]
        if (
            not isinstance(selection, (list, tuple))
            or not selection
            or any(not isinstance(item, str) or not item for item in selection)
        ):
            raise ValueError("Choose explicit job IDs" + (" or all-reviewed" if operation == "submit" else ""))
        return list(dict.fromkeys(selection))

    def apply(self, ids, pdf, engine=None):
        pdf = Path(pdf).resolve()
        if not pdf.is_file():
            raise FileNotFoundError(pdf)
        if not pdf.read_bytes().startswith(b"%PDF-"):
            raise ValueError("Choose an existing PDF resume")
        selected = engine or self.default_engine
        if selected not in self.engines:
            raise ValueError("Selected engine is unavailable; no fallback is permitted")
        profile = self._profile()
        results = []
        for job_id in self._selection(ids, "apply"):
            with self._operation_lock(job_id):
                job = self.store.get(job_id)
                if job["state"] in ("filled", "reviewed") and job.get("tab_id"):
                    results.append({"job_id": job_id, "status": job["state"], "tab_id": job["tab_id"]})
                    continue
                continuing = job["state"] == "needs_manual" and bool(job.get("tab_id"))
                if job["state"] != "pending" and not continuing:
                    raise ValueError("Job is claimed, unresolved or already submitted")
                if not continuing and not self.store.claim(job_id, lane=self.lane):
                    raise ValueError("Job is already claimed")
                # No cross-engine fallback and no tailoring in the quick path.
                analysis = {
                    "kind": "apply_analysis",
                    "allowed_tools": [],
                    "prompt": "Analyze the supplied job using confirmed scoped facts. No browser actions.",
                    "schema": {
                        "type": "object",
                        "required": ["status"],
                        "additionalProperties": False,
                        "properties": {"status": {"type": "string", "enum": ["completed"]}},
                    },
                    "job": job,
                    "profile": profile,
                    "pdf": str(pdf),
                    "instructions": "Fill only from confirmed scoped facts; preserve tab; never submit.",
                }
                try:
                    self.engines[selected].run(analysis)
                except Exception as error:
                    problem = str(error) or type(error).__name__
                    if continuing:
                        self.store.update(job_id, problem=problem)
                    else:
                        self.store.update(
                            job_id,
                            state="pending",
                            lane=None,
                            claim=None,
                            claimed_at=None,
                            problem=problem,
                            retryable=True,
                        )
                    results.append({"job_id": job_id, "status": "retryable", "reason": problem})
                    continue
                tab_id = job.get("tab_id") if continuing else None
                try:
                    if continuing:
                        fresh = self.browser.snapshot(tab_id, job_id=job_id)
                        if fresh.get("required_unknown") or fresh.get("errors"):
                            results.append({"job_id": job_id, "status": "needs_manual", "tab_id": tab_id})
                            continue
                    else:
                        tab_id = self.browser.open(job["url"])
                    self.store.update(
                        job_id,
                        tab_id=tab_id,
                        pdf=str(pdf),
                        resume_sha256=_file_digest(pdf),
                        engine=selected,
                        lane=self.lane,
                        **({"debug_url": self.debug_url} if self.debug_url else {}),
                    )
                    # Preserve injected single-page bridges' existing signature.
                    native = getattr(self.browser, "native_uploads", False) is True
                    fill_result = self.browser.fill(
                        tab_id, profile=profile, job=job, **({"pdf": str(pdf)} if native else {})
                    )
                    if fill_result.get("status") == "needs_manual" or fill_result.get("blocked"):
                        self.store.update(
                            job_id,
                            state="needs_manual",
                            fill_result=fill_result,
                            problem=str(fill_result.get("blocked") or "Complete the held form manually"),
                        )
                        results.append({"job_id": job_id, "status": "needs_manual", "tab_id": tab_id})
                        continue
                    if native:
                        receipt = fill_result.get("upload_receipt")
                        if not receipt:
                            raise ValueError("Observed upload receipt is missing; inspect the held Resume page")
                        self.store.update(job_id, upload_receipt=receipt)
                    else:
                        self.browser.upload_file(tab_id, str(pdf))
                    live = self.browser.snapshot(tab_id, job_id=job_id)
                    if live.get("errors") or live.get("required_unknown"):
                        raise ValueError("Held form still contains unresolved fields")
                    self.store.update(job_id, state="filled", fill_result=fill_result, fill_snapshot=live)
                    results.append({"job_id": job_id, "status": "filled", "tab_id": tab_id})
                except Exception as error:
                    tab_id = getattr(error, "tab_id", None) or tab_id
                    self.store.update(
                        job_id, state="needs_manual", tab_id=tab_id, problem=str(error) or type(error).__name__
                    )
                    results.append({"job_id": job_id, "status": "needs_manual", "tab_id": tab_id, "reason": str(error)})
        return {"jobs": results, **self.status()}

    def _live(self, record):
        live = self.browser.snapshot(record["tab_id"], job_id=record["id"])
        if not isinstance(live, dict):
            raise ValueError("Live browser evidence is unavailable")
        if live.get("job_id") != record["id"] or live.get("tab_id") != record["tab_id"]:
            raise ValueError("Live browser evidence belongs to a different job or tab")
        if not live.get("page_id") or urlsplit(str(live.get("url", ""))).scheme not in ("http", "https"):
            raise ValueError("Live page identity is missing")
        from .storage import _canonical_url

        if _canonical_url(live["url"]) != _canonical_url(record["url"]):
            raise ValueError("Live URL belongs to a different job")
        if live.get("errors") or live.get("required_unknown"):
            raise ValueError("Live form contains unresolved fields or validation errors")
        fields = live.get("fields")
        if not isinstance(fields, list) or not fields:
            raise ValueError("Live form fields are missing")
        profile = self._profile()
        from .profile import resolve_field
        from .browser import validate_quote

        for field in fields:
            value = field.get("value")
            required = field.get("required")
            if required and (
                field.get("observed") is False
                or field.get("status") not in (None, "confirmed")
                or value in (None, "", [])
                or (value is False and field.get("type") == "checkbox")
                or value in ("Select One", "Select an option")
            ):
                raise ValueError("A required answer is missing or unconfirmed")
            label = str(field.get("label", ""))
            if field.get("type") == "summary":
                validate_quote(live.get("text"), field.get("evidence"), label, value)
            if field.get("type") == "file":
                selected = field.get("files", [])
                if not selected or any(
                    Path(item.get("name", "")).name != Path(record["pdf"]).name
                    or item.get("size") != Path(record["pdf"]).stat().st_size
                    for item in selected
                ):
                    raise ValueError("Actual selected attachment does not match the PDF")
                continue
            if field.get("type") == "summary" and label.casefold() in ("resume", "resume pdf"):
                continue
            if value not in (None, "", []) or required:
                expected = resolve_field(profile, field, record)
                if expected.get("status") != "confirmed" or expected.get("value") != value:
                    raise ValueError("Observed answer differs from confirmed fact or exact question scope")
        pdf = Path(record["pdf"])
        if not pdf.is_file() or _file_digest(pdf) != record["resume_sha256"]:
            raise ValueError("Selected PDF changed or disappeared")
        if live.get("resume_sha256") != record["resume_sha256"]:
            raise ValueError("Live attachment is missing or is a different PDF")
        return live

    def review(self, ids):
        results = []
        for job_id in self._selection(ids, "review"):
            with self._operation_lock(job_id):
                record = self.store.get(job_id)
                if record["state"] not in ("filled", "reviewed"):
                    raise ValueError("Only held filled forms can be reviewed")
                profile = self._profile()
                live = self._live(record)
                review = {
                    "job_id": job_id,
                    "tab_id": record["tab_id"],
                    "page_id": live["page_id"],
                    "url": live["url"],
                    "profile_revision": profile["revision"],
                    "profile_sha256": _digest(profile),
                    "resume_sha256": record["resume_sha256"],
                    "form_sha256": _digest(live),
                    "reviewed_at": self.clock(),
                    "valid": True,
                }
                review["review_id"] = _digest(review)
                self.store.update(job_id, state="reviewed", review=review)
                results.append(review)
        return {"reviews": results, **self.status()}

    def submit(self, ids):
        results = []
        for job_id in self._selection(ids, "submit"):
            with self._operation_lock(job_id):
                record = self.store.get(job_id)
                review = record.get("review")
                if record["state"] != "reviewed" or not review or not review.get("valid"):
                    raise ValueError(
                        "Submission requires a previously reviewed job; uncertain outcomes need manual reconciliation"
                    )
                profile = self._profile()
                live = self._live(record)
                if profile["revision"] != review["profile_revision"] or _digest(profile) != review["profile_sha256"]:
                    raise ValueError("Profile changed after review")
                if _digest(live) != review["form_sha256"]:
                    raise ValueError("Live page, form or attachment changed after review")
                self.store.record_submit_intent(
                    job_id,
                    {
                        "review_id": review["review_id"],
                        "tab_id": record["tab_id"],
                        "created_at": self.clock(),
                    },
                )
                try:
                    evidence = self.browser.submit(record["tab_id"], job_id=job_id)
                    if isinstance(evidence, dict):
                        evidence = {**evidence, "confirmed_at": self.clock()}
                    self.store.mark_submitted(job_id, evidence)
                    results.append({"job_id": job_id, "status": "submitted"})
                except Exception as error:
                    # Never retry a click whose outcome may have reached the server.
                    self.store.update(job_id, state="needs_reconciliation", problem=type(error).__name__)
                    results.append({"job_id": job_id, "status": "needs_reconciliation"})
        return {"jobs": results, **self.status()}

    def status(self):
        jobs = self.store.jobs()
        unresolved_states = {"submit_intent", "needs_reconciliation", "needs_manual", "claimed"}
        return {
            "held_tabs": [
                {"job_id": record["id"], "tab_id": record["tab_id"], "state": record["state"]}
                for record in jobs
                if record.get("tab_id") and record["state"] != "submitted"
            ],
            "unresolved": [
                {"job_id": record["id"], "state": record["state"], "problem": record.get("problem")}
                for record in jobs
                if record["state"] in unresolved_states
            ],
            "counts": {
                state: sum(record["state"] == state for record in jobs)
                for state in sorted({record["state"] for record in jobs})
            },
        }
