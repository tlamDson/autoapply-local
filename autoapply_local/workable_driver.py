"""Workable script driver: maps Workable application-form fields to confirmed facts.

The Workable adapter binds each control to the form-API id of its
``data-ui`` box. Standard fields use fixed ids (``firstname``, ``lastname``,
``email``, ``phone``, ``resume``); custom questions are ``QA_<n>`` and fall back
to the generic resolver (exact question-scoped answers), so authorization or
sponsorship is never inferred. Yes/No questions are role=radio buttons and
education/experience are entry lists behind an Add control: only the engine can
set them, so the driver hands it the confirmed value. Workable parses an
uploaded resume into those entries, so the resume goes in before the fill.
"""

from .script_driver import WIDGET_TYPES, ScriptDriver

CONTACT_IDS = {"firstname": "first_name", "lastname": "last_name", "email": "application_email", "phone": "phone"}
# Record fields handed to the engine per entry list; the first ones identify an entry.
GROUPS = {
    "education": (("school",), ("degree", "major")),
    "experience": (("title", "employer"), ()),
}


def _confirmed(answer):
    return isinstance(answer, dict) and answer.get("status") == "confirmed" and answer.get("value") not in (None, "")


def _entries(records, identity, extra):
    entries = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict) or not all(_confirmed(record.get(name)) for name in identity):
            continue
        entries.append({name: record[name]["value"] for name in identity + extra if _confirmed(record.get(name))})
    return entries


class WorkableDriver(ScriptDriver):
    name = "workable"
    upload_before_fill = True
    runs_before_engine = True
    resume_input = '[data-ui="resume"] input[type="file"]'
    widget_types = WIDGET_TYPES | {"radiogroup", "group"}

    def is_resume(self, field):
        return field.get("type") == "file" and field.get("id") == "resume"

    def known(self, field, profile, job):
        key = str(field.get("id", ""))
        if key in CONTACT_IDS:
            return profile.get("contact", {}).get(CONTACT_IDS[key], {})
        if field.get("type") == "group" and key in GROUPS:
            entries = _entries(profile.get(key), *GROUPS[key])
            return {"value": entries, "status": "confirmed"} if entries else {"value": None, "status": "unknown"}
        return None

    def plan(self, fields, profile, job, pdf=None, bank=None):
        result = super().plan(fields, profile, job, pdf=pdf, bank=bank)
        for item in result["unknown"]:
            if item["type"] == "radiogroup" and item["reason"] == "agent_interaction_required":
                wanted = str(item["value"]).casefold()
                if not any(wanted in (str(o.get("value")).casefold(), str(o.get("label")).casefold()) for o in item["options"]):
                    # Not one of the buttons: report it, never hand a guess on.
                    item["reason"] = "option_not_found"
        return result
