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


def _text(value):
    if isinstance(value, list):
        return "; ".join(_text(item) for item in value)
    if isinstance(value, dict):
        if "name" in value and len(value) == 1:
            return str(value["name"])
        parts = [str(value.get(k)) for k in ("school", "employer", "degree", "title", "major", "location") if value.get(k)]
        dates = " to ".join(str(value[k]) for k in ("start", "end") if value.get(k))
        return ", ".join(parts + ([dates] if dates else []))
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
    lines = {lang: "; ".join(f"{_label(f, lang)}: {_text(v)}" for f, v in shown.items()) for lang in ("en", "vi")}
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


def steps(root, document, extractor=None):
    """Return the quick-step questions placed before the optional advanced intake."""
    groups = document["groups"]
    try:
        config = read_json(root / "config.json")
    except (OSError, ValueError):
        config = {}
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
    """Recompute the filter review from the current draft answers, just before it is shown."""
    if question.get("quick_step") != "filters":
        return
    derived = job_filters.derive(state["document"]["groups"])
    question["_draft"] = job_filters.intake_values(derived)
    question["prompt"] = {lang: job_filters.summary(derived, lang) + (
        " Xác nhận hay sửa?" if lang == "vi" else " Confirm these filters, or edit them?") for lang in ("en", "vi")}


def _put(state, field, value):
    group, name = field.split(".")
    state["document"]["groups"][group][name] = copy.deepcopy(value)


def apply(state, question, status, value):
    """Confirm writes the draft; Edit writes it and asks each field; skip writes nothing."""
    question_id = question["id"]
    state["field_status"][question_id] = status
    index = next(i for i, pending in enumerate(state["queue"]) if pending["id"] == question_id)
    replacements = []
    if status == "answered" and value in ("confirm", "edit"):
        for field, draft in question["_draft"].items():
            _put(state, field, draft)
        if value == "edit":
            for field, draft in question["_draft"].items():
                word = ("Suggested", "Gợi ý") if question["quick_step"] == "filters" else ("Resume shows", "Resume ghi")
                text = _text(draft) if not isinstance(draft, list) or draft else "none"
                note = {"en": f"{word[0]}: {text}; skip keeps it.", "vi": f"{word[1]}: {text}; bỏ qua để giữ nguyên."}
                replacements.append(_catalog(field, note))
    state["queue"][index:index + 1] = replacements
