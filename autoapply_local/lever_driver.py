"""Lever script driver: maps Lever application-form fields to confirmed facts.

The Lever adapter binds each control to its form name. Standard inputs use
fixed names (``name``, ``email``, ``phone``, ``location``, ``urls[LinkedIn]``,
``eeo[gender]``); custom questions are ``cards[<id>][fieldN]`` and fall back to
the generic resolver (exact question-scoped answers), so authorization or
sponsorship is never inferred. Lever parses an uploaded resume and overwrites
fields with what it read, so the resume goes in before the planned fill.
"""

import re

from .script_driver import ScriptDriver

CONTACT_NAMES = {"email": "application_email", "phone": "phone", "location": "city"}
URL_NAMES = {"linkedin": "linkedin", "github": "github", "portfolio": "portfolio", "website": "portfolio"}
EEO_NAMES = {
    "gender": "gender",
    "race": "race",
    "veteran": "veteran_status",
    "disability": "disability_status",
}
URL = re.compile(r"^urls\[([^\]]+)\]$")
EEO = re.compile(r"^eeo\[([^\]]+)\]$")


def _confirmed(answer):
    return isinstance(answer, dict) and answer.get("status") == "confirmed" and answer.get("value") not in (None, "")


class LeverDriver(ScriptDriver):
    name = "lever"
    upload_before_fill = True

    def is_resume(self, field):
        return field.get("type") == "file" and field.get("id") == "resume"

    def known(self, field, profile, job):
        key = str(field.get("id", ""))
        contact = profile.get("contact", {})
        if key == "name":
            legal = contact.get("legal_name", {})
            if _confirmed(legal):
                return legal
            first, last = contact.get("first_name"), contact.get("last_name")
            if _confirmed(first) and _confirmed(last):
                return {"value": f"{first['value']} {last['value']}", "status": "confirmed"}
            return {"value": None, "status": "unknown"}
        if key in CONTACT_NAMES:
            return contact.get(CONTACT_NAMES[key], {})
        url = URL.match(key)
        if url and url.group(1).casefold() in URL_NAMES:
            return contact.get(URL_NAMES[url.group(1).casefold()], {})
        eeo_key = EEO.match(key)
        if eeo_key and eeo_key.group(1).casefold() in EEO_NAMES:
            eeo = (profile.get("compensation") or {}).get("eeo")
            answers = eeo.get("value") if isinstance(eeo, dict) and eeo.get("status") == "confirmed" else None
            value = answers.get(EEO_NAMES[eeo_key.group(1).casefold()]) if isinstance(answers, dict) else None
            return {"value": value, "status": "confirmed" if value not in (None, "") else "unknown"}
        return None
