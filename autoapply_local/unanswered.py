"""Required questions with no confirmed answer: one batch per run, one popup.

``apply`` holds each tab whose required questions no confirmed fact or bank
answer covers and records those fields on the job. ``Questions.collect`` turns
every held job's open fields into one batch (the same question asked by several
jobs is one question), ``packet`` renders it for the host's native question
tool, and ``answer`` reads the user's actual answers and saves them to the
answer bank. ``refill_plan`` gives each held tab the banked values for its open
fields, so a refill that failed is retried from the bank. This module never
opens a browser, calls a model or clicks anything; timeout and skip are not
answers.
"""

import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from . import answer_bank
from .storage import atomic_write_json, read_json

FILE = "user/unanswered-questions.json"
TOOLS = {"codex-async": "request_user_input_async", "codex": "request_user_input", "claude": "AskUserQuestion"}
# Questions per popup (one native call) and buttons per question each native tool accepts.
LIMITS = {"claude": (4, 4), "codex": (3, 3), "codex-async": (None, None)}
TEXT_LIMIT = 1000
LABEL_LIMIT = 60  # longest option label shown as a native button
HEADER = "Application"  # native headers take at most 12 characters


def pending_fields(fill_result):
    """Required fields of a fill result the user can answer in the popup."""
    pending = []
    for item in (fill_result or {}).get("unknown") or []:
        if not isinstance(item, dict) or not item.get("required") or not item.get("id") or item.get("type") == "file":
            continue
        if item.get("reason") == "agent_interaction_required":
            continue  # a confirmed value exists; only the widget needs a hand
        pending.append({key: copy.deepcopy(item.get(key)) for key in ("id", "label", "type", "required", "options", "reason")})
    return pending


def _option_value(option):
    return option.get("value") if isinstance(option, dict) else option


def _option_label(option):
    return option.get("label", option.get("value")) if isinstance(option, dict) else option


def tab_value(kind, field_options, value):
    """The value one tab's own control takes for a bank or popup answer, or None."""
    if kind != "choice":
        return value
    matches = [option for option in field_options or []
               if _option_value(option) not in ("", None)
               and answer_bank.normalize(_option_label(option)) == answer_bank.normalize(value)]
    return _option_value(matches[0]) if len(matches) == 1 else None


def check_answers(fields, answers):
    """Refuse any answer that is not valid for its open field, before a tab is touched."""
    if not isinstance(answers, dict) or not answers:
        raise ValueError("No answers to fill")
    open_fields = {field.get("id"): field for field in fields}
    for field_id, value in answers.items():
        field = open_fields.get(field_id)
        if field is None:
            raise ValueError(f"{field_id} is not an open question of this held tab")
        kind = answer_bank.kind(field)
        if kind == "boolean":
            if type(value) is not bool:
                raise ValueError(f"{field_id} needs Yes or No")
        elif kind == "choice":
            values = [_option_value(option) for option in field.get("options") or []]
            if value in ("", None) or values.count(value) != 1:
                raise ValueError(f"{field_id} needs one of its own listed options")
        elif not isinstance(value, str) or not value.strip() or len(value) > TEXT_LIMIT:
            raise ValueError(f"{field_id} needs a non-empty answer of at most 1000 characters")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _choices(question):
    qid = question["id"]
    if question["kind"] == "choice":
        picks = [{"id": f"{qid}:option:{index}", "label": label, "value": label}
                 for index, label in enumerate(question["options"])]
    elif question["kind"] == "boolean":
        picks = [{"id": f"{qid}:option:yes", "label": "Yes", "value": True},
                 {"id": f"{qid}:option:no", "label": "No", "value": False}]
    else:
        picks = []
    picks.append({"id": f"{qid}:skip", "label": "Skip for now", "value": None})
    picks.append({"id": f"{qid}:skip-tab", "label": "I will fill it in the tab", "value": None})
    return picks


def _buttons(question, choices, per_question):
    """The choices shown as native buttons; the rest are typed in the text box."""
    answers = [choice for choice in choices if choice["value"] is not None]
    skip = choices[-2:-1]
    # The skip button counts too: an option named like it would be two buttons with one label.
    labels = [answer_bank.normalize(choice["label"]) for choice in answers + skip]
    fits = (
        answers
        and len(set(labels)) == len(labels)
        and all(0 < len(choice["label"]) <= LABEL_LIMIT for choice in answers)
        and (per_question is None or len(answers) + 1 <= per_question)
    )
    return (answers + skip, False) if fits else (choices[-2:], bool(answers))


