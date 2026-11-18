"""Group resume text into draft facts: contact, education, experience, skills.

Everything here is an unconfirmed suggestion for the setup Confirm/Edit step;
nothing is written and no fact is established until the user confirms it.
"""

import re
import zipfile
from xml.etree import ElementTree

from .extraction import extract_source


MONTHS = {name: index for index, name in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
POINT = rf"(?:{MONTH}\s+)?\d{{4}}"
RANGE = re.compile(rf"({POINT})\s*(?:–|—|-|to)\s*({POINT}|present|current|now)\b", re.I)
EXPECTED = re.compile(rf"expected(?:\s+graduation)?:?\s+({POINT})", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<!\d)(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
PROFILE_URL = re.compile(r"(?:https?://)?(?:www\.)?((?:linkedin\.com/in|github\.com)/[\w.-]+)", re.I)
URL = re.compile(r"(?:https?://|www\.)[^\s|,;]+", re.I)
NAME = re.compile(r"[A-Z][A-Za-z'.-]+(?: [A-Z][A-Za-z'.-]+){1,3}")
SCHOOL = re.compile(r"\b(university|college|institute|school|academy)\b", re.I)
DEGREE = re.compile(r"^(bachelor|master|associate|doctor|ph\.?d|b\.?s\.?|b\.?a\.?|m\.?s\.?|b\.?eng|m\.?eng|mba)\b", re.I)
GPA = re.compile(r"\bGPA:?\s*(\d(?:\.\d+)?)\s*(?:/\s*(\d(?:\.\d+)?))?", re.I)
BULLET = re.compile(r"^\s*[•▪●◦*\-–]\s*")
HEADINGS = {
    "education": "education",
    "experience": "experience", "work experience": "experience", "professional experience": "experience",
    "employment": "experience", "work history": "experience",
    "skills": "skills", "technical skills": "skills", "skills and tools": "skills",
}
OTHER_HEADINGS = {"projects", "personal projects", "leadership", "activities", "awards", "honors",
                  "certifications", "publications", "volunteering", "summary", "objective", "interests"}
# Research and project sections hold dated entries that name a partner school; they are never education.
OTHER_WORDS = re.compile(r"\b(research|projects?)\b", re.I)
HEADING_WORDS = {"research", "project", "projects", "and", "&", "experience", "selected", "academic",
                 "undergraduate", "personal", "relevant", "technical", "independent", "publications"}
PARENTHETICAL = re.compile(r"\([^)]*\)")


def empty():
    return {"contact": {}, "education": {"records": []}, "experience": [], "skills": []}


def _date(text):
    text = text.strip().casefold()
    if text in ("present", "current", "now"):
        return "present"
    match = re.match(rf"(?:({MONTH})\s+)?(\d{{4}})", text, re.I)
    if not match:
        return None
    if match.group(1):
        return f"{match.group(2)}-{MONTHS[match.group(1)[:3]]:02d}"
    return match.group(2)


def _columns(line):
    return [part.strip() for part in re.split(r"\s{2,}|\s+\|\s+|\t", line.strip()) if part.strip()]


def _heading(line):
    """Return the section a heading line opens, or None when the line is content."""
    text = line.strip().rstrip(":").strip()
    key = text.casefold()
    if key in HEADINGS:
        return HEADINGS[key]
    if key in OTHER_HEADINGS:
        return "other"
    words = key.replace(",", " ").split()
    if OTHER_WORDS.search(text) and len(words) <= 4 and all(word in HEADING_WORDS for word in words):
        return "other"
    return None


def _sections(lines):
    sections, current = {"header": []}, "header"
    for line in lines:
        heading = _heading(line)
        if heading:
            current = heading
        else:
            sections.setdefault(current, []).append(line)
            continue
        sections.setdefault(current, [])
    return sections


def _contact(lines, text):
    contact = {}
    first = next((line.strip() for line in lines if line.strip()), "")
    if NAME.fullmatch(first):
        contact["legal_name"] = first
    header = "\n".join(lines)
    if match := EMAIL.search(header or text):
        contact["application_email"] = match.group(0)
    if match := PHONE.search(header):
        contact["phone"] = match.group(0)
    for match in PROFILE_URL.finditer(header):
        key = "linkedin" if match.group(1).casefold().startswith("linkedin") else "github"
        contact.setdefault(key, "https://" + match.group(1))
    for match in URL.finditer(header):
        if not PROFILE_URL.match(match.group(0)):
            url = match.group(0)
            contact.setdefault("portfolio", url if url.casefold().startswith("http") else "https://" + url)
    return contact


def _range(record, line):
    match = RANGE.search(line)
    if match:
        start, end = _date(match.group(1)), _date(match.group(2))
        if start:
            record["start"] = start
        if end:
            record["end"] = end
    return match


def _education(lines):
    group, record = {"records": []}, None
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if SCHOOL.search(PARENTHETICAL.sub("", text)) and not DEGREE.match(text):
            record = {"school": _columns(RANGE.sub("", text))[0]}
            _range(record, text)
            group["records"].append(record)
        elif record is not None and DEGREE.match(text) and "degree" not in record:
            degree = _columns(EXPECTED.sub("", text))[0]
            name, _, major = degree.partition(" in ")
            if not major and " " in degree and not degree.casefold().startswith(("bachelor", "master", "associate", "doctor")):
                name, major = degree.split(" ", 1)
            record["degree"] = name.strip().rstrip(",")
            if major.strip():
                record["major"] = major.strip()
        if record is not None and (match := EXPECTED.search(text)):
            value = _date(match.group(1))
            record["graduation"] = {"value": value, "precision": "month" if "-" in value else "year", "kind": "expected"}
        if match := GPA.search(text):
            group["gpa"] = float(match.group(1))
            if match.group(2):
                group["gpa_scale"] = float(match.group(2))
    return group


def _experience(lines):
    records, record, bullets = [], None, []

    def close():
        if record is not None and bullets:
            record["description"] = "; ".join(bullets)

    for line in lines:
        text = line.strip()
        if not text:
            continue
        match = RANGE.search(text)
        if match and not BULLET.match(line):
            close()
            record, bullets = {}, []
            head = _columns(text[:match.start()] + text[match.end():])
            if head:
                record["employer"] = head[0]
            _range(record, text)
            records.append(record)
        elif record is not None and BULLET.match(line):
            bullets.append(BULLET.sub("", line).strip())
        elif record is not None and "title" not in record and not bullets:
            columns = _columns(text)
            record["title"] = columns[0]
            if len(columns) > 1:
                record["location"] = columns[1]
    close()
    return [{key: r[key] for key in ("employer", "title", "location", "start", "end", "description") if key in r}
            for r in records]


def _skills(lines):
    names = []
    for line in lines:
        text = line.split(":", 1)[1] if ":" in line else line
        names.extend(part.strip() for part in re.split(r"[,;|•]", text))
    return _unique_skills({"name": name} for name in names if name)


def _unique_skills(skills):
    seen, result = set(), []
    for skill in skills:
        if skill["name"].casefold() not in seen:
            seen.add(skill["name"].casefold())
            result.append(skill)
    return result


def parse(text):
    """Return grouped draft facts found in one resume's text."""
    lines = (text or "").splitlines()
    if not any(line.strip() for line in lines):
        return empty()
    sections = _sections(lines)
    return {
        "contact": _contact(sections.get("header", []), text),
        "education": _education(sections.get("education", [])),
        "experience": _experience(sections.get("experience", [])),
        "skills": _skills(sections.get("skills", [])),
    }


def _key(record, names):
    return tuple(str(record.get(name, "")).casefold() for name in names)


def merge(parsed):
    """Combine several resumes: first value wins per contact field; records are unioned."""
    result = empty()
    for facts in parsed:
        for key, value in facts["contact"].items():
            result["contact"].setdefault(key, value)
        for key, value in facts["education"].items():
            if key != "records":
                result["education"].setdefault(key, value)
        known = {_key(r, ("school", "degree")) for r in result["education"]["records"]}
        result["education"]["records"].extend(
            r for r in facts["education"]["records"] if _key(r, ("school", "degree")) not in known)
        known = {_key(r, ("employer", "title")) for r in result["experience"]}
        result["experience"].extend(r for r in facts["experience"] if _key(r, ("employer", "title")) not in known)
        result["skills"] = _unique_skills([*result["skills"], *facts["skills"]])
    return result


def resume_paths(config):
    resumes = (config or {}).get("resumes") or {}
    paths = [resumes.get("main_pdf")]
    paths.extend(item.get("path") if isinstance(item, dict) else item for item in resumes.get("alternatives") or [])
    return [path for path in paths if isinstance(path, str) and path]


def from_config(config, extractor=None):
    """Read every resume terminal setup saved; unreadable files are skipped, never fatal."""
    parsed = []
    for path in resume_paths(config):
        try:
            parsed.append(parse(extract_source(path, extractor)["text"]))
        except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile, ElementTree.ParseError):
            continue
    return merge(parsed)
