"""Resumable draft onboarding and native host question packets.

This module never invokes a model, browser or question tool. The top-level host
owns discovery, the actual tool call, its answer and explicit confirmation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

from . import guided_catalog, intake
from .storage import atomic_write_json, lock


PROGRESS = "user/setup-progress.json"
DEPENDENCIES = ("user/profile-intake.md", "profile.json", "config.json", "answers.json", "intake-import.json")
TOOLS = {"codex-async": "request_user_input_async", "codex": "request_user_input", "claude": "AskUserQuestion"}
STATUSES = {"answered", "unknown", "not_applicable", "declined", "notasked"}
META = ("schema_version", "session_id", "revision", "batch_id")


def _walk(items):
    for item in items:
        yield item
        yield from _walk(item.get("fields", []))


def _catalog():
    return {q["id"]: q for q in _walk(guided_catalog.questions())}


def _hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _integrity(state):
    data = {key: value for key, value in state.items() if key != "integrity"}
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _read(root):
    path = root / PROGRESS
    if not path.is_file():
        raise ValueError("Start setup --guided first")
    state = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=intake._unique_pairs)
    if not isinstance(state, dict) or state.get("integrity") != _integrity(state):
        raise ValueError("Setup progress was edited manually; restore the saved draft before resuming")
    return state


def _check(root, state):
    for relative, expected in state["references"].items():
        if _hash_file(root / relative) != expected:
            raise ValueError("Setup dependency changed. In the native popup choose Reload latest confirmed profile or Keep current draft; only explicit Reload permits setup --restart --ui UI. Inspect a new preview before confirming.")


def _save(root, state):
    state["integrity"] = _integrity(state)
    atomic_write_json(root / PROGRESS, state)


def _new_batch(state):
    state["batch_id"] = uuid4().hex


def _descriptor(question, path=None):
    question = copy.deepcopy(question)
    question["_path"] = path or ["document", "groups", *question["field"].split(".")]
    return question


def _seed(root):
    editable = intake._validate_document(intake.read_intake(root / "user/profile-intake.md"))
    imported = intake._load(root / "intake-import.json")
    document = copy.deepcopy(imported if imported is not None else editable)
    intake._validate_document(document)
    conflicts = []
    if imported is not None:
        # The editable export may predate the last confirmation. Start with the
        # confirmed intake, then preserve later normalized edits as well.
        expected = intake._profile_changes(imported)
        profile = intake._load(root / "profile.json", {})
        # Revision one is bootstrap/legacy input, not evidence that normalized
        # values supersede this explicit intake. Confirmed imports advance it.
        normalized = profile.get("revision", 1) > 1
        if not normalized:
            profile = {}
        groups = document["groups"]
        for group in ("contact", "targets", "logistics", "international_student", "compensation", "history"):
            for key in groups[group]:
                if key in profile.get(group, {}) and _different(profile[group][key], expected[group].get(key)):
                    groups[group][key] = copy.deepcopy(profile[group][key])
        for group in ("education", "authorization", "experience"):
            if profile.get(group) and _different(profile[group], expected[group]):
                groups[group]["records"] = copy.deepcopy(profile[group])
            details = group + "_details"
            for key, value in profile.get(details, {}).items():
                if key in groups[group] and _different(value, expected[details].get(key)):
                    groups[group][key] = copy.deepcopy(value)
        for key, value in profile.get("preferences", {}).items():
            if key in groups["filters"] and _different(value, expected["preferences"].get(key)):
                groups["filters"][key] = copy.deepcopy(value)
        if "skills" in profile and _different(profile["skills"], expected["skills"]):
            groups["experience"]["skills"] = copy.deepcopy(profile["skills"])
        config = intake._load(root / "config.json", {}) if normalized else {}
        for group in ("runtime", "resources", "resumes", "tailor"):
            for key, value in config.get(group, {}).items():
                if key in groups[group] and _different(value, imported["groups"][group].get(key)):
                    groups[group][key] = copy.deepcopy(value)
        if imported["groups"]["resources"].get("boards") is not None and "boards" in config:
            groups["resources"]["boards"] = copy.deepcopy(config["boards"])
        if profile.get("answers") and profile["answers"] != [
            {**answer, "valid": True} for answer in document.get("answers", [])
        ]:
            document["answers"] = copy.deepcopy(profile["answers"])
        blank = intake.blank_intake()
        for group, fields in editable["groups"].items():
            for key, value in fields.items():
                current = groups[group][key]
                # Defaults and unchanged stale exports cannot clear newer facts.
                if value == blank["groups"][group][key] or value == imported["groups"][group][key] or value == current:
                    continue
                if current == blank["groups"][group][key]:
                    groups[group][key] = copy.deepcopy(value)
                else:
                    conflicts.append(_seed_conflict(group + "." + key, current, value))
        if editable.get("answers") and editable["answers"] != imported.get("answers", []):
            if not document.get("answers"):
                document["answers"] = copy.deepcopy(editable["answers"])
            elif editable["answers"] != document["answers"]:
                conflicts.append(_seed_conflict("answers", document["answers"], editable["answers"]))
    top = guided_catalog.questions()
    state = {
        "schema_version": 1, "session_id": uuid4().hex, "revision": 0,
        "document": document,
        "references": {name: _hash_file(root / name) for name in DEPENDENCIES},
        "field_status": {q["id"]: "notasked" for q in _walk(top)},
        "queue": conflicts + [_descriptor(q) for q in top[:3]], "cursor": 3,
        "issued": {}, "pages": {}, "editors": {}, "preview": None,
    }
    _new_batch(state)
    return state


def _different(left, right):
    return intake._without_confirmation_time(left) != intake._without_confirmation_time(right)


def _seed_conflict(field, current, draft):
    name = field.rsplit(".", 1)[-1].replace("_", " ")
    path = ["document", "answers"] if field == "answers" else ["document", "groups", *field.split(".")]
    question = {"id": "setup.seed." + field, "field": field, "kind": "choice", "value_type": "string",
                "seed_conflict": True, "_path": path, "_draft": copy.deepcopy(draft),
                "prompt": {"en": f"For {name}, keep the confirmed value {json.dumps(current, ensure_ascii=False)} or use the editable draft {json.dumps(draft, ensure_ascii=False)}?",
                           "vi": f"Về {name}, giữ giá trị đã xác nhận {json.dumps(current, ensure_ascii=False)} hay dùng bản nháp {json.dumps(draft, ensure_ascii=False)}?"},
                "allow_custom": False, "choices": []}
    question["choices"] = [_choice(question, "keep_confirmed", "Keep confirmed value", "Giữ giá trị đã xác nhận"),
                           _choice(question, "use_draft", "Use editable draft", "Dùng giá trị bản nháp")]
    return question


def _get(data, path):
    for part in path:
        data = data[part]
    return data


def _put(data, path, value):
    for part in path[:-1]:
        if isinstance(data, dict):
            if part not in data or data[part] is None:
                data[part] = {}
            data = data[part]
        else:
            data = data[part]
    data[path[-1]] = value


def _advance(state):
    if not state["queue"]:
        top = guided_catalog.questions()
        cursor = state["cursor"]
        state["queue"] = [_descriptor(q) for q in top[cursor:cursor + 3]]
        state["cursor"] = min(cursor + 3, len(top))


def _active(state):
    questions = []
    for question in state["queue"][:3]:
        if question.get("_terminal") and questions:
            break
        questions.append(question)
        if question.get("_terminal"):
            break
    return questions


def _choice(question, value, en, vi, *, control=False):
    key = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]
    return {"id": question["id"] + ":" + ("control:" if control else "option:") + key,
            "value": value, "label": {"en": en, "vi": vi}, "control": control}


def _controls(question):
    controls = []
    if question.get("allow_unknown"):
        controls.append(_choice(question, "unknown", "I don't know yet", "Tôi chưa biết", control=True))
    if question.get("allow_skip"):
        controls.append(_choice(question, "notasked", "Skip for now", "Bỏ qua lúc này", control=True))
    if question.get("allow_decline"):
        controls.append(_choice(question, "declined", "Prefer not to answer", "Không muốn trả lời", control=True))
    return controls


def _visible(state, question, ui):
    result = {k: copy.deepcopy(v) for k, v in question.items() if not k.startswith("_")}
    editor = question["kind"] in ("record", "list", "object")
    if editor:
        pool = [_choice(question, "add", "Add an entry", "Thêm một mục", control=True),
                _choice(question, "done", "Done", "Hoàn tất", control=True)] + _controls(question)
        if question.get("_existing_editor"):
            pool = [_choice(question, "done", "Done", "Hoàn tất", control=True),
                    _choice(question, "cancel", "Cancel and keep original record", "Hủy và giữ bản ghi gốc", control=True)]
        elif question["field"] in ("education.records", "experience.records", "authorization.records") and not question.get("_terminal"):
            try:
                records = _get(state, question["_path"])
            except (KeyError, TypeError):
                records = []
            if records:
                pool.insert(0, _choice(question, "edit_existing", "Edit an existing record", "Sửa bản ghi đã có", control=True))
    else:
        pool = copy.deepcopy(question.get("choices", [])) + _controls(question)
        if question["kind"] == "multi_choice":
            pool.append(_choice(question, "clear", "Clear selected values", "Xóa các mục đã chọn", control=True))
    repeated = question["kind"] == "multi_choice" and ui in ("codex", "claude")
    limit = 3 if ui == "codex" else 4 if ui == "claude" else None
    page = state["pages"].get(question["id"], 0)
    if limit and (len(pool) > limit or repeated):
        slots = limit - (2 if repeated else 1)
        start = (page * slots) % len(pool)
        choices = [pool[(start + index) % len(pool)] for index in range(slots)]
        choices.append(_choice(question, "more", "More choices", "Lựa chọn tiếp", control=True))
        if repeated:
            choices.append(_choice(question, "done", "Done", "Hoàn tất", control=True))
    else:
        choices = pool
    if ui == "codex-async" and question["kind"] == "multi_choice":
        choices.append(_choice(question, "done", "Done", "Hoàn tất", control=True))
    result["choices"] = choices
    return result


def _native(questions, ui, language):
    native, bindings = [], []
    for question in questions:
        choices = question.get("choices", [])
        prompt = question["prompt"].get(language, question["prompt"]["en"])
        if question["kind"] in ("text", "path", "number", "date"):
            prompt += (" Nhập giá trị trong ô văn bản; các nút bên dưới là tùy chọn bỏ qua."
                       if language == "vi" else " Enter the value in the text box; buttons below are optional skip controls.")
        labels = [c["label"].get(language, c["label"]["en"]) for c in choices]
        if ui == "codex-async":
            native.append({"title": prompt, "options": labels})
        else:
            options = [{"label": label, "description": label} for label in labels]
            item = {"header": "Setup", "question": prompt, "options": options}
            if ui == "codex":
                item["id"] = "q" + hashlib.sha256(question["id"].encode()).hexdigest()[:10]
            else:
                item["multiSelect"] = question["kind"] == "multi_choice"
            native.append(item)
        bindings.append({"question_id": question["id"], "option_ids": [c["id"] for c in choices]})
    return {"tool": TOOLS[ui], "arguments": {"questions": native}, "bindings": bindings}


def _packet(state, ui):
    metadata = {key: state[key] for key in META}
    if ui == "none":
        return {**metadata, "status": "ui_required", "questions": [], "ui": {},
                "next": "Open a host with an available native question tool in a supported mode, then resume setup --guided --ui UI."}
    questions = [_visible(state, q, ui) for q in _active(state)]
    state["displayed"] = questions
    state["displayed_ui"] = ui
    for question in _active(state):
        state["issued"][question["id"]] = copy.deepcopy(question)
    language = state["document"]["groups"]["runtime"].get("language")
    language = language if language in ("en", "vi") else "en"
    return {**metadata, "status": "questions" if questions else "draft_complete", "questions": questions,
            "ui": _native(questions, ui, language),
            "next": "Call the discovered native tool and wait for the user's actual answer; setup --finish previews a partial draft."}


def _edit(state, question_id):
    catalog = _catalog()
    if question_id in state["issued"]:
        question = copy.deepcopy(state["issued"][question_id])
    elif question_id in catalog:
        question = _descriptor(catalog[question_id])
        # Repeated field templates have no selected record until Add or an
        # existing issued record question is chosen.
        for ancestor in catalog.values():
            if ancestor.get("repeatable") and question["field"].startswith(ancestor["field"] + "."):
                raise ValueError("Choose an existing record question or add a record first")
    else:
        raise ValueError("Choose a catalog question or a previously issued record question")
    question.pop("_terminal", None)
    question.pop("_existing_editor", None)
    state["queue"] = [question]
    state["pages"][question["id"]] = 0
    state["preview"] = None
    state["revision"] += 1
    _new_batch(state)


def _leaf_fields(question):
    for child in question.get("fields", []):
        if child["kind"] == "object" and child.get("fields") and child["field"].rsplit(".", 1)[-1] != "eeo":
            yield from _leaf_fields(child)
        else:
            yield child


def _replace_question(state, question_id, replacements):
    index = next(i for i, pending in enumerate(state["queue"]) if pending["id"] == question_id)
    state["queue"][index:index + 1] = replacements


def _record_label(record, index):
    def text(key):
        value = record.get(key)
        if isinstance(value, dict) and "status" in value and "value" in value:
            value = value["value"]
        return str(value) if value is not None else ""

    context = " / ".join(value for value in (text("school") or text("employer") or text("country"), text("degree") or text("title")) if value)
    return f"{index + 1}. {context or 'Record'}"


def _pick_existing(state, question):
    records = _get(state, question["_path"])
    if not isinstance(records, list) or not records or not all(isinstance(record, dict) for record in records):
        raise ValueError("No existing records are available to edit")
    picker = {"id": question["id"] + "@picker:" + uuid4().hex, "field": question["field"],
              "kind": "choice", "value_type": "string", "record_picker": True, "allow_custom": False,
              "prompt": {"en": "Choose an existing record to edit, or keep the current records.",
                         "vi": "Chọn bản ghi đã có để sửa, hoặc giữ các bản ghi hiện tại."},
              "_origin": copy.deepcopy(question), "_records": {}, "choices": []}
    for index, record in enumerate(records):
        key = uuid4().hex
        picker["_records"][key] = {"index": index, "snapshot": copy.deepcopy(record)}
        label = _record_label(record, index)
        picker["choices"].append(_choice(picker, key, label, label))
    picker["choices"].append(_choice(picker, "cancel", "Keep original records", "Giữ các bản ghi gốc", control=True))
    _replace_question(state, question["id"], [picker])


def _enter_editor(state, question, *, existing_index=None, replace_id=None):
    field = question["field"]
    path = question["_path"]
    token = uuid4().hex
    record_id = field.split(".")[0] + ":" + token
    children = list(_leaf_fields(question))
    if not children:
        raise ValueError("This editor has no structured entry fields")
    is_array = question["value_type"] == "array"
    mapping = field in ("resumes.role_mapping", "compensation.eeo")
    if mapping:
        state["editors"][token] = {"path": path, "value": {}, "mapping": True}
        base = ["editors", token, "value"]
    elif is_array:
        try:
            records = _get(state, path)
        except (KeyError, TypeError):
            records = None
        if records is None:
            records = []
            _put(state, path, records)
        if not isinstance(records, list):
            raise ValueError("Expected a repeatable list")
        scalar = len(children) == 1 and children[0]["field"] == field + ".value"
        if existing_index is None:
            item = None if scalar else {"id": record_id} if field in (
                "education.records", "authorization.records", "experience.records") else {}
            records.append(item)
            base = path + [len(records) - 1]
            state["editors"][token] = {"path": base, "scalar": scalar}
        else:
            item = records[existing_index]
            record_id = item.get("id") or record_id
            base = path + [existing_index]
            state["editors"][token] = {"path": base, "original": copy.deepcopy(item)}
    else:
        base = path
        state["editors"][token] = {"path": path}
    issued = []
    for child in children:
        suffix = child["field"][len(field) + 1:]
        target = base if is_array and state["editors"][token].get("scalar") else base + suffix.split(".")
        item = _descriptor(child, target)
        if existing_index is not None:
            # A nested graduation editor addresses the envelope's payload only
            # after an explicit change discards that one fact's confirmation.
            for length in range(len(base) + 1, len(target)):
                ancestor_path = target[:length]
                try:
                    ancestor = _get(state, ancestor_path)
                except (KeyError, TypeError):
                    break
                if isinstance(ancestor, dict) and "value" in ancestor and "status" in ancestor:
                    item["_fact_path"] = ancestor_path
                    break
        item["id"] = question["id"] + "@" + token + "." + suffix
        item["record_id"] = record_id
        issued.append(item)
    terminal = copy.deepcopy(question)
    terminal["_terminal"] = token
    if existing_index is not None:
        terminal["_existing_editor"] = True
    _replace_question(state, replace_id or question["id"], issued + [terminal])


def _close_editor(state, question):
    token = question.get("_terminal")
    editor = state["editors"].get(token)
    if editor and editor.get("mapping"):
        supplied = editor["value"]
        key_name, value_name = ("role", "path") if question["field"] == "resumes.role_mapping" else ("category", "value")
        if supplied.get(key_name) and supplied.get(value_name) is not None:
            current = copy.deepcopy(_get(state, editor["path"]) or {})
            current[supplied[key_name]] = supplied[value_name]
            _put(state, editor["path"], current)
    # Preserve editor targets for stable record-question edits after restart.


def _typed_value(root, question, value):
    expected = question["value_type"]
    if value is None or value == "" or (isinstance(value, str) and not value.strip()):
        raise ValueError("Blank is not an answer; choose unknown or skip explicitly")
    valid = {
        "string": lambda v: isinstance(v, str),
        "boolean": lambda v: type(v) is bool,
        "integer": lambda v: type(v) is int,
        "number": lambda v: type(v) in (int, float) and math.isfinite(v),
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
    }
    if not valid[expected](value):
        raise ValueError("Answer has the wrong type")
    if expected == "array" and question["kind"] == "multi_choice" and not all(isinstance(v, str) for v in value):
        raise ValueError("Choose typed list options")
    if question["kind"] == "date":
        intake.validate_profile({"schema_version": 1, "revision": 1, "start": value})
    if question["kind"] == "path":
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if not path.is_file():
            raise ValueError("The selected file does not exist; keep it unknown until available")
        if question["field"] in ("resumes.main_pdf", "resumes.newest") and not path.read_bytes().startswith(b"%PDF-"):
            raise ValueError("Choose an existing PDF resume")
        value = str(path)
    return value


def _decode(root, state, question, entry):
    if not isinstance(entry, dict) or set(entry) - {"question_id", "status", "value", "option_ids"}:
        raise ValueError("Invalid answer fields")
    status = entry.get("status")
    if status not in STATUSES:
        raise ValueError("Invalid draft answer status")
    if status != "answered":
        if "value" in entry or "option_ids" in entry:
            raise ValueError("A skipped or unknown answer cannot carry a value")
        if status == "declined" and not question.get("allow_decline"):
            raise ValueError("Decline is available only for optional sensitive questions")
        return status, None, None
    if ("value" in entry) == ("option_ids" in entry):
        raise ValueError("Supply either typed text or stable option IDs")
    if "option_ids" in entry:
        ids = entry["option_ids"]
        if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids) or len(ids) != len(set(ids)):
            raise ValueError("Invalid or duplicate option IDs")
        choices = {c["id"]: c for c in question.get("choices", [])}
        if not set(ids).issubset(choices):
            raise ValueError("Choose an option actually issued in this batch")
        selected = [choices[option] for option in ids]
        controls = [c for c in selected if c.get("control")]
        if controls:
            if len(selected) != 1:
                raise ValueError("Navigation and factual choices must be answered separately")
            action = controls[0]["value"]
            if action in STATUSES:
                return action, None, None
            return status, None, action
        if question["kind"] != "multi_choice" and len(selected) != 1:
            raise ValueError("Choose exactly one option")
        value = [c["value"] for c in selected] if question["kind"] == "multi_choice" else selected[0]["value"]
    else:
        if question["kind"] in ("record", "list", "object"):
            raise ValueError("Use the structured editor, not raw JSON")
        value = entry["value"]
        choices = [c["value"] for c in question.get("choices", []) if not c.get("control")]
        if choices and not question.get("allow_custom"):
            values = value if question["kind"] == "multi_choice" and isinstance(value, list) else [value]
            if not all(any(type(v) is type(c) and v == c for c in choices) for v in values):
                raise ValueError("A factual enum cannot be inferred from free text")
        if question["kind"] == "multi_choice" and isinstance(value, str) and question.get("allow_custom"):
            value = [value]
    return status, _typed_value(root, question, value), None


def _apply_entry(state, question, status, value, action):
    question_id = question["id"]
    if question.get("seed_conflict"):
        if value == "use_draft":
            _put(state, question["_path"], copy.deepcopy(question["_draft"]))
        state["field_status"][question_id] = status
        state["queue"] = [q for q in state["queue"] if q["id"] != question_id]
        return
    if action == "more":
        state["pages"][question_id] = state["pages"].get(question_id, 0) + 1
        return
    if question.get("record_picker"):
        origin = question["_origin"]
        if action == "cancel" or status != "answered":
            _replace_question(state, question_id, [origin])
            return
        binding = question["_records"].get(value)
        records = _get(state, origin["_path"])
        if binding is None or binding["index"] >= len(records) or records[binding["index"]] != binding["snapshot"]:
            raise ValueError("The selected record changed; choose it again from the editor")
        _enter_editor(state, origin, existing_index=binding["index"], replace_id=question_id)
        return
    if action == "edit_existing":
        _pick_existing(state, question)
        return
    if action == "cancel" and question.get("_existing_editor"):
        editor = state["editors"][question["_terminal"]]
        _put(state, editor["path"], copy.deepcopy(editor["original"]))
        origin = {key: value for key, value in question.items() if key not in ("_terminal", "_existing_editor")}
        _replace_question(state, question_id, [origin])
        return
    if action == "add":
        _close_editor(state, question)
        _enter_editor(state, question)
        return
    if action == "clear":
        _put(state, question["_path"], [])
        state["field_status"][question_id] = "answered"
        return
    if action == "done":
        _close_editor(state, question)
        state["field_status"][question_id] = "answered"
    else:
        state["field_status"][question_id] = status
        if status != "notasked" and question.get("_fact_path"):
            ancestor = _get(state, question["_fact_path"])
            if isinstance(ancestor, dict) and "status" in ancestor and "value" in ancestor:
                _put(state, question["_fact_path"], copy.deepcopy(ancestor["value"]))
        if status == "answered":
            repeated = question["kind"] == "multi_choice"
            if repeated:
                try:
                    previous = _get(state, question["_path"])
                except (KeyError, TypeError):
                    previous = []
                # Repeated native single choices accumulate until Done; a
                # complete typed selection remains compatible with the CLI.
                if state.get("displayed_ui") == "codex":
                    merged = list(previous or [])
                    for item in value:
                        if item not in merged:
                            merged.append(item)
                    _put(state, question["_path"], merged)
                    state["pages"][question_id] = state["pages"].get(question_id, 0) + 1
                    return
            _put(state, question["_path"], value)
        elif status != "notasked":
            empty = [] if question["value_type"] == "array" else {} if question["value_type"] == "object" else None
            _put(state, question["_path"], empty)
    state["queue"] = [q for q in state["queue"] if q["id"] != question_id]


def _answer(root, state, path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=intake._unique_pairs)
    if not isinstance(payload, dict) or set(payload) != {*META, "answers"}:
        raise ValueError("Invalid response packet; timeout, interruption and preselection are not answers")
    for key in META:
        if type(payload.get(key)) is not type(state[key]) or payload[key] != state[key]:
            raise ValueError("Stale session, revision or batch; resume the current native question")
    entries = payload["answers"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("Wait for an actual user answer")
    displayed = {q["id"]: q for q in state.get("displayed", [])}
    originals = {q["id"]: q for q in _active(state)}
    seen, decoded = set(), []
    for entry in entries:
        question_id = entry.get("question_id") if isinstance(entry, dict) else None
        if not isinstance(question_id, str) or question_id in seen or question_id not in displayed or question_id not in originals:
            raise ValueError("Duplicate, unknown or unissued question ID")
        seen.add(question_id)
        status, value, action = _decode(root, state, displayed[question_id], entry)
        decoded.append((originals[question_id], status, value, action))
    # Decode the entire response before changing even the draft in memory.
    for question, status, value, action in decoded:
        _apply_entry(state, question, status, value, action)
    state["preview"] = None
    state["revision"] += 1
    _advance(state)
    _new_batch(state)


def _finish(root, state, ui):
    document = copy.deepcopy(state["document"])
    # Unknown optional keys and keys for another ATS are draft answers, never
    # board schema fields. Required keys still go through strict normalization.
    boards = document["groups"]["resources"].get("boards")
    if isinstance(boards, list):
        for index, board in enumerate(boards):
            ats = board.get("ats")
            applicable = {"ats", "company", "endpoint", *( ("host", "tenant", "site", "search") if ats == "workday" else ("board",))}
            boards[index] = {key: value for key, value in board.items() if key in applicable and value is not None}
    intake._validate_document(document)
    # Record calendar/precision validation is deferred until all subfields are
    # available, without changing intake's factual normalization rules.
    for record in document["groups"]["education"]["records"]:
        graduation = record.get("graduation")
        if graduation and graduation.get("value") is not None:
            intake.validate_profile({"schema_version": 1, "revision": 1, "education": [
                {"graduation": graduation if "status" in graduation else intake.fact(graduation)}]})
    path = root / "user/setup-intake.md"
    # Validate the complete draft before publishing its fixed-path export.
    from .storage import _atomic_bytes

    content = "# Guided intake draft\n\n```json\n" + json.dumps(document, ensure_ascii=False, indent=2) + "\n```\n"
    _atomic_bytes(path, content.encode("utf-8"))
    preview = intake.preview_import(root, path)
    state["preview"] = preview
    state["revision"] += 1
    _new_batch(state)
    confirm_question = {"id": "setup.confirm", "field": "setup.confirm", "kind": "choice",
                        "prompt": {"en": "Confirm the saved preview or keep this draft?", "vi": "Xác nhận bản xem trước đã lưu hay giữ bản nháp?"},
                        "choices": [
                            {"id": "setup-confirm", "value": "confirm", "label": {"en": "Confirm", "vi": "Xác nhận"}},
                            {"id": "setup-keep", "value": "keep", "label": {"en": "Keep draft", "vi": "Giữ bản nháp"}},
                        ]}
    result = {**{key: state[key] for key in META}, "status": "preview" if ui != "none" else "ui_required",
              "intake": str(path), "preview": preview, "questions": [confirm_question] if ui != "none" else [],
              "ui": _native([confirm_question], ui, "en") if ui != "none" else {},
              "next": "Show this exact saved preview. Wait for explicit native Confirm before setup --confirm; Keep draft makes no normalized changes."}
    return result


def _internal_path(root, relative):
    path = root / relative
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or (getattr(part, "is_junction", lambda: False)()):
            raise ValueError("Setup recovery refuses a symlink or junction in its internal paths")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Setup recovery path escapes the runtime root")
    return path


def _restart(root, ui):
    # The top-level host calls this only after the user's real Reload choice.
    # A raw CLI without an available native UI must keep every draft byte.
    if ui == "none":
        return {"status": "ui_required", "questions": [], "ui": {},
                "next": "Use a permitted native popup: Reload latest confirmed profile / Keep current draft. Only explicit Reload permits setup --restart --ui UI."}
    for relative in (*DEPENDENCIES, PROGRESS, "user/setup-intake.md", "user/setup-backups", "user/setup-progress.lock", "profile-update.lock"):
        _internal_path(root, relative)
    with lock(root / "user/setup-progress.lock"), lock(root / "profile-update.lock"):
        state = _seed(root)
        result = _packet(state, ui)
        snapshots = {}
        for key, relative in (("progress", PROGRESS), ("intake", "user/setup-intake.md")):
            path = _internal_path(root, relative)
            if path.exists():
                snapshots[key] = (path.name, path.read_bytes())
        backups = {}
        if snapshots:
            directory = _internal_path(root, "user/setup-backups/" + uuid4().hex)
            directory.mkdir(parents=True, exist_ok=False)
            for key, (name, content) in snapshots.items():
                target = directory / name
                with target.open("xb") as stream:
                    stream.write(content)
                backups[key] = str(target)
        from .storage import _atomic_bytes

        state["integrity"] = _integrity(state)
        _atomic_bytes(root / PROGRESS, (json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"), backup=False)
        return {**result, "recovery": {"root": str(root), "backups": backups}}


def run(root, *, answer=None, finish=False, confirm=False, restart=False, edit=None, ui="none"):
    root = Path(root).resolve()
    if ui not in {*TOOLS, "none"}:
        raise ValueError("Choose a supported native UI schema or none")
    if restart:
        return _restart(root, ui)
    with lock(root / "user/setup-progress.lock"):
        # apply_import owns the profile OS lock. Its trusted precondition checks
        # all extra dependencies inside that lock, without nesting the OS lock.
        if confirm:
            state = _read(root)
            _check(root, state)
            if state.get("preview") is None:
                raise ValueError("Finish and inspect a saved preview before confirming")
            expected = state["integrity"]

            def precondition():
                current = _read(root)
                if current["integrity"] != expected:
                    raise ValueError("Setup progress changed before confirmation")
                _check(root, current)

            result = intake.apply_import(root, state["preview"], confirm=True, precondition=precondition)
            if result.get("applied") is True:
                # Only our successful import may advance the dependency CAS.
                state["references"] = {name: _hash_file(root / name) for name in DEPENDENCIES}
                state["preview"] = None
                state["revision"] += 1
                _new_batch(state)
                _save(root, state)
            return result
        with lock(root / "profile-update.lock"):
            exists = (root / PROGRESS).exists()
            state = _read(root) if exists else _seed(root)
            _check(root, state)
            before = copy.deepcopy(state)
            if edit is not None:
                _edit(state, edit)
            if answer is not None:
                _answer(root, state, answer)
            if finish:
                result = _finish(root, state, ui)
            else:
                result = _packet(state, ui)
            if not exists or state != before:
                _save(root, state)
            return result
