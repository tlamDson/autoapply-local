"""Script driver interface: fill known fields from confirmed facts, report the rest.

A script driver scans the held tab with its ATS adapter, plans an answer for
every field a confirmed fact or exact scoped answer covers, fills those through
the adapter and returns the remaining fields as ``unknown``. Only ``unknown``
fields need the engine. Drivers never click: a script that could click or submit
is refused before it reaches the browser.
"""

import hashlib
import json
import re
from pathlib import Path

ADAPTERS = {
    "greenhouse": "AutoApplyGreenhouse",
    "lever": "AutoApplyLever",
    "rippling": "AutoApplyRippling",
    "workable": "AutoApplyWorkable",
    "workday": "AutoApplyWorkday",
    "ashby": "AutoApplyAshby",
}
CLICKS = re.compile(r"\.click\s*\(|requestSubmit|\.submit\s*\(")
CHOICE_TYPES = {"select", "select-one", "radio"}
WIDGET_TYPES = {"combobox", "listbox", "checkbox-group"}

AUDIT_READBACK = """
for (const field of result.fields) {
  const element = globalThis.__ADAPTER__.resolve(document, field.id);
  if (element?.type === 'file') {
    field.files = [];
    for (const file of element.files || []) {
      const bytes = await file.arrayBuffer();
      const digest = await crypto.subtle.digest('SHA-256', bytes);
      const sha256 = Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, '0')).join('');
      field.files.push({name: file.name, size: file.size, sha256});
    }
  }
  if (field.type !== 'summary') field.observed = Boolean(element);
}
result.attachments = result.fields.filter(field => field.type === 'summary' && /^(resume|resume pdf)$/i.test(field.label))
  .map(field => ({name: field.value, observed: true, evidence: field.evidence}));
result.url = location.href;
return result;"""


def adapter_code(ats):
    if ats not in ADAPTERS:
        raise ValueError("Unsupported ATS requires manual interaction")
    directory = Path(__file__).with_name("adapters")
    return (directory / "common.cjs").read_text(encoding="utf-8") + "\n" + (directory / (ats + ".cjs")).read_text(
        encoding="utf-8"
    )


def adapter_script(ats, operation, answers=None):
    """The evaluate_script function that runs one adapter operation in the tab."""
    code, adapter = adapter_code(ats), ADAPTERS[ats]
    if operation == "fill":
        action = f"return await globalThis.{adapter}.fill(document, {json.dumps(answers or {}, ensure_ascii=False)});"
    else:
        action = f"const result = globalThis.{adapter}.audit(document);" + AUDIT_READBACK.replace("__ADAPTER__", adapter)
    return "async () => {\n" + code + "\n" + action + "\n}"


def _empty(value):
    return value in ("", None, False) or (isinstance(value, list) and not value)


def _confirmed(answer):
    return isinstance(answer, dict) and answer.get("status") == "confirmed" and answer.get("value") not in (None, "")


