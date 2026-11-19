"""Fill, hold and review forms using fresh browser observations; the user submits by hand.

Giả định của người thực thi: review records live in the locked job ledger, not
an arbitrary approval file; all browser calls use an injected MCP bridge.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .refill_proof import blocked_fields as _blocked_fields, blocked_reason as _blocked_reason, unproven
from .storage import Store, lock, read_json
from .unanswered import check_answers, pending_fields


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
        if (
            not isinstance(selection, (list, tuple))
            or not selection
            or any(not isinstance(item, str) or not item for item in selection)
        ):
            raise ValueError("Choose explicit job IDs")
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
                        held_at=(job.get("held_at") if continuing else None) or self.clock(),
                        pdf=str(pdf),
                        resume_sha256=_file_digest(pdf),
                        engine=selected,
                        lane=self.lane,
                        **({"debug_url": self.debug_url} if self.debug_url else {}),
                    )
                    # Preserve injected single-page bridges' existing signature.
                    native = getattr(self.browser, "native_uploads", False) is True
                    extra = {"pdf": str(pdf)} if native else {}
                    if getattr(self.browser, "answer_bank", False) is True:
                        from .answer_bank import AnswerBank

                        extra["bank"] = AnswerBank(self.root)
                    fill_result = self.browser.fill(tab_id, profile=profile, job=job, **extra)
                    unanswered = pending_fields(fill_result)
                    if fill_result.get("status") == "needs_manual" or fill_result.get("blocked"):
                        self.store.update(
                            job_id,
                            state="needs_manual",
                            fill_result=fill_result,
                            unanswered=unanswered,
                            **({"upload_receipt": fill_result["upload_receipt"]} if fill_result.get("upload_receipt") else {}),
                            problem=str(fill_result.get("blocked") or "Complete the held form manually"),
                        )
                        results.append({"job_id": job_id, "status": "needs_manual", "tab_id": tab_id,
                                        **({"questions": len(unanswered)} if unanswered else {})})
                        continue
                    if native:
                        receipt = fill_result.get("upload_receipt")
                        if not receipt:
                            raise ValueError("Observed upload receipt is missing; inspect the held Resume page")
                        self.store.update(job_id, upload_receipt=receipt)
                    else:
                        self.browser.upload_file(tab_id, str(pdf))
                    if unanswered:
                        # Held for the end-of-run popup; questions --answer refills it.
                        self.store.update(
                            job_id,
                            state="needs_manual",
                            fill_result=fill_result,
                            unanswered=unanswered,
                            problem=f"{len(unanswered)} required question(s) wait for the end-of-run popup",
                        )
                        results.append(
                            {"job_id": job_id, "status": "needs_manual", "tab_id": tab_id, "questions": len(unanswered)}
                        )
                        continue
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

    def refill(self, job_id, answers):
        """Fill the user's popup answers into one held tab; never submits or closes it."""
        if not isinstance(answers, dict) or not answers:
            raise ValueError("No answers to fill")
        with self._operation_lock(job_id):
            record = self.store.get(job_id)
            if record["state"] != "needs_manual" or not record.get("tab_id"):
                raise ValueError("Only a held tab waiting for answers can be refilled")
            check_answers(record.get("unanswered") or [], answers)
            tab_id = record["tab_id"]
            result = self.browser.refill(tab_id, job=record, answers=answers)
            filled = set(result.get("filled", []))
            fields = record.get("unanswered") or []
            remaining = [item for item in fields if item["id"] not in answers or item["id"] not in filled]
            try:
                live = self.browser.snapshot(tab_id, job_id=job_id)
            except ValueError as error:
                # Without a fresh readback nothing counts as filled; every field stays retryable.
                reason = f"Fresh readback failed: {error}"
                self.store.update(job_id, unanswered=fields, problem=reason)
                return {"job_id": job_id, "status": "needs_manual", "tab_id": tab_id,
                        "questions": len(fields), "reason": reason}
            # The adapter's own report is not proof: a field is done only when the
            # fresh readback shows the user's answer for it.
            wrong = unproven(fields, answers, live)
            remaining = [item for item in fields if item in remaining or item["id"] in wrong]
            if live.get("errors") or live.get("required_unknown"):
                # An unclean form keeps every field for questions --refill.
                remaining = fields
            blocked = result.get("blocked") or []
            if blocked:
                # A blocked field stays open even when the readback looks right,
                # so questions lists the job and questions --refill retries it.
                held = _blocked_fields(fields, blocked)
                remaining = [item for item in fields if item in remaining or item in held]
            if remaining or blocked or live.get("errors") or live.get("required_unknown"):
                problem = _blocked_reason(blocked) or "Held form still contains unresolved fields"
                self.store.update(job_id, unanswered=remaining, problem=problem)
                return {"job_id": job_id, "status": "needs_manual", "tab_id": tab_id, "questions": len(remaining)}
            self.store.update(job_id, state="filled", unanswered=[], problem=None, fill_snapshot=live)
            return {"job_id": job_id, "status": "filled", "tab_id": tab_id}

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
