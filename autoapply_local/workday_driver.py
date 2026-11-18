"""Workday script driver: fill each application step from confirmed facts and stop on Review.

Workday splits the application into steps (My Information, My Experience,
Application Questions, Voluntary Disclosures, ...). On each form step the
driver scans, plans and fills like every script driver, types the
React-controlled name boxes with ``insertText`` (the plain setter is ignored
there), uploads the selected resume on the step that asks for it (counted only
when the browser reports it and a fresh audit shows the file), and presses
Next only when no required field is left. It stops on the first step with a
required unknown field (``needs_engine``), on validation errors after Next, and
on Review, which it never presses: the user reviews and submits by hand.

Contact facts map by Workday's stable field IDs; every other question goes to
the generic resolver (exact question-scoped answers), so authorization or
sponsorship is never inferred. Sign-in, account creation and the verification
code are ``WorkdayAccount``'s job; without it a sign-in page holds the tab.
"""

import re
from pathlib import PurePath

from . import workday_steps
from .script_driver import ScriptDriver

FIELD_FACTS = {
    "name--legalName--firstName": "first_name",
    "name--legalName--lastName": "last_name",
    "address--addressLine1": "street",
    "address--city": "city",
    "address--postalCode": "postcode",
    "phoneNumber--phoneNumber": "phone",
    "email": "application_email",
    "socialNetworkAccounts--linkedInAccount": "linkedin",
}
OTHER_FILES = re.compile(r"cover|letter|transcript|portfolio|writing|sample|other", re.I)
ACCOUNT_PAGES = ("job", "start", "sign_in", "create_account", "verify_code", "verify_email")
HOLD_PAGES = {"captcha": "captcha", "applied": "already_applied"}


def _hold(report, reason, field="page"):
    report["blocked"].append({"id": field, "reason": reason})
    report["status"] = "needs_manual"
    return report


class WorkdayDriver(ScriptDriver):
    name = "workday"
    max_steps = 12

    def known(self, field, profile, job):
        key = FIELD_FACTS.get(str(field.get("id", "")))
        return profile.get("contact", {}).get(key, {}) if key else None

    def is_resume(self, field):
        # The application's file input is the resume unless its label names another document.
        return field.get("type") == "file" and not OTHER_FILES.search(str(field.get("label", "")))

    def _step(self, client, tab_id, script):
        return client.evaluate_script(tab_id, script, operation=workday_steps.OPERATION)

    def run(self, client, tab_id, profile, job, pdf=None, account=None, bank=None):
        """Fill every step up to Review; never presses Review's Submit and never closes the tab."""
        report = {
            "driver": self.name,
            "filled": [],
            "blocked": [],
            "uploads": [],
            "unknown": [],
            "resume_field": None,
            "steps": [],
            "account": None,
            "review": None,
            "status": "needs_manual",
        }
        if account is not None:
            signed = account.ensure_signed_in(job)
            report["account"] = signed
            if signed.get("status") == "already_applied":
                return _hold(report, "already_applied")
            if signed.get("status") != "signed_in":
                return _hold(report, signed.get("reason") or "sign_in_required")
        for _ in range(self.max_steps):
            where = self._step(client, tab_id, workday_steps.script("where"))
            page = where.get("page") if isinstance(where, dict) else None
            if page == "review":
                report["review"], report["status"] = where.get("step"), "filled"
                return report
            if page != "form" or not isinstance(where.get("step"), dict):
                if page in ACCOUNT_PAGES:
                    return _hold(report, "sign_in_required")
                if page in HOLD_PAGES:
                    return _hold(report, HOLD_PAGES[page])
                blocked = where.get("blocked") if isinstance(where, dict) else None
                if isinstance(blocked, list) and blocked:
                    report["blocked"].extend(blocked)
                    report["status"] = "needs_manual"
                    return report
                return _hold(report, "form_not_readable")
            if not self._fill_step(client, tab_id, profile, job, pdf, where, report, bank):
                return report
            moved = self._step(client, tab_id, workday_steps.press_script("next", where))
            if not isinstance(moved, dict) or not moved.get("pressed"):
                return _hold(report, (moved.get("reason") if isinstance(moved, dict) else None) or "press_failed")
            if not moved.get("moved"):
                errors = list(((moved.get("where") or {}).get("errors")) or [])
                report["blocked"].append({"id": "page", "reason": "validation_error", "errors": errors})
                report["status"] = "needs_engine"
                return report
        return _hold(report, "too_many_steps")

    def _fill_step(self, client, tab_id, profile, job, pdf, where, report, bank=None):
        """Fill the current step; False when the run stops on it."""
        step = where["step"]
        title = step.get("title")
        page, answers, fields = self.fill_page(client, tab_id, profile, job, pdf=pdf, bank=bank)
        self._type_blocked(client, tab_id, page, answers)
        report["filled"].extend(page["filled"])
        report["blocked"].extend(page["blocked"])
        report["unknown"].extend({**item, "step": title} for item in page["unknown"])
        report["resume_field"] = page["resume_field"] or report["resume_field"]
        report["steps"].append(
            {
                "index": step.get("index"),
                "total": step.get("total"),
                "title": title,
                "filled": list(page["filled"]),
                "unknown": [item["id"] for item in page["unknown"]],
            }
        )
        if page["status"] == "needs_manual":
            report["status"] = "needs_manual"
            return False
        if pdf is not None and page["resume_field"] and not self._upload(client, tab_id, pdf, page, fields, report):
            return False
        if any(item.get("required") for item in page["unknown"]):
            report["status"] = "needs_engine"
            return False
        return True

    def _type_blocked(self, client, tab_id, page, answers):
        values = {
            item["id"]: answers[item["id"]]
            for item in page["blocked"]
            if item.get("reason") == "agent_interaction_required" and isinstance(answers.get(item.get("id")), str)
        }
        if not values:
            return
        result = self._step(client, tab_id, workday_steps.script("typeText", values))
        done = set((result or {}).get("typed") or []) & set(values) if isinstance(result, dict) else set()
        page["filled"].extend(key for key in values if key in done)
        page["blocked"] = [item for item in page["blocked"] if item.get("id") not in done]
        page["unknown"] = [item for item in page["unknown"] if item.get("id") not in done]

    def _upload(self, client, tab_id, pdf, page, fields, report):
        field_id = page["resume_field"]
        current = str(next((item.get("value") for item in fields if item.get("id") == field_id), "") or "")
        if current:
            if current != PurePath(str(pdf)).name:
                _hold(report, "other_resume_attached", field_id)
                return False
            return True
        upload = getattr(client, "upload_file", None)
        if not callable(upload):
            _hold(report, "resume_upload_required", field_id)
            return False
        result = upload(tab_id, str(pdf))
        if not isinstance(result, dict) or result.get("status") != "uploaded":
            _hold(report, "resume_upload_failed", field_id)
            return False
        # Success is what the page shows: a fresh audit must list the PDF in the field.
        audited = client.evaluate_script(tab_id, self.script("audit"), operation="audit")
        fields = audited.get("fields") if isinstance(audited, dict) else None
        field = next((item for item in fields or [] if item.get("id") == field_id), None)
        names = [str(file.get("name")) for file in (field or {}).get("files") or [] if isinstance(file, dict)]
        if PurePath(str(pdf)).name not in [str((field or {}).get("value") or ""), *names]:
            _hold(report, "resume_upload_not_observed", field_id)
            return False
        report["uploads"].append({"id": field_id, "path": str(pdf), "status": "uploaded"})
        return True
