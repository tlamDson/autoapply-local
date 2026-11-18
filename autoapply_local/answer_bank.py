"""Answer bank: the user's own answers to form questions no profile fact covers.

Each entry is an answer the user gave in the end-of-run popup, keyed by the
exact question: its normalized label, its kind (choice, boolean or text), its
option labels and the job's country and job type. Questions about work
authorization, sponsorship, consent, EEO or pay (in the label or in a money
option) also keep the employer and start date, the full scope a profile answer needs. A question outside that scope is a
different question and is asked again. The bank
lives in the runtime root (``user/answer-bank.json``) and never holds inferred
answers: only explicit popup answers become ``confirmed`` entries.
"""

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .storage import atomic_write_json, read_json

FILE = "user/answer-bank.json"
JOB_SCOPE = ("country", "level", "company", "start_date")
SENSITIVE = re.compile(
    r"authori[sz]|sponsor|visa|citizen|immigra|work permit|right to work|clearance|export|itar|consent|"
    r"criminal|convict|background check|gender|race|ethnic|hispanic|veteran|disab|pronoun|sexual|"
    # Pay: any wording of salary, wage, rate or money amount keeps the full scope.
    r"salar|compensat|remunerat|\bpay|wage|stipend|hourly|\brates?\b|per (?:hour|year|annum|month|week)|"
    r"annual|income|\bearn|bonus|equity|\$|€|£|\busd\b|\beur\b|\bgbp\b",
    re.I,
)
CHOICE_TYPES = {"select", "select-one", "radio", "combobox", "listbox", "checkbox-group"}


def normalize(text):
    return " ".join(str(text or "").casefold().split())


def options(field):
    """Option labels a user can pick, without the empty placeholder option."""
    labels = []
    for option in field.get("options") or []:
        if isinstance(option, dict):
            if option.get("value") in ("", None):
                continue
            label = option.get("label", option.get("value"))
        else:
            label = option
        if str(label).strip():
            labels.append(str(label).strip())
    return labels


def kind(field):
    if field.get("type") == "checkbox" and not field.get("options"):
        return "boolean"
    return "choice" if field.get("type") in CHOICE_TYPES or options(field) else "text"


def job_scope(job):
    """The job attributes a bank key may depend on."""
    return {name: (job or {}).get(name) for name in JOB_SCOPE}


def scope(field, job):
    job = job or {}
    result = {"country": normalize(job.get("country")), "job_type": normalize(job.get("level"))}
    if SENSITIVE.search(" ".join([str(field.get("label", "")), *options(field)])):
        result.update(employer=normalize(job.get("company")), effective_at=normalize(job.get("start_date")))
    return result


def key(field, job):
    identity = {
        "question": normalize(field.get("label")),
        "kind": kind(field),
        "options": sorted(normalize(label) for label in options(field)),
        **scope(field, job),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


class AnswerBank:
    def __init__(self, root):
        self.path = Path(root) / FILE

    def _load(self):
        if not self.path.exists():
            return {"schema_version": 1, "answers": []}
        data = read_json(self.path)
        if not isinstance(data, dict) or not isinstance(data.get("answers"), list) or any(
            not isinstance(entry, dict) or not isinstance(entry.get("key"), str) for entry in data["answers"]
        ):
            raise ValueError("The answer bank is not valid; restore user/answer-bank.json from its .bak copy")
        return data

    def lookup(self, field, job):
        """The confirmed answer for this exact question, or None."""
        wanted = key(field, job)
        for entry in reversed(self._load()["answers"]):
            if entry["key"] == wanted and entry.get("status") == "confirmed" and entry.get("value") not in (None, ""):
                return copy.deepcopy({name: entry[name] for name in ("value", "status", "provenance", "confirmed_at")})
        return None

    def entries_for(self, job):
        """Confirmed answers whose scope matches this job, for the engine fill."""
        found = {}
        for entry in self._load()["answers"]:
            if entry.get("status") != "confirmed" or entry.get("value") in (None, ""):
                continue
            field = {"label": entry.get("question", ""), "options": entry.get("options") or []}
            if entry.get("scope") != scope(field, job):
                continue
            found[entry["key"]] = {name: copy.deepcopy(entry.get(name)) for name in ("question", "kind", "options", "value")}
        return list(found.values())

    def confirm(self, field, job, value, *, source_id):
        """Save an explicit user answer; a newer answer replaces the older one."""
        if value in (None, "") or (kind(field) == "boolean" and type(value) is not bool):
            raise ValueError("Only an actual answer can be saved")
        data = self._load()
        wanted = key(field, job)
        data["answers"] = [entry for entry in data["answers"] if entry["key"] != wanted]
        data["answers"].append(
            {
                "key": wanted,
                "question": str(field.get("label", "")).strip(),
                "kind": kind(field),
                "options": options(field),
                "scope": scope(field, job),
                "value": value,
                "status": "confirmed",
                "provenance": {"kind": "user_confirmation", "source_id": source_id},
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        atomic_write_json(self.path, data)
