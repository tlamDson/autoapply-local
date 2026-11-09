"""One editable intake, explicit preview/confirmation and revision invalidation.

Assumption: runtime root contains profile.json/config.json/answers.json and user/;
all profile edits invalidate reviews conservatively, even non-dependent edits.
"""

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .profile import fact, validate_profile


FIELDS = {
    "runtime": "available_engines apply_engine tailor_engine mode language",
    "contact": "legal_name first_name last_name preferred_name application_email gmail_email phone phone_country_code city state country postcode street linkedin github portfolio",
    "resumes": "main_pdf alternatives role_mapping newest master_source withdrawn_claims superseded_facts",
    "education": "records current_program return_after_internship class_standing_now class_standing_at_internship gpa gpa_scale major_gpa display_gpa transcript_available",
    "targets": "job_types roles include_roles exclude_roles include_stacks exclude_stacks adjacent_roles seniority countries",
    "logistics": "season year window earliest_start latest_end hours_per_week weeks term_time_availability preferred_locations excluded_locations work_modes relocation travel housing relocation_support",
    "authorization": "records current_status authorized_now authorized_at_start restrictions sponsorship_now sponsorship_future",
    "international_student": "status intended_mechanism cpt opt stem_opt visa_expiry program_end work_auth_expiry school_support dso_support employer_support unknown_conditions",
    "filters": "eligibility no_sponsor_conflict priority_employers excluded_employers include_big_tech",
    "experience": "records current most_recent unlisted_projects skills metrics certifications languages",
    "compensation": "minimum range currency period unpaid_consent pronouns eeo decline_preferences",
    "history": "import_files alternate_emails avoid_employers",
    "tailor": "enabled scope employers fact_sources pages section_order summary gpa honors required_entries excluded_entries required_bullets excluded_bullets allow_rewrite allow_reselection missing_facts filename",
    "resources": "gmail_opt_in gmail_account gmail_lookback gmail_permissions lanes chrome_override boards",
}
LIST_FIELDS = set(
    "available_engines alternatives withdrawn_claims superseded_facts records job_types roles include_roles exclude_roles include_stacks exclude_stacks adjacent_roles countries preferred_locations excluded_locations work_modes restrictions unknown_conditions priority_employers excluded_employers unlisted_projects skills metrics certifications languages decline_preferences import_files alternate_emails avoid_employers employers fact_sources section_order required_entries excluded_entries required_bullets excluded_bullets gmail_permissions".split()
)
BOOL_FIELDS = set(
    "return_after_internship display_gpa transcript_available authorized_now authorized_at_start sponsorship_now sponsorship_future include_big_tech unpaid_consent enabled allow_rewrite allow_reselection gmail_opt_in term_time_availability travel relocation_support school_support dso_support employer_support".split()
)
INT_FIELDS = {"lanes", "gmail_lookback", "pages", "year", "weeks"}
OBJECT_FIELDS = {"role_mapping", "cpt", "opt", "stem_opt", "eeo"}


def blank_intake():
    groups = {}
    for group, fields in FIELDS.items():
        groups[group] = {
            name: [] if name in LIST_FIELDS else {} if name in OBJECT_FIELDS else None for name in fields.split()
        }
    groups["resources"].update(lanes=1, gmail_opt_in=False, gmail_lookback=7, boards=None)
    groups["runtime"].update(mode="quick", language="en")
    groups["tailor"].update(enabled=False, pages=1, missing_facts="omit", filename="resume")
    return {"schema_version": 1, "groups": groups, "answers": []}


