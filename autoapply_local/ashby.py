"""Ashby postings: the public GraphQL questions of one job and a fill plan.

The module builds the public posting request and reads its recorded response;
it never sends a request or an application itself. A fill plan keeps only
answers confirmed in the user's profile and reports every other required
question, so nothing is guessed.
"""

import html
import json
import re
from urllib.parse import urlsplit

from .profile import resolve_field

HOST = "jobs.ashbyhq.com"
ENDPOINT = f"https://{HOST}/api/non-user-graphql?op=ApiJobPosting"
QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName:String!,$jobPostingId:String!){jobPosting("
    "organizationHostedJobsPageName:$organizationHostedJobsPageName,jobPostingId:$jobPostingId){id title "
    "descriptionHtml applicationForm{sections{fieldEntries{... on FormFieldEntry{isRequired descriptionHtml field}}}} "
    "surveyForms{sections{fieldEntries{... on FormFieldEntry{isRequired descriptionHtml field}}}}}}"
)
KIND = {
    "String": "text",
    "Email": "text",
    "Phone": "text",
    "Date": "text",
    "Number": "text",
    "Url": "text",
    "LongText": "textarea",
    "File": "file",
    "Boolean": "yesno",
    "ValueSelect": "choice",
    "Location": "choice",
    "MultiValueSelect": "multi",
    "EducationHistory": "group",
}
LIMIT = re.compile(r"(?:(\d[\d,]*)\s*(?:-|–|to)\s*)?(\d[\d,]*)[\s-]*(words?|characters?|chars?)\b", re.I)
MIN_BEFORE = re.compile(r"(?:at least|minimum(?: of)?|\bmin\.?|(?<!no )more than|\bover)\s*$", re.I)
MIN_AFTER = re.compile(r"^\s*(?:minimum|min\b|or more)", re.I)


def job_ref(url):
    """Return (organization, posting id) of a jobs.ashbyhq.com posting or application URL."""
    parts = urlsplit(str(url))
    path = [part for part in parts.path.split("/") if part]
    if parts.scheme not in ("http", "https") or parts.hostname != HOST or len(path) < 2:
        raise ValueError("Not an Ashby posting URL")
    return path[0], path[1]


def form_url(url):
    organization, posting = job_ref(url)
    return f"https://{HOST}/{organization}/{posting}/application"


def posting_request(url):
    """The public posting query as data; the caller's fetch layer sends it."""
    organization, posting = job_ref(url)
    body = {
        "operationName": "ApiJobPosting",
        "query": QUERY,
        "variables": {"organizationHostedJobsPageName": organization, "jobPostingId": posting},
    }
    return {"url": ENDPOINT, "body": json.dumps(body).encode("utf-8"), "headers": {"Content-Type": "application/json"}}


def desc_text(raw):
    """Field description HTML as plain lines; list items start with '- '."""
    text = re.sub(r"<li\b[^>]*>", "\n-\n", raw or "", flags=re.I)
    text = re.sub(r"<br\s*/?>|</?(?:p|div|ul|ol|li|h\d)\b[^>]*>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    lines, bullet = [], False
    for line in (" ".join(part.split()) for part in text.split("\n")):
        if line == "-":
            bullet = True
        elif line:
            lines.append("- " + line if bullet else line)
            bullet = False
    text = "\n".join(lines)
    return re.sub(r" ([.,;:!?])", r"\1", text)


def length_limits(text):
    """Maximum length stated in a description: {max_words, max (characters)}; minimums are ignored."""
    limits = {}
    for hit in LIMIT.finditer(text or ""):
        before = text[max(0, hit.start() - 25) : hit.start()]
        after = text[hit.end() : hit.end() + 15]
        if hit.group(1) is None and (MIN_BEFORE.search(before) or MIN_AFTER.search(after)):
            continue
        number = int(hit.group(2).replace(",", ""))
        key = "max_words" if hit.group(3).lower().startswith("word") else "max"
        if number > 0:
            limits[key] = min(limits.get(key, number), number)
    return limits


def _field(entry, section):
    item = entry.get("field") or {}
    if item.get("isDeactivated"):
        return None
    kind = KIND.get(item.get("type"))
    if not kind:
        raise ValueError(f"Unknown Ashby field type: {item.get('type')}")
    options = [value.get("label") for value in item.get("selectableValues") or [] if not value.get("isArchived")]
    if kind == "yesno":
        options = ["Yes", "No"]
    path = item.get("path") or item.get("id")
    field = {
        "id": "resume" if path == "_systemfield_resume" else path,
        "label": item.get("title") or "",
        "kind": kind,
        "required": bool(entry.get("isRequired")),
        "options": options,
        "src_type": item.get("type"),
        "section": section,
    }
    description = desc_text(entry.get("descriptionHtml"))
    if description:
        field.update(desc=description, **length_limits(description))
    return field


def parse_posting(raw):
    """Read a recorded ApiJobPosting response: title, plain description and fields."""
    if raw.get("errors"):
        raise ValueError(str(raw["errors"])[:200])
    posting = (raw.get("data") or {}).get("jobPosting")
    if not posting:
        raise LookupError("Ashby posting is closed")
    fields = []
    for section in (posting.get("applicationForm") or {}).get("sections", []):
        fields += [_field(entry, "form") for entry in section.get("fieldEntries", [])]
    for survey in posting.get("surveyForms") or []:
        for section in survey.get("sections", []):
            fields += [_field(entry, "survey") for entry in section.get("fieldEntries", [])]
    return {
        "title": posting.get("title") or "",
        "description": desc_text(posting.get("descriptionHtml")),
        "fields": [field for field in fields if field],
    }


def _usable(field, answer):
    """The confirmed answer in the form the field accepts, or None."""
    if answer.get("status") != "confirmed" or answer.get("value") in (None, ""):
        return None
    value = answer["value"]
    if field["kind"] == "yesno":
        value = {True: "Yes", False: "No"}.get(value, value) if isinstance(value, bool) else str(value).strip()
        value = next((option for option in field["options"] if option.casefold() == value.casefold()), None)
    elif field["kind"] in ("choice", "multi"):
        values = value if isinstance(value, list) else [value]
        if not values or any(item not in field["options"] for item in values):
            return None
        value = values if field["kind"] == "multi" else values[0]
    elif field["kind"] not in ("text", "textarea"):
        return None
    return None if value is None else dict(answer, value=value)


def plan_answers(fields, profile, job):
    """Split posting fields into confirmed answers, uploads and unanswered required questions."""
    plan = {"known": {}, "uploads": [], "unknown": []}
    for field in fields:
        if field["kind"] == "file":
            plan["uploads"].append(field["id"])
            continue
        answer = _usable(field, resolve_field(profile, field, job))
        if answer is not None:
            plan["known"][field["id"]] = answer
        elif field["required"]:
            plan["unknown"].append(field["id"])
    return plan
