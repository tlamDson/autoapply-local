"""Quick engine setup (plan 3.3 steps 1-3) inside the guided native onboarding.

After terminal setup, guided setup first shows each group of facts found in the
user's resumes for Confirm or Edit, then asks what a resume cannot hold, then
shows the job filters derived from those answers for Confirm or Edit. Extracted
values stay draft suggestions: the saved preview and explicit Confirm still
decide what becomes a fact.
"""

import copy
import hashlib
import json

from . import guided_catalog, job_filters, resume_facts
from .storage import read_json


RESUME_GROUPS = ("contact", "education", "experience", "skills")
RESUME_LABELS = {
    "contact": ("contact information", "thông tin liên hệ"),
    "education": ("education", "học tập"),
    "experience": ("experience", "kinh nghiệm"),
    "skills": ("skills", "kỹ năng"),
}
MISSING = (
    "authorization.authorized_now", "authorization.sponsorship_now", "authorization.sponsorship_future",
    "contact.street", "contact.city", "contact.state", "contact.postcode", "contact.country",
    "compensation.eeo", "logistics.season", "logistics.year", "targets.job_types", "targets.roles",
)
FILTERS_ID = "setup.quick.filters"


def enabled(root):
    """Quick steps follow a terminal setup, which saves jobs per run or resumes."""
    try:
        config = read_json(root / "config.json")
    except (OSError, ValueError):
        return False
    runtime, resumes = config.get("runtime") or {}, config.get("resumes") or {}
    return "jobs_per_run" in runtime or bool(resumes.get("main_pdf"))


def _blank(value):
    return job_filters.raw(value) in (None, "", [], {})


def _choice(question_id, value, en, vi):
    key = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]
    return {"id": question_id + ":option:" + key, "value": value, "label": {"en": en, "vi": vi}, "control": False}


def _review(question_id, step, prompt, draft):
    question = {"id": question_id, "field": question_id, "kind": "choice", "value_type": "string",
                "quick_step": step, "prompt": prompt, "allow_skip": True, "allow_unknown": False,
                "allow_custom": False, "_draft": draft}
    question["choices"] = [_choice(question_id, "confirm", "Confirm", "Xác nhận"),
                           _choice(question_id, "edit", "Edit", "Sửa")]
    return question


def _text(value, language="en"):
    """Render a draft value completely: Confirm writes exactly what this shows."""
    if isinstance(value, list):
        return " | ".join(_text(item, language) for item in value)
    if isinstance(value, dict):
        if set(value) == {"name"}:
            return str(value["name"])
        if set(value) >= {"value", "kind"} and set(value) <= {"value", "precision", "kind"}:
            return f"{value['value']} ({value['kind']})"
        parts = []
        for key, item in value.items():
            if key == "id" or item in (None, "", [], {}):
                continue
            label = guided_catalog.LABELS.get(key, (key, key))[1 if language == "vi" else 0]
            parts.append(f"{label}: {_text(item, language)}")
        return ", ".join(parts)
    return str(value)


def _label(field, language):
    name = field.rsplit(".", 1)[-1]
    return guided_catalog.LABELS[name][1 if language == "vi" else 0]


def _resume_draft(groups, facts):
    """Map each resume group to {intake field: value} for values not already in the draft."""
    drafts = {"contact": {}, "education": {}, "experience": {}, "skills": {}}
    for key, value in facts["contact"].items():
        if _blank(groups["contact"].get(key)):
            drafts["contact"]["contact." + key] = value
    for group, items, names in (("education", facts["education"]["records"], ("school", "degree")),
                                ("experience", facts["experience"], ("employer", "title"))):
        current = list(groups[group].get("records") or [])
        known = {tuple(str(job_filters.raw(r.get(n)) or "").casefold() for n in names) for r in current if isinstance(r, dict)}
        new = [{"id": f"{group}:resume-{len(current) + i + 1}", **r} for i, r in enumerate(
            r for r in items if tuple(str(r.get(n, "")).casefold() for n in names) not in known)]
        if new:
            drafts[group][group + ".records"] = current + new
        if group == "education":
            for key in ("gpa", "gpa_scale"):
                if key in facts["education"] and _blank(groups["education"].get(key)):
                    drafts["education"]["education." + key] = facts["education"][key]
    skills = list(groups["experience"].get("skills") or [])
    known = {str(job_filters.raw(s.get("name")) or "").casefold() for s in skills if isinstance(s, dict)}
    new = [s for s in facts["skills"] if s["name"].casefold() not in known]
    if new:
        drafts["skills"]["experience.skills"] = skills + new
    return drafts