def template_text():
    from .questionnaire import questionnaire_text

    return (
        "# Profile intake / Thông tin ứng viên\n\n"
        "Edit the single JSON block. null = unknown, never No. All groups stay visible.\n"
        "Sửa một khối JSON dưới đây. null = chưa biết, không phải Không.\n"
        "Use raw values or fact envelopes: {value,status,provenance,confirmed_at,scope}.\n"
        "Statuses: confirmed, unknown, not_applicable, declined. Scope identifiers: country, "
        "job_type, employer, question, effective_at. Confirm only facts you supplied.\n"
        "Education records: id, school, degree, major, status (current/completed/leave), start, "
        "end, graduation {value:YYYY-MM,precision:month,kind:expected|actual}. Year precision is allowed.\n"
        "Experience records: id, employer, title, start, end, location, description, metrics "
        "(precise/estimated). Skills need evidence work/project/course/learning and source_id.\n"
        "Authorization records: id, country, job_type, employer, current_status, authorized_now, "
        "authorized_at_start, sponsorship_now, sponsorship_future, restrictions, mechanism.\n"
        "CPT/OPT/STEM_OPT each: status not_applied/planned/pending/granted, valid_from, valid_to, "
        "school_support, dso_support, employer_support; keep visa/program/auth expiry separate.\n"
        "Câu nhạy cảm, pháp lý, đồng ý, quan hệ công ty và tự luận cần xác nhận đúng nguyên văn câu hỏi.\n"
        "Targets / Mục tiêu: internship/co-op/newgrad/experienced; SWE/backend/frontend/fullstack/"
        "mobile/platform/SRE/DevOps/data/AI/ML/research/QA/SDET/security/custom.\n"
        "Filters: eligibility broad_unknown|explicit_only; no_sponsor_conflict review|exclude.\n"
        "Tailor: scope all|priority|bigtech, user employer list; missing_facts ask|omit; pages 1|2.\n"
        "Gmail is opt-in: permissions search/read/current_job_verification and exact account.\n"
        "Gmail chỉ đọc khi bạn cho phép và chỉ với tài khoản đã chọn; tối đa 3 lane khi tự chọn.\n\n"
        + questionnaire_text()
        + "```json\n"
        + json.dumps(blank_intake(), ensure_ascii=False, indent=2)
        + "\n```\n"
    )


