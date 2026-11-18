"""Required questions with no confirmed answer: one batch per run, one popup.

``apply`` holds each tab whose required questions no confirmed fact or bank
answer covers and records those fields on the job. ``Questions.collect`` turns
every held job's open fields into one batch (the same question asked by several
jobs is one question), ``packet`` renders it for the host's native question
tool, and ``answer`` reads the user's actual answers, saves them to the answer
bank and returns the value each held tab gets. This module never opens a
browser, calls a model or clicks anything; timeout and skip are not answers.
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
# Questions per tool call and options per question each native tool accepts.
LIMITS = {"claude": (4, 4), "codex": (3, 3), "codex-async": (None, None)}
TEXT_LIMIT = 1000


def pending_fields(fill_result):
    """Required fields of a fill result the user can answer in the popup."""
    pending = []
    for item in (fill_result or {}).get("unknown") or []:
        if not item.get("required") or not item.get("id") or item.get("type") == "file":
            continue
        if item.get("reason") == "agent_interaction_required":
            continue  # a confirmed value exists; only the widget needs a hand
        pending.append({key: copy.deepcopy(item.get(key)) for key in ("id", "label", "type", "required", "options", "reason")})
    return pending


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
    if question["kind"] == "text":
        picks.append({"id": f"{qid}:skip-tab", "label": "I will fill it in the tab", "value": None})
    return picks


def _native(question, choices, ui):
    per_question = LIMITS[ui][1]
    count = len(question["targets"])
    prompt = question["label"] + f" (asked by {count} held application{'s' if count != 1 else ''})"
    shown = choices
    if per_question is not None and len(choices) > per_question:
        prompt += " Options: " + " | ".join(question["options"]) + ". Type one exactly in the text box."
        shown = [choice for choice in choices if choice["value"] is None]
    elif question["kind"] == "text":
        prompt += " Type the answer in the text box."
    labels = [choice["label"] for choice in shown]
    if ui == "codex-async":
        return {"title": prompt, "options": labels}, shown
    item = {"header": "Application", "question": prompt,
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

    def collect(self, records):
        """One batch from every held job's open required fields."""
        groups = {}
        for record in records:
            if record.get("state") != "needs_manual" or not record.get("tab_id"):
                continue
            for field in record.get("unanswered") or []:
                if self.bank.lookup(field, record) is not None:
                    continue
                key = answer_bank.key(field, record)
                question = groups.setdefault(key, {
                    "id": "q" + key[:12],
                    "key": key,
                    "label": str(field.get("label") or field.get("id")).strip(),
                    "kind": answer_bank.kind(field),
                    "options": answer_bank.options(field),
                    "country": record.get("country"),
                    "targets": [],
                })
                target = {"job_id": record["id"], "field_id": field["id"], "type": field.get("type"),
                          "label": field.get("label"), "options": copy.deepcopy(field.get("options") or [])}
                if all((t["job_id"], t["field_id"]) != (target["job_id"], target["field_id"]) for t in question["targets"]):
                    question["targets"].append(target)
        data = self._save(list(groups.values()))
        return self._summary(data)

    def _summary(self, data):
        pending = len(data["questions"])
        return {"pending": pending, "batch_id": data["batch_id"],
                **({"next": "Run questions --ui UI, show its native popup once and pass the user's answers to questions --answer PATH"}
                   if pending else {})}

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
        per_call = LIMITS[ui][0] or len(data["questions"])
        questions, natives, bindings = [], [], []
        for question in data["questions"]:
            choices = _choices(question)
            native, shown = _native(question, choices, ui)
            questions.append({"id": question["id"], "label": question["label"], "kind": question["kind"],
                              "options": question["options"], "choices": choices,
                              "jobs": [target["job_id"] for target in question["targets"]]})
            natives.append(native)
            bindings.append({"question_id": question["id"], "option_ids": [choice["id"] for choice in shown]})
        calls = [{"questions": natives[start:start + per_call]} for start in range(0, len(natives), per_call)]
        return {"status": "questions", "batch_id": data["batch_id"], "questions": questions,
                "ui": {"tool": TOOLS[ui], "calls": calls, "bindings": bindings},
                "next": ("Make these native calls in order as one popup, wait for the user's actual answers, then write "
                         '{"batch_id": ..., "answers": [{"question_id": ..., "option_id" or "text": ...}]} to a private file '
                         "and run questions --answer PATH. Timeout or preselection is not an answer.")}

    def _decode(self, question, entry):
        choices = _choices(question)
        if set(entry) == {"question_id", "option_id"}:
            choice = next((item for item in choices if item["id"] == entry["option_id"]), None)
            if choice is None:
                raise ValueError("Unknown option ID")
            return choice["value"]
        if set(entry) != {"question_id", "text"} or not isinstance(entry["text"], str):
            raise ValueError("Each answer needs exactly one option_id or text")
        text = " ".join(entry["text"].split())
        if not text or len(text) > TEXT_LIMIT:
            raise ValueError("A typed answer must be non-empty and at most 1000 characters")
        if question["kind"] == "text":
            return text
        match = next((item for item in choices if item["value"] is not None
                      and answer_bank.normalize(item["label"]) == answer_bank.normalize(text)), None)
        if match is None:
            raise ValueError("The typed answer is not one of the listed options")
        return match["value"]

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
            first = question["targets"][0]
            self.bank.confirm(first, {"country": question["country"]}, value, source_id="unanswered:" + data["batch_id"])
            answered.add(question["id"])
            for target in question["targets"]:
                refill.setdefault(target["job_id"], {})[target["field_id"]] = self._tab_value(question, target, value)
        remaining = [question for question in data["questions"] if question["id"] not in answered]
        if answered:
            data = self._save(remaining, batch_id=uuid4().hex)
        return {"saved": len(answered), "pending": len(remaining), "batch_id": data["batch_id"], "refill": refill}

    @staticmethod
    def _tab_value(question, target, value):
        if question["kind"] != "choice":
            return value
        for option in target["options"]:
            label = option.get("label", option.get("value")) if isinstance(option, dict) else option
            if answer_bank.normalize(label) == answer_bank.normalize(value):
                return option.get("value", label) if isinstance(option, dict) else option
        return value