def _resume_question(group, draft, groups):
    shown = {field: value for field, value in draft.items()}
    for field in ("education.records", "experience.records", "experience.skills"):
        if field in shown:
            before = len(groups[field.split(".")[0]].get(field.split(".")[1]) or [])
            shown[field] = shown[field][before:]
    lines = {lang: "; ".join(f"{_label(f, lang)}: {_text(v, lang)}" for f, v in shown.items()) for lang in ("en", "vi")}
    en, vi = RESUME_LABELS[group]
    prompt = {"en": f"Your resume shows this {en}: {lines['en']}. Confirm it, or edit it?",
              "vi": f"Resume của bạn có {vi} sau: {lines['vi']}. Xác nhận hay sửa?"}
    return _review("setup.quick.resume." + group, "resume", prompt, draft)


def _catalog(field, note=None):
    question = copy.deepcopy(guided_catalog.index()[field])
    question["_path"] = ["document", "groups", *field.split(".")]
    if note:
        question["prompt"] = {lang: question["prompt"][lang] + " " + note[lang] for lang in ("en", "vi")}
    return question


def _config(root):
    try:
        config = read_json(root / "config.json")
    except (OSError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def prefill(root, document):
    """Seed blank runtime and resume fields from terminal setup, so the draft shows
    them and an explicit clear in setup is a real change rather than a blank."""
    config, groups = _config(root), document["groups"]
    runtime, resumes = config.get("runtime") or {}, config.get("resumes") or {}
    for key in ("language", "apply_engine"):
        if runtime.get(key) and (_blank(groups["runtime"].get(key)) or (key == "language" and groups["runtime"][key] == "en")):
            groups["runtime"][key] = runtime[key]
    if resumes.get("main_pdf") and _blank(groups["resumes"].get("main_pdf")):
        groups["resumes"]["main_pdf"] = resumes["main_pdf"]
    paths = [item.get("path") if isinstance(item, dict) else item for item in resumes.get("alternatives") or []]
    paths = [path for path in paths if isinstance(path, str) and path]
    if paths and _blank(groups["resumes"].get("alternatives")):
        groups["resumes"]["alternatives"] = paths


def steps(root, document, extractor=None):
    """Return the quick-step questions placed before the optional advanced intake."""
    groups = document["groups"]
    config = _config(root)
    facts = resume_facts.from_config(config, extractor=extractor)
    drafts = _resume_draft(groups, facts)
    questions = [_resume_question(group, drafts[group], groups) for group in RESUME_GROUPS if drafts[group]]
    drafted = {field for draft in drafts.values() for field in draft}
    questions.extend(_catalog(field) for field in MISSING if field not in drafted and _blank(_value(groups, field)))
    filters = _review(FILTERS_ID, "filters", {"en": "", "vi": ""}, {})
    filters["_alone"] = True
    questions.append(filters)
    return questions


def _value(groups, field):
    group, name = field.split(".")
    return groups[group].get(name)


def walk_skips(questions):
    """Catalog IDs the quick steps cover, so the advanced walk does not ask them again."""
    skips = set()
    for question in questions:
        if question.get("quick_step"):
            skips.update(question["_draft"])
        else:
            skips.add(question["id"])
    skips.update(job_filters.intake_values(job_filters.derive({})))
    return sorted(skips)


def refresh(state, question):
    """Recompute the filter review from the current draft answers, just before it is shown.

    A filter the user edited (its saved value differs from what the answers last
    derived) is shown and written as edited; only unedited filters follow the answers.
    """
    if question.get("quick_step") != "filters":
        return
    groups = state["document"]["groups"]
    derived = job_filters.derive(groups)
    draft = job_filters.intake_values(derived)
    question["_derived"] = {field: copy.deepcopy(draft[field]) for field in SYNCED}
    record = (state.get("quick_filters") or {}).get("derived")
    reference = record or state.get("quick_baseline") or {}
    for field in SYNCED:
        current = job_filters.raw(_value(groups, field))
        # Before this session's first review an empty filter was never derived
        # (legacy or advanced-only intake), so it is not an edit; after it, a
        # cleared filter is one.
        if field in reference and isinstance(current, list) and current != reference[field] and (record or current):
            draft[field] = copy.deepcopy(current)
            derived[field.split(".")[1]] = list(current)
    question["_draft"] = draft
    question["prompt"] = {lang: job_filters.summary(derived, lang) + (
        " Xác nhận hay sửa?" if lang == "vi" else " Confirm these filters, or edit them?") for lang in ("en", "vi")}


def _put(state, field, value):
    group, name = field.split(".")
    state["document"]["groups"][group][name] = copy.deepcopy(value)


# Filter fields derived only from other answers; the posting window and excluded
# companies also read the filter fields themselves, so they are never re-synced.
SYNCED = ("filters.jd_exclude", "filters.title_include", "filters.title_exclude")


def baseline(groups):
    """What the answers derive for the synced filters; a saved filter that differs was edited."""
    values = job_filters.intake_values(job_filters.derive(groups))
    return {field: values[field] for field in SYNCED}


def sync_filters(state):
    """After answers change, replace derived filters the user has not edited and
    show the filter review again, so an answer such as Unknown sponsorship never
    leaves an exclusion that no longer follows from the answers."""
    record = state.get("quick_filters")
    if not record:
        return
    groups = state["document"]["groups"]
    derived = baseline(groups)
    if derived == record["derived"]:
        return
    for field, old in record["derived"].items():
        group, name = field.split(".")
        if groups[group].get(name) == old:
            _put(state, field, derived[field])
    record["derived"] = derived
    if not any(pending.get("quick_step") == "filters" for pending in state["queue"]):
        review = copy.deepcopy(state["issued"].get(FILTERS_ID) or _review(FILTERS_ID, "filters", {"en": "", "vi": ""}, {}))
        review["_alone"] = True
        state["queue"].insert(0, review)


def apply(state, question, status, value):
    """Confirm writes the draft; Edit writes it and asks each field; skip writes nothing."""
    question_id = question["id"]
    state["field_status"][question_id] = status
    index = next(i for i, pending in enumerate(state["queue"]) if pending["id"] == question_id)
    replacements = []
    if status == "answered" and value in ("confirm", "edit"):
        for field, draft in question["_draft"].items():
            _put(state, field, draft)
        if question["quick_step"] == "filters":
            # Record what the answers derived, not the draft: an edited filter stays an edit.
            state["quick_filters"] = {"derived": copy.deepcopy(question.get("_derived") or {
                field: question["_draft"][field] for field in SYNCED})}
        if value == "edit":
            for field, draft in question["_draft"].items():
                word = ("Suggested", "Gợi ý") if question["quick_step"] == "filters" else ("Resume shows", "Resume ghi")
                text = {lang: _text(draft, lang) if not isinstance(draft, list) or draft else "none"
                        for lang in ("en", "vi")}
                note = {"en": f"{word[0]}: {text['en']}; skip keeps it.",
                        "vi": f"{word[1]}: {text['vi']}; bỏ qua để giữ nguyên."}
                replacements.append(_catalog(field, note))
    state["queue"][index:index + 1] = replacements
