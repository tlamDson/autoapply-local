"""One resume per job: role tag match, keyword score tie-break, one remembered question.

Configuration (``config["resumes"]``): ``main_pdf`` with optional ``main_tags``,
``alternatives`` (``{"id", "path", "tags"}`` or a plain path) and the older
``role_mapping`` (token -> alternative id or path), whose tokens count as tags.
Order is main first, then alternatives, then mapped-only paths.

Rules: a resume whose tag is a whole-word match in the job title is a match;
common role abbreviations (``_ALIASES``) also match their long form and back,
and an abbreviation reached through an alias must be upper case in the title;
several matches -> highest JD keyword coverage (no model), equal or unknown ->
configuration order. No match and several resumes -> ask once per role and
remember the answer in a small JSON file; one resume alone needs no question;
with no way to ask (``fallback``, no terminal) the main resume is used and the
reason says so, nothing remembered.
Nothing here opens a form or touches a facts file.
"""

import json
import re
from collections import namedtuple
from pathlib import Path

from . import jd_keywords
from .storage import atomic_write_json, read_json

Choice = namedtuple("Choice", "path reason tag score")
Candidate = namedtuple("Candidate", "id path tags")

_NOISE = re.compile(r"\b(?:20\d\d|spring|summer|fall|autumn|winter)\b")


class ResumeChoiceNeeded(ValueError):
    """No tag matches the role and the user has not said which resume to use."""

    def __init__(self, role, options):
        self.role, self.options = role, options
        names = ", ".join(item["id"] for item in options)
        super().__init__(
            f"No resume tag matches '{role}': choose one of ({names}), add a tag to a resume, "
            "or run apply in a terminal to be asked once"
        )


def _words(text):
    return re.sub(r"[^a-z0-9+#]+", " ", str(text or "").casefold()).strip()


def role_key(role):
    """Role title reduced to what identifies the role: no case, punctuation, season or year."""
    return " ".join(_NOISE.sub(" ", _words(role)).split())


def _resolve(path):
    return Path(path).expanduser().resolve()


def candidates(config):
    """Configured resumes in order, each with the tags that select it."""
    settings = (config or {}).get("resumes") or {}
    items = []

    def add(item_id, path, tags):
        items.append([item_id, _resolve(path), [str(tag) for tag in tags if str(tag).strip()]])

    if settings.get("main_pdf"):
        add("main", settings["main_pdf"], settings.get("main_tags") or [])
    for index, entry in enumerate(settings.get("alternatives") or []):
        if isinstance(entry, dict):
            path = entry.get("path", entry.get("pdf"))
            if path:
                add(str(entry.get("id") or f"alt{index + 1}"), path, entry.get("tags") or [])
        elif entry:
            add(f"alt{index + 1}", entry, [])
    for token, target in (settings.get("role_mapping") or {}).items():
        by_id = next((item for item in items if item[0] == str(target)), None)
        if by_id is None:
            known = _resolve(target)
            by_id = next((item for item in items if item[1] == known), None)
        if by_id is None:
            add(f"map{len(items) + 1}", target, [])
            by_id = items[-1]
        by_id[2].append(str(token))
    return [Candidate(item_id, path, tuple(tags)) for item_id, path, tags in items]


# Abbreviation -> long forms that mean the same role (already reduced by ``_words``).
_ALIASES = {
    "swe": ("software engineer", "software engineering"),
    "sde": ("software development engineer",),
    "ml": ("machine learning",),
    "ai": ("artificial intelligence",),
    "fe": ("frontend", "front end"),
    "be": ("backend", "back end"),
}


def _forms(words):
    """(phrase, upper_only) pairs a tag matches: itself, then its aliases."""
    yield words, False
    for short, longs in _ALIASES.items():
        if words == short:
            yield from ((phrase, False) for phrase in longs)
        elif words in longs:
            yield short, True


def _matching_tag(tags, role):
    padded = f" {_words(role)} "
    upper = {token.casefold() for token in re.findall(r"[A-Za-z0-9+#]+", str(role or "")) if token.isupper()}
    for tag in tags:
        words = _words(tag)
        if not words:
            continue
        for phrase, upper_only in _forms(words):
            if f" {phrase} " in padded and (not upper_only or phrase in upper):
                return tag
    return None


def resume_text(path, extractor=None):
    """Text of a resume file, or '' when it cannot be read (score zero, never a failure)."""
    from . import extraction

    try:
        result = extraction.extract_source(path, extractor=extractor)
    except (OSError, ValueError):
        return ""
    return str(result.get("text", "")) if isinstance(result, dict) else str(result or "")


class Memory:
    """Answers to the no-match question, keyed by role, in one JSON file."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        try:
            data = read_json(self.path)
        except (OSError, ValueError):
            return {}
        choices = data.get("choices") if isinstance(data, dict) else None
        return choices if isinstance(choices, dict) else {}

    def get(self, role):
        value = self._load().get(role_key(role))
        return _resolve(value) if isinstance(value, str) and value else None

    def set(self, role, path):
        choices = self._load()
        choices[role_key(role)] = str(_resolve(path))
        atomic_write_json(self.path, {"version": 1, "choices": choices})


def _score(job, matches, text_of):
    description = job.get("description") or ""
    scores = []
    for item in matches:
        try:
            text = text_of(item.path)
        except (OSError, ValueError):
            text = ""
        scores.append(jd_keywords.coverage(description, text))
    return scores


def _answer(answer, options):
    for item in options:
        if answer in (item["id"], str(item["path"])):
            return item
    try:
        resolved = _resolve(answer)
    except (OSError, ValueError, TypeError):
        resolved = None
    for item in options:
        if resolved == item["path"]:
            return item
    raise ValueError("The answer must be one of the configured resumes")


def choose(config, job, *, memory=None, ask=None, text_of=None, fallback=False):
    """Pick the resume for one job. ``ask(role, options)`` returns an id or path, or None.

    ``fallback``: with no ``ask`` (no terminal), use the first configured resume (the main
    one) instead of raising; a declined question still raises."""
    found = candidates(config)
    if not found:
        raise ValueError("Select a main PDF or a role-mapped alternative in the intake")
    role = str(job.get("role", ""))
    matches = [item for item in found if _matching_tag(item.tags, role)]
    if len(matches) == 1:
        return Choice(matches[0].path, "role_tag", _matching_tag(matches[0].tags, role), None)
    if matches:
        scores = _score(job, matches, text_of or resume_text)
        if all(score is None for score in scores):
            return Choice(matches[0].path, "role_tag", _matching_tag(matches[0].tags, role), None)
        best = max(range(len(matches)), key=lambda i: (scores[i] or 0.0, -i))
        return Choice(matches[best].path, "keyword_score", _matching_tag(matches[best].tags, role), scores[best])
    if len(found) == 1:
        return Choice(found[0].path, "only_resume", None, None)
    remembered = memory.get(role) if memory else None
    if remembered and any(item.path == remembered for item in found):
        return Choice(remembered, "remembered", None, None)
    if fallback and ask is None:
        return Choice(found[0].path, "no_terminal", None, None)
    options = [{"id": item.id, "path": item.path, "tags": list(item.tags)} for item in found]
    answer = ask(role, options) if ask else None
    if not answer:
        raise ResumeChoiceNeeded(role, options)
    picked = _answer(answer, options)
    if memory:
        memory.set(role, picked["path"])
    return Choice(picked["path"], "asked", None, None)
