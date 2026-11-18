"""Greenhouse script driver: maps Greenhouse job-board fields to confirmed facts.

Standard Greenhouse inputs have stable IDs (first_name, email, ...); custom
questions are ``question_<n>`` and are matched by an exact link label. EEO IDs
use the user's confirmed voluntary answers. Everything else falls back to the
generic resolver (exact contact labels and question-scoped answers), so
authorization or sponsorship is never inferred.
"""

import re

from .script_driver import ScriptDriver

CONTACT_IDS = {
    "first_name": "first_name",
    "last_name": "last_name",
    "preferred_name": "preferred_name",
    "email": "application_email",
    "phone": "phone",
    "candidate-location": "city",
}
CONTACT_LABELS = (
    (re.compile(r"^linkedin( profile)?( url)?$"), "linkedin"),
    (re.compile(r"^github( profile)?( url)?$"), "github"),
    (re.compile(r"^(personal )?(website|portfolio)( url)?$"), "portfolio"),
)
EEO_IDS = {"gender", "race", "hispanic_ethnicity", "veteran_status", "disability_status"}


def _normalize(label):
    return " ".join(str(label or "").casefold().replace("*", " ").split())


class GreenhouseDriver(ScriptDriver):
    name = "greenhouse"

    def known(self, field, profile, job):
        key = str(field.get("id", ""))
        contact = profile.get("contact", {})
        if key in CONTACT_IDS:
            return contact.get(CONTACT_IDS[key], {})
        label = _normalize(field.get("label"))
        for pattern, fact_key in CONTACT_LABELS:
            if pattern.match(label):
                return contact.get(fact_key, {})
        if key in EEO_IDS:
            eeo = (profile.get("compensation") or {}).get("eeo")
            answers = eeo.get("value") if isinstance(eeo, dict) and eeo.get("status") == "confirmed" else None
            value = answers.get(key) if isinstance(answers, dict) else None
            return {"value": value, "status": "confirmed" if value not in (None, "") else "unknown"}
        return None
