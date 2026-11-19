"""Answer bank: the user's own answers to form questions no profile fact covers.

Each entry is an answer the user gave in the end-of-run popup, keyed by the
exact question: its normalized label, its kind (choice, multi, boolean or
text), its
option labels and the job's country and job type. Only a question known to be
about the candidate alone (education, skills, languages, clubs, projects and
the like) is shared across employers; every other question keeps the employer
and start date, and so does any question with one of these marks, whatever
else it mentions. A required checkbox, a yes/no or
agree/decline question, a question that refers to the employer or the role,
and any wording about work authorization, sponsorship, consent, agreement,
contact or data permission, EEO or pay (in the label or in a money option) also keep the employer and
start date, the full scope a profile answer needs. A question outside that
scope is a different question and is asked again. The bank lives in the
runtime root (``user/answer-bank.json``) and never holds inferred answers:
only explicit popup answers become ``confirmed`` entries.
"""

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .storage import atomic_write_json, lock, read_json

FILE = "user/answer-bank.json"
LOCK = "user/answer-bank.lock"
JOB_SCOPE = ("country", "level", "company", "start_date")
SENSITIVE = re.compile(
    r"authori[sz]|sponsor|visa|citizen|immigra|work permit|right to work|clearance|export|itar|consent|"
    # Work eligibility: any wording of being legally allowed to work, or a status that grants it.
    r"eligib|\blegal|lawful|permitted to work|allowed to work|entitled to work|permanent resident|green card|"
    r"criminal|convict|background check|gender|race|ethnic|hispanic|veteran|disab|pronoun|sexual|"
    # Pay: any wording of salary, wage, rate or money amount keeps the full scope.
    r"salar|compensat|remunerat|\bpay|wage|stipend|hourly|\brates?\b|per (?:hour|year|annum|month|week)|"
    r"annual|income|\bearn|bonus|equity|\$|€|£|\busd\b|\beur\b|\bgbp\b|"
    # Agreement: accepting, acknowledging or attesting to one employer's own text.
    r"agree|accept|acknowledg|\bterms\b|condition|polic|privacy|notice|statement|certif|attest|declar|confirm|"
    r"understand|\bread\b|\bsign|arbitrat|waive|releas|truthful|accura|non-?compete|non-?disclos|confidential|"
    # The employer or the role itself: the answer belongs to this application.
    r"\b(?:us|we|our|ours|here)\b|\b(?:this|the|your) (?:role|position|job|company|team|employer|firm|"
    r"organi[sz]ation|opportunity|opening|internship|program(?:me)?|posting|vacancy)\b|"
    r"refer|relative|related to|conflict|previously|former|\bbefore\b|\bever\b|interview|appl(?:y|ied|ication)|"
    # Contact and data permission: what one employer may do with the candidate.
    r"contact|recruit|touch|communit|network|\bpool\b|alert|newsletter|subscri|marketing|communicat|notif|"
    r"future|opportunit|\bmay\b|permi|allow|\bopt\b|share|shar\b|sharing|store|retain|\bkeep\b|on file|"
    r"process|\bdata\b|personal information|reach|sms|text message|\bcall\b",
    re.I,
)
# Topics whose answer is a fact about the candidate alone, whoever asks.
CANDIDATE = re.compile(
    r"\b(?:clubs?|societ(?:y|ies)|school|universit(?:y|ies)|college|degree|major|minor|gpa|grade|graduat\w*|"
    r"languages?|skills?|experience|projects?|courses?|coursework|tools?|frameworks?|technolog(?:y|ies)|"
    r"hobb(?:y|ies)|github|linkedin|portfolio|website|awards?|honou?rs?|publications?|hackathons?)\b",
    re.I,
)
# An option that only says yes or no, or accepts or declines something.
VERDICT = re.compile(
    r"(?:yes|no|y|n|true|false|(?:i )?(?:do not |don't |dont )?"
    r"(?:agree|disagree|accept|decline|consent|acknowledge|understand|certify|confirm))\b",
    re.I,
)
CHOICE_TYPES = {"select", "select-one", "radio", "combobox", "listbox"}
# A group of checkboxes takes every option that applies: its answer is a list of option labels.
MULTI_TYPES = {"checkbox-group"}


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
    if field.get("type") in MULTI_TYPES:
        return "multi"
    return "choice" if field.get("type") in CHOICE_TYPES or options(field) else "text"


def multi_value(field, value):
    """The listed option labels a multi answer picks, in the field's option order, or None."""
    labels = options(field)
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        return None
    picked = [normalize(item) for item in value]
    if len(set(picked)) != len(picked):
        return None
    chosen = [label for label in labels if normalize(label) in picked]
    return chosen if len(chosen) == len(picked) else None


def job_scope(job):
    """The job attributes a bank key may depend on."""
    return {name: (job or {}).get(name) for name in JOB_SCOPE}


def employer_bound(field):
    """Whether an answer to this question holds for one employer and start date only."""
    labels = options(field)
    return bool(
        kind(field) == "boolean"
        or (labels and all(VERDICT.match(normalize(label)) for label in labels))
        or SENSITIVE.search(" ".join([str(field.get("label", "")), *labels]))
        # Anything not known to concern the candidate alone stays with the employer.
        or not CANDIDATE.search(str(field.get("label", "")))
    )


def scope(field, job):
    job = job or {}
    result = {"country": normalize(job.get("country")), "job_type": normalize(job.get("level"))}
    if employer_bound(field):
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
        self.lock_path = Path(root) / LOCK

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
            if entry["key"] == wanted and entry.get("status") == "confirmed" and entry.get("value") not in (None, "", []):
                return copy.deepcopy({name: entry[name] for name in ("value", "status", "provenance", "confirmed_at")})
        return None

    def entries_for(self, job):
        """Confirmed answers whose scope matches this job, for the engine fill."""
        found = {}
        for entry in self._load()["answers"]:
            if entry.get("status") != "confirmed" or entry.get("value") in (None, "", []):
                continue
            types = {"boolean": "checkbox", "multi": "checkbox-group"}
            field = {"label": entry.get("question", ""), "options": entry.get("options") or [],
                     **({"type": types[entry["kind"]]} if entry.get("kind") in types else {})}
            if entry.get("scope") != scope(field, job):
                continue
            found[entry["key"]] = {name: copy.deepcopy(entry.get(name)) for name in ("question", "kind", "options", "value")}
        return list(found.values())

    def confirm(self, field, job, value, *, source_id):
        """Save an explicit user answer; a newer answer replaces the older one."""
        if value in (None, "") or (kind(field) == "boolean" and type(value) is not bool):
            raise ValueError("Only an actual answer can be saved")
        if kind(field) == "multi":
            value = multi_value(field, value)
            if value is None:
                raise ValueError("A multi-select answer needs one or more distinct listed options")
        # Hold the lock across read-modify-write so a concurrent confirm cannot drop this answer.
        with lock(self.lock_path):
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