def _native(question, choices, ui):
    count = len(question["targets"])
    prompt = question["label"] + f" (asked by {count} held application{'s' if count != 1 else ''})"
    shown, typed = _buttons(question, choices, LIMITS[ui][1])
    if typed:
        prompt += " Options: " + " | ".join(question["options"]) + ". Type one exactly in the text box."
    elif question["kind"] != "boolean" and not any(choice["value"] is not None for choice in shown):
        prompt += " Type the answer in the text box."
    labels = [choice["label"] for choice in shown]
    if ui == "codex-async":
        return {"title": prompt, "options": labels}, shown
    item = {"header": HEADER, "question": prompt,
            "options": [{"label": label, "description": label} for label in labels]}
    if ui == "codex":
        item["id"] = question["id"]
    else:
        item["multiSelect"] = False
    return item, shown


class Questions:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / FILE
        self.bank = answer_bank.AnswerBank(self.root)

    def _load(self):
        if not self.path.exists():
            return {"schema_version": 1, "batch_id": None, "digest": None, "questions": []}
        data = read_json(self.path)
        if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
            raise ValueError("The unanswered question batch is not valid; run apply again to rebuild it")
        return data

    def _save(self, questions, batch_id=None):
        previous = self._load()
        digest = _digest(questions)
        if batch_id is None:
            batch_id = previous["batch_id"] if previous.get("digest") == digest and previous.get("batch_id") else uuid4().hex
        data = {"schema_version": 1, "batch_id": batch_id, "digest": digest, "questions": questions}
        atomic_write_json(self.path, data)
        return data

    @staticmethod
    def _held(records):
        return [record for record in records
                if record.get("state") == "needs_manual" and record.get("tab_id") and record.get("unanswered")]

    def refill_plan(self, records):
        """Banked values for every held tab's open fields (including failed refills)."""
        plan = {}
        for record in self._held(records):
            for field in record["unanswered"]:
                found = self.bank.lookup(field, record)
                if found is None:
                    continue
                value = tab_value(answer_bank.kind(field), field.get("options"), found["value"])
                if value is not None:
                    plan.setdefault(record["id"], {})[field["id"]] = value
        return plan

    def collect(self, records):
        """One batch from every held job's open required fields."""
        groups = {}
        for record in self._held(records):
            for field in record["unanswered"]:
                if self.bank.lookup(field, record) is not None:
                    continue
                key = answer_bank.key(field, record)
                question = groups.setdefault(key, {
                    "id": "q" + key[:12],
                    "key": key,
                    "label": str(field.get("label") or field.get("id")).strip(),
                    "kind": answer_bank.kind(field),
                    "options": answer_bank.options(field),
                    "job": answer_bank.job_scope(record),
                    "targets": [],
                })
                target = {"job_id": record["id"], "field_id": field["id"], "type": field.get("type"),
                          "label": field.get("label"), "options": copy.deepcopy(field.get("options") or [])}
                if all((t["job_id"], t["field_id"]) != (target["job_id"], target["field_id"]) for t in question["targets"]):
                    question["targets"].append(target)
        data = self._save(list(groups.values()))
        ready = len(self.refill_plan(records))
        summary = {"pending": len(data["questions"]), "batch_id": data["batch_id"], "refill_ready": ready}
        if data["questions"]:
            summary["next"] = "Run questions --ui UI, show its native popup once and pass the user's answers to questions --answer PATH"
        elif ready:
            summary["next"] = "Run questions --refill to fill banked answers into the held tabs"
        return summary

    def packet(self, ui):
        data = self._load()
        if ui not in (*TOOLS, "none"):
            raise ValueError("Choose a native question UI")
        if not data["questions"]:
            return {"status": "none", "batch_id": data["batch_id"], "questions": [], "ui": {}}
        if ui == "none":
            return {"status": "ui_required", "batch_id": data["batch_id"], "pending": len(data["questions"]),
                    "questions": [], "ui": {},
                    "next": "Open a host with a native question tool, then run questions --ui UI; the batch is kept."}
        # One popup is one native call; what does not fit waits for the next popup,
        # questions asked by the most held applications first.
        ordered = sorted(data["questions"], key=lambda question: -len(question["targets"]))
        per_call = LIMITS[ui][0] or len(ordered)
        shown_now, deferred = ordered[:per_call], len(ordered) - per_call
        questions, natives, bindings = [], [], []
        for question in shown_now:
            choices = _choices(question)
            native, shown = _native(question, choices, ui)
            questions.append({"id": question["id"], "label": question["label"], "kind": question["kind"],
                              "options": question["options"], "choices": shown,
                              "jobs": [target["job_id"] for target in question["targets"]]})
            natives.append(native)
            bindings.append({"question_id": question["id"], "option_ids": [choice["id"] for choice in shown]})
        later = (f" {deferred} more question(s) do not fit this popup; they stay pending for the next one."
                 if deferred > 0 else "")
        return {"status": "questions", "batch_id": data["batch_id"], "questions": questions,
                "deferred": max(deferred, 0),
                "ui": {"tool": TOOLS[ui], "calls": [{"questions": natives}], "bindings": bindings},
                "next": ("Make this one native call as the popup, wait for the user's actual answers, then write "
                         '{"batch_id": ..., "answers": [{"question_id": ..., "option_id" or "text": ...}]} to a private file '
                         "and run questions --answer PATH. Timeout or preselection is not an answer." + later)}

    @staticmethod
    def _decode(question, entry):
        choices = _choices(question)
        if set(entry) == {"question_id", "option_id"}:
            matches = [item for item in choices if item["id"] == entry["option_id"]]
            if not matches:
                raise ValueError("Unknown option ID")
            choice = matches[0]
        else:
            if set(entry) != {"question_id", "text"} or not isinstance(entry["text"], str):
                raise ValueError("Each answer needs exactly one option_id or text")
            text = " ".join(entry["text"].split())
            if not text or len(text) > TEXT_LIMIT:
                raise ValueError("A typed answer must be non-empty and at most 1000 characters")
            if question["kind"] == "text":
                return text
            found = [item for item in choices if item["value"] is not None
                     and answer_bank.normalize(item["label"]) == answer_bank.normalize(text)]
            if not found:
                raise ValueError("The typed answer is not one of the listed options")
            choice = found[0]
        if choice["value"] is not None and question["kind"] == "choice":
            same = [label for label in question["options"]
                    if answer_bank.normalize(label) == answer_bank.normalize(choice["label"])]
            if len(same) != 1:
                raise ValueError("That option label is ambiguous; fill this question in the held tab")
        return choice["value"]

    def answer(self, path):
        """Save the user's actual answers; return the value for each held tab."""
        data = self._load()
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"batch_id", "answers"}:
            raise ValueError("Invalid response file; timeout, interruption and preselection are not answers")
        if not data["batch_id"] or payload["batch_id"] != data["batch_id"]:
            raise ValueError("Stale question batch; run questions --ui UI again")
        entries = payload["answers"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("Wait for the user's actual answers")
        questions = {question["id"]: question for question in data["questions"]}
        decoded, seen = [], set()
        for entry in entries:
            question_id = entry.get("question_id") if isinstance(entry, dict) else None
            if question_id not in questions or question_id in seen:
                raise ValueError("Duplicate or unknown question ID")
            seen.add(question_id)
            decoded.append((questions[question_id], self._decode(questions[question_id], entry)))
        # Every answer is checked before the bank or any tab changes.
        refill, answered = {}, set()
        for question, value in decoded:
            if value is None:
                continue
            self.bank.confirm(question["targets"][0], question["job"], value, source_id="unanswered:" + data["batch_id"])
            answered.add(question["id"])
            for target in question["targets"]:
                mapped = tab_value(question["kind"], target["options"], value)
                if mapped is not None:
                    refill.setdefault(target["job_id"], {})[target["field_id"]] = mapped
        remaining = [question for question in data["questions"] if question["id"] not in answered]
        if answered:
            data = self._save(remaining, batch_id=uuid4().hex)
        return {"saved": len(answered), "pending": len(remaining), "batch_id": data["batch_id"], "refill": refill}