def _load(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else copy.deepcopy(default)


def setup(root):
    from .boards import install_defaults
    from .storage import Store, atomic_write_json

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for relative in ("templates/profile-intake.md", "user/profile-intake.md"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open("x", encoding="utf-8") as handle:
                handle.write(template_text())
    defaults = {
        "profile.json": {
            "schema_version": 1,
            "revision": 1,
            "contact": {},
            "education": [],
            "authorization": [],
            "experience": [],
            "targets": {},
            "skills": [],
            "preferences": {},
        },
        "config.json": {
            "runtime": {"apply_engine": None, "tailor_engine": None},
            "resources": {"lanes": 1, "gmail_opt_in": False},
            "resumes": {},
        },
        "answers.json": {"profile_revision": 1, "valid": True, "answers": []},
    }
    for name, value in defaults.items():
        if name != "config.json" and not (root / name).exists():
            atomic_write_json(root / name, value)
    install_defaults(root, defaults["config.json"])
    Store(root)
    return {"status": "ready", "intake": str(root / "user/profile-intake.md")}


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_intake(path):
    text = Path(path).read_text(encoding="utf-8")
    blocks = re.findall(r"```json\s*\n(.*?)\n```", text, re.S)
    if len(blocks) != 1:
        raise ValueError("Intake needs exactly one fenced JSON block")
    data = json.loads(blocks[0], object_pairs_hook=_unique_pairs)
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("groups"), dict):
        raise ValueError("Invalid intake schema")
    return data


def _validate_document(data):
    groups = data["groups"]
    if set(groups) != set(FIELDS):
        raise ValueError("Keep all fourteen intake groups")
    for group, fields in FIELDS.items():
        if not isinstance(groups[group], dict):
            raise ValueError(f"{group} must be an object")
        for field in fields.split():
            if group == "resources" and field == "boards":
                value = groups[group].get(field)
                if value is not None:
                    from .boards import normalize

                    groups[group][field] = normalize(value)
                continue
            if field not in groups[group]:
                raise ValueError(f"Missing {group}.{field}")
            value = groups[group][field]
            if isinstance(value, dict) and "value" in value and "status" in value:
                validate_profile({"schema_version": 1, "revision": 1, "intake_fact": {field: value}})
                value = value["value"]
            if value is None:
                continue
            expected = (
                list
                if field in LIST_FIELDS
                else bool
                if field in BOOL_FIELDS
                else int
                if field in INT_FIELDS
                else dict
                if field in OBJECT_FIELDS
                else None
            )
            if expected and type(value) is not expected:
                raise ValueError(f"{group}.{field} must be {expected.__name__}")
            if field == "lanes" and value not in (1, 2, 3):
                raise ValueError("Choose one to three lanes")
            if group == "tailor" and field == "pages" and value not in (1, 2):
                raise ValueError("Choose one or two pages")
    answers = data.get("answers", [])
    validate_profile({"schema_version": 1, "revision": 1, "answers": answers})
    for answer in answers:
        if (
            not isinstance(answer, dict)
            or not isinstance(answer.get("question"), str)
            or not answer["question"].strip()
        ):
            raise ValueError("Each answer needs its exact question")
        if answer.get("status") == "confirmed":
            scope = answer.get("scope", {})
            if any(not scope.get(key) for key in ("country", "job_type", "employer", "question", "effective_at")):
                raise ValueError("Confirmed answers need country, job_type, employer, question and effective_at")
            if scope["question"] != answer["question"]:
                raise ValueError("Answer question must match its exact scope")
    return data


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preview_import(root, path):
    root, path = Path(root), Path(path)
    data = _validate_document(read_intake(path))
    old = _load(root / "intake-import.json", blank_intake())
    changes = []
    for group, fields in data["groups"].items():
        for field, value in fields.items():
            before = old["groups"].get(group, {}).get(field)
            if before != value:
                changes.append({"field": f"{group}.{field}", "old": before, "new": value})
    if data.get("answers", []) != old.get("answers", []):
        changes.append({"field": "answers", "old": old.get("answers", []), "new": data["answers"]})
    current = _load(root / "profile.json")
    updated = {**current, **_profile_changes(data), "revision": current["revision"] + 1}
    needs_reconfirmation = _reconcile_answers(current, updated, data.get("answers"))
    return {
        "changes": changes,
        "path": str(path.resolve()),
        "sha256": _digest(path),
        "profile_sha256": _digest(root / "profile.json"),
        "config_sha256": _digest(root / "config.json"),
        "document": data,
        "needs_reconfirmation": needs_reconfirmation,
    }


def _as_fact(value, field):
    if isinstance(value, dict) and "value" in value and "status" in value:
        return copy.deepcopy(value)
    return fact(value, source_id=f"intake:{field}")


def _record(record, group, index):
    if not isinstance(record, dict):
        raise ValueError(f"{group} record must be an object")
    result = {"id": record.get("id", f"{group}:{index + 1}")}
    for field, value in record.items():
        if field != "id":
            result[field] = (
                [_as_fact(item, f"{group}:{index}:{field}") for item in value]
                if field == "metrics" and isinstance(value, list)
                else _as_fact(value, f"{group}:{index}:{field}")
            )
    return result


def _profile_changes(data):
    groups = data["groups"]
    changes = {"contact": {key: _as_fact(value, f"contact.{key}") for key, value in groups["contact"].items()}}
    for group in ("education", "authorization", "experience"):
        changes[group] = [_record(record, group, index) for index, record in enumerate(groups[group]["records"])]
        changes[group + "_details"] = {
            key: _as_fact(value, f"{group}.{key}") for key, value in groups[group].items() if key != "records"
        }
    for group in ("targets", "logistics", "international_student", "compensation", "history"):
        changes[group] = copy.deepcopy(groups[group])
    # Fetch consumes season/year directly from targets.
    changes["targets"].update({key: copy.deepcopy(groups["logistics"][key]) for key in ("season", "year")})
    changes["preferences"] = copy.deepcopy(groups["filters"])
    changes["skills"] = copy.deepcopy(groups["experience"]["skills"])
    return changes


def _without_confirmation_time(value):
    if isinstance(value, dict):
        return {key: _without_confirmation_time(child) for key, child in value.items() if key != "confirmed_at"}
    if isinstance(value, list):
        return [_without_confirmation_time(child) for child in value]
    return value


def _confirmation_time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _explicit_answer(answer):
    return (
        answer.get("status") == "confirmed"
        and answer.get("value") is not None
        and answer.get("value") != ""
        and _confirmation_time(answer.get("confirmed_at")) is not None
        and answer.get("provenance", {}).get("kind") == "user_confirmation"
        and bool(answer.get("provenance", {}).get("source_id"))
        and isinstance(answer.get("scope"), dict)
    )


def _answer_identity(answer):
    return json.dumps([answer.get("question"), answer.get("scope")], sort_keys=True)


def _index_answers(answers):
    indexed = {}
    for answer in answers:
        identity = _answer_identity(answer)
        if identity in indexed:
            raise ValueError("Duplicate answer identity")
        indexed[identity] = answer
    return indexed


def _reconcile_answers(old, updated, incoming_answers):
    """Retain invalidated evidence until that exact answer is freshly confirmed."""
    dependencies = (
        "education",
        "education_details",
        "authorization",
        "authorization_details",
        "international_student",
        "targets",
        "logistics",
    )
    facts_changed = any(
        _without_confirmation_time(old.get(key)) != _without_confirmation_time(updated.get(key)) for key in dependencies
    )
    now = datetime.now(timezone.utc).isoformat()
    incoming = _index_answers(copy.deepcopy(incoming_answers or []))
    previous_answers = _index_answers(old.get("answers", []))
    reconciled = []
    for identity, previous in previous_answers.items():
        saved = copy.deepcopy(previous)
        supplied = incoming.pop(identity, None)
        if facts_changed:
            saved.update(valid=False, invalidated_at=now, invalidated_revision=updated["revision"])
        if supplied is not None:
            # Identity already matched; presentation IDs do not establish facts.
            same_schema = type(previous.get("value")) is type(supplied.get("value")) and previous.get(
                "provenance"
            ) == supplied.get("provenance")
            unchanged = same_schema and all(previous.get(key) == supplied.get(key) for key in ("value", "status"))
            if unchanged and saved.get("valid") is not False:
                confirmed = _confirmation_time(supplied.get("confirmed_at"))
                previous_confirmation = _confirmation_time(previous.get("confirmed_at"))
                if (
                    _explicit_answer(supplied)
                    and previous_confirmation is not None
                    and confirmed > previous_confirmation
                ):
                    saved.update(supplied, valid=True)
            else:
                confirmed = _confirmation_time(supplied.get("confirmed_at"))
                boundaries = [previous.get("confirmed_at"), saved.get("invalidated_at")]
                fresh = confirmed is not None and all(
                    _confirmation_time(boundary) is not None and confirmed > _confirmation_time(boundary)
                    for boundary in boundaries
                    if boundary is not None
                )
                if same_schema and _explicit_answer(supplied) and fresh:
                    saved.update(supplied, valid=True)
                else:
                    if saved.get("valid") is not False:
                        saved.update(invalidated_at=now, invalidated_revision=updated["revision"])
                    saved["valid"] = False
        reconciled.append(saved)
    for answer in incoming.values():
        answer["valid"] = _explicit_answer(answer)
        reconciled.append(answer)
    updated["answers"] = reconciled
    return [
        {
            "id": answer.get("id") or "answer:" + hashlib.sha256(_answer_identity(answer).encode()).hexdigest()[:16],
            "question": answer["question"],
        }
        for answer in reconciled
        if answer.get("valid") is False
    ]


def apply_import(root, preview, confirm=False, *, precondition=None):
    if not confirm:
        return {
            "applied": False,
            "changes": preview["changes"],
            "needs_reconfirmation": preview["needs_reconfirmation"],
        }
    from .storage import atomic_write_json, lock

    root = Path(root)
    with lock(root / "profile-update.lock"):
        if precondition is not None:
            precondition()
        if _digest(preview["path"]) != preview["sha256"] or _digest(root / "profile.json") != preview["profile_sha256"]:
            raise ValueError("Intake or profile changed; preview again")
        if _digest(root / "config.json") != preview["config_sha256"]:
            raise ValueError("Config changed; preview again")
        data = _validate_document(read_intake(preview["path"]))
        groups = data["groups"]
        current = _load(root / "profile.json")
        changes = _profile_changes(data)
        # Validate before any configuration/fact mutation.
        updated = {**current, **changes, "revision": current["revision"] + 1}
        validate_profile(updated)
        result = _save_profile(root, current, updated, data.get("answers"))
        config = {
            **_load(root / "config.json"),
            **{key: copy.deepcopy(groups[key]) for key in ("runtime", "resources", "resumes", "tailor")},
        }
        boards = config["resources"].pop("boards", None)
        if boards is not None:
            config["boards"] = boards
        atomic_write_json(root / "config.json", config)
        atomic_write_json(root / "intake-import.json", data)
        return {**result, "applied": True}


def _save_profile(root, old, updated, incoming_answers=None):
    from .storage import atomic_write_json

    needs_reconfirmation = _reconcile_answers(old, updated, incoming_answers)
    validate_profile(updated)
    atomic_write_json(root / "profile.json", updated)
    answers = updated["answers"]
    atomic_write_json(
        root / "answers.json",
        {
            "profile_revision": updated["revision"],
            "valid": bool(answers) and all(answer.get("valid") is not False for answer in answers),
            "answers": copy.deepcopy(answers),
        },
    )
    invalidated = []
    for name in ("reviews",):
        path = root / f"{name}.json"
        records = _load(path)
        if records is not None:
            if isinstance(records, list):
                records = [{**item, "valid": False} for item in records]
            else:
                records["valid"] = False
            atomic_write_json(path, records)
        invalidated.append(name)
    # Workflow reviews also bind profile revision, so stale ledger reviews fail.
    return {
        "revision": updated["revision"],
        "invalidated": ["answers", *invalidated],
        "needs_reconfirmation": needs_reconfirmation,
    }


def update_profile(root, changes, confirm=False):
    from .storage import lock

    root = Path(root)
    if not confirm:
        return {"applied": False, "changes": changes}
    with lock(root / "profile-update.lock"):
        old = _load(root / "profile.json")
        updated = {**old, **copy.deepcopy(changes), "revision": old["revision"] + 1}
        validate_profile(updated)
        return _save_profile(root, old, updated, changes.get("answers"))
