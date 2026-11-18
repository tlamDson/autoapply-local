"""Rippling script driver: resume first, Rippling's parse, then confirmed facts on top; the form is held.

Rippling parses the uploaded resume and writes what it read (name, links) into
the form. The driver therefore uploads first, waits for that parse, and only
then writes confirmed facts, so the user's own facts win over the parse. A
parse that lands late is caught by reading every written fact back: a changed
fact is written again a bounded number of times, then reported. A
question with no confirmed, exactly scoped answer is left as it is and reported.
The driver never clicks a button; the user reviews and submits the held tab.
"""

import hashlib
import json
import math
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from .profile import resolve_answer

ATS = "rippling"
ADAPTERS = Path(__file__).with_name("adapters")
# Rippling's fixed field names -> contact facts, in order of preference.
CONTACT = {
    "first_name": ("first_name",),
    "last_name": ("last_name",),
    "email": ("application_email",),
    "phone_number": ("phone",),
    "nickname": ("preferred_name",),
    "linkedin_link": ("linkedin",),
    "website_link": ("portfolio", "github"),
}
# Adapter calls the driver may make, with the MCP readback each one fits
# (browser.EVALUATE_SCHEMAS); none of them clicks a control.
ACTIONS = {"modelReady": "fill", "modelScan": "audit", "modelFill": "fill", "modelCheck": "fill", "location": "fill"}
APPLY_PATH = re.compile(r"(?:/[A-Za-z]{2}-[A-Za-z]{2})?/[^/]+/jobs/[^/]+(?:/apply)?/?")


def matches(url):
    """True for an ats.rippling.com job or apply URL."""
    parts = urlsplit(str(url))
    return parts.scheme == "https" and parts.hostname == "ats.rippling.com" and bool(APPLY_PATH.fullmatch(parts.path))


def script(action, payload=None):
    """One evaluate_script function: common.cjs, rippling.cjs, then a single adapter call."""
    if action not in ACTIONS:
        raise ValueError("Unsupported Rippling adapter action")
    code = "\n".join((ADAPTERS / name).read_text(encoding="utf-8") for name in ("common.cjs", "rippling.cjs"))
    argument = "" if payload is None else ", " + json.dumps(payload, ensure_ascii=False)
    return f"async () => {{\n{code}\nreturn await globalThis.AutoApplyRippling.{action}(document{argument});\n}}"


def _confirmed(answer):
    return isinstance(answer, dict) and answer.get("status") == "confirmed" and answer.get("value") not in (None, "")


def location_query(profile):
    """'City, ST' from confirmed contact facts, or None."""
    contact = profile.get("contact", {})
    city, state = contact.get("city"), contact.get("state")
    if _confirmed(city) and _confirmed(state):
        return f"{city['value']}, {state['value']}"
    return None


def plan(profile, fields, job):
    """Confirmed answers keyed by Rippling field name, the location query, and what stays unknown."""
    answers, unknown, location = {}, [], None
    contact = profile.get("contact", {})
    for field in fields:
        name = field["id"]
        if field.get("type") == "file":
            continue
        if name == "location":
            location = location_query(profile)
            if location is None:
                unknown.append(_unknown(field))
            continue
        if name in CONTACT:
            supplied = next((contact[key] for key in CONTACT[name] if _confirmed(contact.get(key))), None)
        else:
            # Custom, EEO and other employer questions take only an answer
            # confirmed for this exact question and scope, never a contact
            # fact that happens to share the label.
            supplied = resolve_answer(profile, field.get("label", ""), _scope(field, job))
        if _confirmed(supplied):
            answers[name] = supplied
        else:
            unknown.append(_unknown(field))
    return {"answers": answers, "unknown": unknown, "location": location}


def _scope(field, job):
    """The same exact scope profile.resolve_field uses for a question."""
    return {
        "country": job.get("country"),
        "job_type": job.get("level"),
        "employer": job.get("company"),
        "question": field.get("label", ""),
        "effective_at": job.get("start_date"),
    }


