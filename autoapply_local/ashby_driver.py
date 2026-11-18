"""Ashby script driver: maps Ashby system fields to confirmed contact facts.

Ashby names its standard inputs ``_systemfield_<name>``; other questions fall
back to the generic resolver (exact contact labels and question-scoped
answers), so authorization or sponsorship is never inferred. Yes/No toggles
with a confirmed answer come back from the adapter as ``native_click_required``
with the option to click, and are reported as unknown for the engine.
"""

from .script_driver import ScriptDriver

SYSTEM_FIELDS = {
    "_systemfield_name": "legal_name",
    "_systemfield_email": "application_email",
    "_systemfield_phone": "phone",
    "_systemfield_preferred_name": "preferred_name",
}


class AshbyDriver(ScriptDriver):
    name = "ashby"

    def known(self, field, profile, job):
        key = SYSTEM_FIELDS.get(str(field.get("id", "")))
        return profile.get("contact", {}).get(key, {}) if key else None
