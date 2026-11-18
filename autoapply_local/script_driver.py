"""Script driver interface: fill known fields from confirmed facts, report the rest.

A script driver scans the held tab with its ATS adapter, plans an answer for
every field a confirmed fact or exact scoped answer covers, fills those through
the adapter and returns the remaining fields as ``unknown``. Only ``unknown``
fields need the engine. Drivers never click: a script that could click or submit
is refused before it reaches the browser.
"""

import json
import re
from pathlib import Path

ADAPTERS = {
    "greenhouse": "AutoApplyGreenhouse",
    "lever": "AutoApplyLever",
    "rippling": "AutoApplyRippling",
    "workday": "AutoApplyWorkday",
}
CLICKS = re.compile(r"\.click\s*\(|requestSubmit|\.submit\s*\(")
CHOICE_TYPES = {"select", "select-one", "radio"}
WIDGET_TYPES = {"combobox", "listbox", "checkbox-group"}

AUDIT_READBACK = """
for (const field of result.fields) {
  const element = globalThis.AutoApplyDOM.resolve(document, field.id);
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
        action = f"const result = globalThis.{adapter}.audit(document);" + AUDIT_READBACK
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

    def plan(self, fields, profile, job, pdf=None):
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
            if not _confirmed(supplied):
                if _empty(field.get("value")):
                    unknown.append({**entry, "reason": "no_confirmed_fact"})
                continue
            value = supplied["value"]
            if kind in WIDGET_TYPES:
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

    def run(self, client, tab_id, profile, job, pdf=None):
        """Scan, plan and fill one held tab; never clicks and never closes it."""
        scanned = client.evaluate_script(tab_id, self.script("audit"), operation="audit")
        if not isinstance(scanned, dict) or "fields" not in scanned:
            # Sign-in, account creation, CAPTCHA or an unreadable page: hold the
            # tab for the user before any fill or upload.
            blocked = scanned.get("blocked") if isinstance(scanned, dict) else None
            return {
                "driver": self.name,
                "filled": [],
                "blocked": list(blocked or [{"id": None, "reason": "form_not_readable"}]),
                "uploads": [],
                "unknown": [],
                "resume_field": None,
                "status": "needs_manual",
            }
        fields = scanned["fields"]
        plan = self.plan(fields, profile, job, pdf=pdf)
        # Confirmed-status objects keep the adapter's own unconfirmed-answer guard.
        confirmed = {key: {"value": value, "status": "confirmed"} for key, value in plan["answers"].items()}
        result = client.evaluate_script(tab_id, self.script("fill", confirmed), operation="fill")
        unknown = list(plan["unknown"])
        listed = {item["id"] for item in unknown}
        described = {field.get("id"): field for field in fields}
        for item in result.get("blocked", []):
            if item.get("id") in listed:
                continue
            field = described.get(item.get("id"), {})
            unknown.append(
                {
                    "id": item.get("id"),
                    "label": field.get("label"),
                    "type": field.get("type"),
                    "required": bool(field.get("required")),
                    "options": field.get("options") or [],
                    "reason": item.get("reason"),
                }
            )
            listed.add(item.get("id"))
        return {
            "driver": self.name,
            "filled": list(result.get("filled", [])),
            "blocked": list(result.get("blocked", [])),
            "uploads": list(result.get("uploads", [])),
            "unknown": unknown,
            "resume_field": plan["resume"],
            "status": "needs_engine" if any(item["required"] for item in unknown) else "filled",
        }


def driver_for(ats):
    """The script driver for an ATS, or None when only the engine can fill it."""
    from .greenhouse_driver import GreenhouseDriver
    from .lever_driver import LeverDriver

    return {"greenhouse": GreenhouseDriver, "lever": LeverDriver}.get(ats, lambda: None)()
