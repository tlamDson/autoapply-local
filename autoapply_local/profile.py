"""Confirmed candidate facts; exact question scope is deliberately conservative."""

import copy
import re
from datetime import date, datetime, timezone


STATUSES = {"confirmed", "unknown", "not_applicable", "declined"}
BOOLEAN_FIELDS = {
    "authorized_now",
    "authorized_at_start",
    "sponsorship_now",
    "sponsorship_future",
    "return_after_internship",
    "display_gpa",
    "transcript_available",
    "unpaid_consent",
    "term_time_availability",
    "travel",
    "relocation_support",
    "school_support",
    "dso_support",
    "employer_support",
}

DATE_FIELDS = {
    "start",
    "end",
    "valid_from",
    "valid_to",
    "visa_expiry",
    "program_end",
    "work_auth_expiry",
    "earliest_start",
    "latest_end",
}


def _date(value, precision=None):
    if value in (None, "", "present", "current"):
        return
    if not isinstance(value, str):
        raise ValueError("Dates must retain year, month or day precision")
    formats = {"year": r"\d{4}", "month": r"\d{4}-\d{2}", "day": r"\d{4}-\d{2}-\d{2}"}
    if precision is not None and (precision not in formats or not re.fullmatch(formats[precision], value)):
        raise ValueError("Date precision does not match its value")
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        raise ValueError("Invalid date")
    try:
        date.fromisoformat(value + ("-01-01" if len(value) == 4 else "-01" if len(value) == 7 else ""))
    except ValueError as error:
        raise ValueError("Invalid calendar date") from error


def fact(value=None, status=None, source_id="intake", scope=None):
    status = status or ("unknown" if value is None or value == "" else "confirmed")
    return {
        "value": value,
        "status": status,
        "provenance": {"kind": "user_confirmation", "source_id": source_id},
        "confirmed_at": datetime.now(timezone.utc).isoformat() if status == "confirmed" else None,
        "scope": copy.deepcopy(scope or {}),
    }


def validate_profile(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Profile must be a schema_version=1 object")
    if type(data.get("revision")) is not int or data["revision"] < 1:
        raise ValueError("Profile revision must be a positive integer")
    for name in ("education", "authorization", "experience", "answers", "skills"):
        if name in data and not isinstance(data[name], list):
            raise ValueError(f"{name} must be a list")

    def visit(value, field="", path=()):
        if isinstance(value, dict):
            if "value" in value and "status" in value:
                if value["status"] not in STATUSES:
                    raise ValueError(f"Invalid fact status at {field}")
                if field in BOOLEAN_FIELDS and value["value"] is not None and type(value["value"]) is not bool:
                    raise ValueError(f"{field} must be a boolean or unknown")
                source = value.get("provenance")
                if not isinstance(source, dict) or not source.get("source_id"):
                    raise ValueError(f"Missing source at {field}")
                if not isinstance(source.get("kind"), str) or not source["kind"]:
                    raise ValueError(f"Missing provenance kind at {field}")
                if value["status"] == "confirmed":
                    if source["kind"] != "user_confirmation":
                        raise ValueError("Only explicit user confirmation establishes facts")
                    if value["value"] is None or value["value"] == "":
                        raise ValueError("Confirmed facts need a value")
                    try:
                        timestamp = datetime.fromisoformat(value["confirmed_at"].replace("Z", "+00:00"))
                        if timestamp.tzinfo is None:
                            raise ValueError("Confirmation timestamp needs timezone")
                    except (KeyError, TypeError, AttributeError, ValueError) as error:
                        raise ValueError(f"Invalid confirmation timestamp at {field}") from error
                if not isinstance(value.get("scope"), dict):
                    raise ValueError(f"Fact scope must be an object at {field}")
                payload = value["value"]
                if field in DATE_FIELDS:
                    _date(payload)
                if field == "graduation" and payload is not None:
                    if not isinstance(payload, dict) or payload.get("kind") not in ("expected", "actual"):
                        raise ValueError("Graduation needs expected/actual kind")
                    _date(payload.get("value"), payload.get("precision"))
                    if not payload.get("value") or payload.get("precision") not in ("year", "month", "day"):
                        raise ValueError("Graduation needs a date with explicit precision")
                if field == "status" and "education" in path and payload not in (None, "current", "completed", "leave"):
                    raise ValueError("Invalid education status")
                if field == "mechanism_status" and payload not in (
                    None,
                    "not_applied",
                    "planned",
                    "pending",
                    "granted",
                ):
                    raise ValueError("Invalid authorization mechanism status")
                return
            for key, child in value.items():
                visit(child, key, (*path, field))
        elif isinstance(value, list):
            for child in value:
                visit(child, field, path)
        elif field in BOOLEAN_FIELDS and value is not None and type(value) is not bool:
            raise ValueError(f"{field} must be a boolean or unknown")
        elif field in DATE_FIELDS:
            _date(value)

    visit(data)
    return copy.deepcopy(data)


def resolve_answer(data, question, scope):
    validate_profile(data)
    for answer in reversed(data.get("answers", [])):
        if (
            answer.get("question") == question
            and answer.get("scope") == scope
            and answer.get("status") == "confirmed"
            and answer.get("valid") is not False
        ):
            return copy.deepcopy({key: value for key, value in answer.items() if key != "question"})
    return fact(None, "unknown", source_id="unanswered", scope=scope)


def resolve_field(data, field, job):
    """Resolve exact common labels/observed record bindings, never fuzzy auth."""
    label = str(field.get("label", ""))
    normalized = " ".join(label.casefold().replace("_", " ").split())
    contact_keys = {
        "legal name": "legal_name",
        "name": "legal_name",
        "email": "application_email",
        "application email": "application_email",
        "email address": "application_email",
        "preferred name": "preferred_name",
        "phone": "phone",
        "phone number": "phone",
        "city": "city",
        "first name": "first_name",
        "given name": "first_name",
        "last name": "last_name",
        "family name": "last_name",
    }
    if normalized in contact_keys:
        return copy.deepcopy(data.get("contact", {}).get(contact_keys[normalized], fact(None, "unknown")))
    binding = field.get("record", {})
    kind = binding.get("kind")
    keys = {
        "education": {"school": "school", "university": "school", "degree": "degree", "major": "major"},
        "experience": {"job title": "title", "title": "title", "employer": "employer", "company": "employer"},
    }
    if kind in keys and normalized in keys[kind]:
        records = data.get(kind, [])
        index = binding.get("index")
        matched = None
        if "index" in binding:
            if type(index) is not int or not 0 <= index < len(records):
                return fact(None, "unknown")
            matched = records[index]
            if "id" in binding and matched.get("id") != binding["id"]:
                return fact(None, "unknown")
        elif binding.get("id"):
            matches = [item for item in records if item.get("id") == binding["id"]]
            if len(matches) == 1:
                matched = matches[0]
        if matched:
            answer = matched.get(keys[kind][normalized], fact(None, "unknown"))
            scope = answer.get("scope", {})
            if scope and scope != {kind + "_id": matched["id"]}:
                return fact(None, "unknown")
            return copy.deepcopy(answer)
        return fact(None, "unknown")
    scope = {
        "country": job.get("country"),
        "job_type": job.get("level"),
        "employer": job.get("company"),
        "question": label,
        "effective_at": job.get("start_date"),
    }
    return resolve_answer(data, label, scope)