def _unknown(field):
    return {"id": field["id"], "label": field.get("label", ""), "required": bool(field.get("required"))}


def _receipt(path):
    data = path.read_bytes()
    return {"name": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class RipplingDriver:
    """Fill one held Rippling tab through an MCP client with evaluate_script and upload_file."""

    def __init__(self, client, *, sleep=time.sleep, parse_wait_s=15, poll_s=0.5, rewrites=2):
        self.client, self.sleep, self.rewrites = client, sleep, rewrites
        self.polls = max(1, math.ceil(parse_wait_s / poll_s)) if poll_s > 0 else 1
        self.poll_s = poll_s

    def call(self, tab_id, action, payload=None):
        result = self.client.evaluate_script(tab_id, script(action, payload), operation=ACTIONS[action])
        if not isinstance(result, dict):
            raise ValueError("Rippling adapter readback is not an object")
        return result

    def wait_parsed(self, tab_id, name):
        for attempt in range(self.polls):
            if "resume" in self.call(tab_id, "modelReady", {"name": name}).get("filled", []):
                return True
            if attempt + 1 < self.polls:
                self.sleep(self.poll_s)
        return False

    def fill(self, tab_id, profile, job, pdf=None):
        receipt = None
        if pdf is not None:
            path = Path(pdf).resolve()
            if not path.is_file() or not path.read_bytes().startswith(b"%PDF-"):
                raise ValueError("Upload requires an existing PDF")
            receipt = _receipt(path)
            self.client.upload_file(tab_id, str(path))
            if not self.wait_parsed(tab_id, receipt["name"]):
                return self._held([{"id": "resume", "reason": "resume_parse_timeout"}], [], [], receipt)
        scanned = self.call(tab_id, "modelScan")
        if not scanned.get("page", {}).get("formsy"):
            return self._held([{"id": "form", "reason": "form_model_missing"}], [], [], receipt)
        steps = plan(profile, scanned["fields"], job)
        result = self.call(tab_id, "modelFill", steps["answers"]) if steps["answers"] else {}
        blocked = list(result.get("blocked", []))
        filled = list(result.get("filled", []))
        if steps["location"]:
            placed = self.call(tab_id, "location", {"query": steps["location"]})
            blocked.extend(placed.get("blocked", []))
        overwritten = self.settle(tab_id, {name: steps["answers"][name] for name in filled if name in steps["answers"]})
        blocked.extend({"id": name, "reason": "value_overwritten"} for name in overwritten)
        filled = [name for name in filled if name not in overwritten]
        after = self.call(tab_id, "modelScan")
        for field in after.get("fields", []):
            if field["id"] == "resume" and receipt and receipt["name"] not in (field.get("value") or []):
                blocked.append({"id": "resume", "reason": "resume_not_attached"})
            elif field.get("required") and field.get("value") in ("", None, []):
                if not any(item["id"] == field["id"] for item in blocked):
                    blocked.append({"id": field["id"], "reason": "required_empty"})
        valid = after.get("page", {}).get("valid")
        return self._held(blocked, filled, steps["unknown"], receipt, valid)

    def settle(self, tab_id, written):
        """Names whose confirmed value a late parse still overwrites after the bounded rewrites."""
        changed = []
        for attempt in range(self.rewrites + 1):
            if not written:
                return []
            self.sleep(self.poll_s)
            check = self.call(tab_id, "modelCheck", written)
            changed = [
                item["id"]
                for item in check.get("blocked", [])
                if item.get("reason") == "value_changed" and item.get("id") in written
            ]
            if not changed:
                return []
            if attempt < self.rewrites:
                self.call(tab_id, "modelFill", {name: written[name] for name in changed})
        return changed

    @staticmethod
    def _held(blocked, filled, unknown, receipt, valid=False):
        return {
            "status": "needs_manual" if blocked else "filled",
            "filled": list(filled),
            "blocked": blocked,
            "unknown": unknown,
            "upload_receipt": receipt,
            "form_valid": bool(valid),
        }