class ScriptDriver:
    """Base class; an ATS driver sets ``name`` and may override ``known``/``is_resume``."""

    name = None
    # True when the ATS parses the uploaded resume and overwrites fields with
    # what it read, so the resume must be uploaded before the planned fill.
    upload_before_fill = False
    # Field types that only native engine interaction can set; the planned
    # confirmed value travels with the field to the engine.
    widget_types = WIDGET_TYPES
    # True when production (the engine's MCP client) runs this driver first and
    # hands the engine only the fields the script could not set.
    runs_before_engine = False
    # CSS selector of the resume file input for the DevTools upload; None = the page's only one.
    resume_input = None

    def known(self, field, profile, job):
        """ATS-specific fact for a field, or None to use the generic resolver."""
        return None

    def is_resume(self, field):
        return field.get("type") == "file" and re.match(r"^(resume|cv)\b", str(field.get("label", "")), re.I)

    def script(self, operation, answers=None):
        script = adapter_script(self.name, operation, answers)
        if CLICKS.search(script):
            raise ValueError(f"The {self.name} adapter can click; a script driver never clicks or submits")
        return script

    def plan(self, fields, profile, job, pdf=None, bank=None):
        """Answers from confirmed facts, then from the user's answer bank."""
        from .profile import resolve_field

        answers, unknown, resume = {}, [], None
        for field in fields:
            kind = field.get("type")
            if kind == "summary" or not field.get("id"):
                continue
            entry = {key: field.get(key) for key in ("id", "label", "type", "required", "options")}
            entry["required"] = bool(entry["required"])
            entry["options"] = entry["options"] or []
            if kind == "file" and self.is_resume(field):
                resume = field["id"]
                if pdf is not None:
                    answers[field["id"]] = str(pdf)
                continue
            supplied = self.known(field, profile, job)
            if supplied is None and kind != "file":
                supplied = resolve_field(profile, field, job)
            if not _confirmed(supplied) and bank is not None and kind != "file":
                supplied = bank.lookup(field, job) or supplied
            if not _confirmed(supplied):
                if _empty(field.get("value")):
                    unknown.append({**entry, "reason": "no_confirmed_fact"})
                continue
            value = supplied["value"]
            if kind in self.widget_types:
                unknown.append({**entry, "reason": "agent_interaction_required", "value": value})
            elif kind in CHOICE_TYPES:
                matches = [
                    option["value"]
                    for option in entry["options"]
                    if option.get("value") == value or str(option.get("label", "")).casefold() == str(value).casefold()
                ]
                if len(matches) == 1 and matches[0] != "":
                    answers[field["id"]] = matches[0]
                else:
                    unknown.append({**entry, "reason": "option_not_found", "value": value})
            elif kind == "checkbox" and type(value) is not bool:
                unknown.append({**entry, "reason": "boolean_required", "value": value})
            else:
                answers[field["id"]] = value
        return {"answers": answers, "unknown": unknown, "resume": resume}

    def _held(self, scanned):
        blocked = scanned.get("blocked") if isinstance(scanned, dict) else None
        return {
            "driver": self.name,
            "filled": [],
            "blocked": list(blocked or [{"id": None, "reason": "form_not_readable"}]),
            "uploads": [],
            "upload_receipt": None,
            "unknown": [],
            "resume_field": None,
            "status": "needs_manual",
        }

    def _held_page(self, scanned):
        return self._held(scanned), {}, []

    def run(self, client, tab_id, profile, job, pdf=None, bank=None):
        """Scan, plan and fill one held tab; never clicks and never closes it."""
        return self.fill_page(client, tab_id, profile, job, pdf=pdf, bank=bank)[0]

    def fill_page(self, client, tab_id, profile, job, pdf=None, bank=None):
        """``run`` for the current page: the report, the planned answers and the scanned fields.

        With ``upload_before_fill`` and a selected PDF, the resume is uploaded
        first and the tab scanned again, so the fill plans from what the ATS
        parsed; the upload must show in the browser's own File readback.
        """
        scanned = client.evaluate_script(tab_id, self.script("audit"), operation="audit")
        if not isinstance(scanned, dict) or "fields" not in scanned:
            # Sign-in, account creation, CAPTCHA or an unreadable page: hold the
            # tab for the user before any fill or upload.
            return self._held_page(scanned)
        fields, uploaded, receipt = scanned["fields"], [], None
        resume = next((item["id"] for item in fields if item.get("id") and self.is_resume(item)), None)
        if self.upload_before_fill and pdf is not None and resume is None:
            # The fill would plan from an unparsed form and nobody could prove the upload.
            return self._held_page({"blocked": [{"id": None, "reason": "resume_field_not_found"}]})
        if self.upload_before_fill and pdf is not None:
            status = client.upload_file(tab_id, str(pdf))
            if isinstance(status, dict) and status.get("status") == "needs_manual":
                return self._held_page({"blocked": [{"id": resume, "reason": "upload_needs_manual"}]})
            scanned = client.evaluate_script(tab_id, self.script("audit"), operation="audit")
            if not isinstance(scanned, dict) or "fields" not in scanned:
                return self._held_page(scanned)
            fields = scanned["fields"]
            receipt = observed_receipt(fields, resume, pdf)
            if receipt is None:
                return self._held_page({"blocked": [{"id": resume, "reason": "upload_not_observed"}]})
            uploaded, pdf = [{"id": resume, "path": str(pdf)}], None
        plan = self.plan(fields, profile, job, pdf=pdf, bank=bank)
        # Confirmed-status objects keep the adapter's own unconfirmed-answer guard.
        confirmed = {key: {"value": value, "status": "confirmed"} for key, value in plan["answers"].items()}
        result = client.evaluate_script(tab_id, self.script("fill", confirmed), operation="fill")
        unknown = list(plan["unknown"])
        listed = {item["id"] for item in unknown}
        described = {field.get("id"): field for field in fields}
        held = False
        for item in result.get("blocked", []):
            if item.get("reason") == "submit_control":
                # The control could submit the form: never hand it to the
                # engine's native click; the user answers it by hand.
                held = True
                unknown = [entry for entry in unknown if entry["id"] != item.get("id")]
                listed.add(item.get("id"))
                continue
            if item.get("id") in listed:
                continue
            field = described.get(item.get("id"), {})
            entry = {
                "id": item.get("id"),
                "label": field.get("label"),
                "type": field.get("type"),
                "required": bool(field.get("required")),
                "options": field.get("options") or [],
                "reason": item.get("reason"),
            }
            if "option" in item:
                entry["option"] = item["option"]
            unknown.append(entry)
            listed.add(item.get("id"))
        report = {
            "driver": self.name,
            "filled": list(result.get("filled", [])),
            "blocked": list(result.get("blocked", [])),
            "uploads": uploaded + list(result.get("uploads", [])),
            "upload_receipt": receipt,
            "unknown": unknown,
            "resume_field": plan["resume"],
            "status": "needs_manual"
            if held
            else "needs_engine"
            if any(item["required"] for item in unknown)
            else "filled",
        }
        return report, plan["answers"], fields


def refill_script(ats, answers):
    """The fill script for answers the user gave after the run; never clicks."""
    driver = driver_for(ats)
    if driver is None:
        raise ValueError("Unsupported ATS requires manual interaction")
    return driver.script("fill", {key: {"value": value, "status": "confirmed"} for key, value in answers.items()})


def observed_receipt(fields, resume, pdf):
    """The browser's File readback of the resume field when it is exactly the selected PDF."""
    data = Path(pdf).read_bytes()
    expected = {"name": Path(pdf).name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    for field in fields:
        if field.get("id") == resume:
            for file in field.get("files") or []:
                if isinstance(file, dict) and all(file.get(key) == value for key, value in expected.items()):
                    return expected
    return None


def driver_for(ats):
    """The script driver for an ATS, or None when only the engine can fill it."""
    from .ashby_driver import AshbyDriver
    from .greenhouse_driver import GreenhouseDriver
    from .lever_driver import LeverDriver
    from .workable_driver import WorkableDriver
    from .workday_driver import WorkdayDriver

    drivers = {
        "ashby": AshbyDriver,
        "greenhouse": GreenhouseDriver,
        "lever": LeverDriver,
        "workable": WorkableDriver,
        "workday": WorkdayDriver,
    }
    return drivers.get(ats, lambda: None)()
