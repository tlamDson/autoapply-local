"""Job filters derived from the user's setup answers, and their offline evaluation.

Derivation only reads explicit answers: an unknown sponsorship need, season or
job type never produces a filter. The user reviews and edits the result in setup.
"""

from datetime import date, timedelta

from . import job_rules


DEFAULT_POSTED_DAYS = 7
SENIOR_TERMS = ("senior", "sr.", "staff", "principal", "lead", "manager", "director")
FULL_TIME_TERMS = ("full-time", "new grad")
INTERN_TERMS = {"internship": "intern", "co-op": "co-op"}
FIELDS = ("posted_within_days", "jd_exclude", "title_include", "title_exclude", "excluded_employers")
JD_LABELS = {
    "no_sponsorship": ("no visa sponsorship", "không bảo lãnh visa"),
    "us_citizen": ("U.S. citizen or permanent resident only", "chỉ công dân hoặc thường trú nhân Mỹ"),
    "clearance": ("security clearance", "yêu cầu security clearance"),
    "itar": ("ITAR / U.S. person", "ITAR / U.S. person"),
}


def raw(value):
    """Unwrap a fact record to its value; unknown facts read as None."""
    if isinstance(value, dict) and "value" in value and "status" in value:
        return value["value"] if value["status"] == "confirmed" else None
    return value


def _unique(items):
    seen, result = set(), []
    for item in items:
        text = str(item).strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result


def sponsorship_need(authorization):
    """True when any explicit answer needs sponsorship, False when all known answers say no, else None."""
    values = [raw(authorization.get(key)) for key in ("sponsorship_now", "sponsorship_future")]
    for record in raw(authorization.get("records")) or []:
        if isinstance(record, dict):
            values.extend(raw(record.get(key)) for key in ("sponsorship_now", "sponsorship_future"))
    if any(value is True for value in values):
        return True
    return False if any(value is False for value in values) else None


def derive(groups):
    targets, logistics = groups.get("targets", {}), groups.get("logistics", {})
    filters, history = groups.get("filters", {}), groups.get("history", {})
    job_types = set(raw(targets.get("job_types")) or [])
    include, exclude = [], []
    if job_types and job_types <= set(INTERN_TERMS):
        include = [INTERN_TERMS[kind] for kind in INTERN_TERMS if kind in job_types]
        exclude = [*SENIOR_TERMS, *FULL_TIME_TERMS]
    elif (job_types and "experienced" not in job_types) or raw(targets.get("seniority")) == "entry":
        exclude = list(SENIOR_TERMS)
    exclude = _unique([*exclude, *(str(role).casefold() for role in raw(targets.get("exclude_roles")) or [])])
    days = raw(filters.get("posted_within_days"))
    return {
        "posted_within_days": days if type(days) is int else DEFAULT_POSTED_DAYS,
        "jd_exclude": list(job_rules.JD_RULES) if sponsorship_need(groups.get("authorization", {})) else [],
        "title_include": include,
        "title_exclude": exclude,
        "excluded_employers": _unique([*(raw(filters.get("excluded_employers")) or []),
                                       *(raw(history.get("avoid_employers")) or [])]),
        "roles": _unique([*(raw(targets.get("roles")) or []), *(raw(targets.get("include_roles")) or [])]),
        "season": raw(logistics.get("season")),
        "year": raw(logistics.get("year")),
    }


def intake_values(derived):
    """The editable ``filters.*`` intake fields that setup writes on Confirm."""
    return {"filters." + name: list(derived[name]) if isinstance(derived[name], list) else derived[name]
            for name in FIELDS}


def summary(derived, language="en"):
    vi = language == "vi"
    none = "không có" if vi else "none"
    join = lambda items: ", ".join(items) if items else none
    season = " ".join(str(part) for part in (derived.get("season"), derived.get("year")) if part)
    season = season.replace("_", " ").title() if season else none
    jd = [JD_LABELS[rule][1 if vi else 0] for rule in derived["jd_exclude"]]
    if vi:
        lines = [f"chỉ job đăng trong {derived['posted_within_days']} ngày",
                 f"bỏ job ghi: {join(jd)}", f"tiêu đề phải có: {join(derived['title_include'])}",
                 f"tiêu đề không được có: {join(derived['title_exclude'])}", f"role: {join(derived['roles'])}",
                 f"kỳ: {season}", f"công ty loại trừ: {join(derived['excluded_employers'])}"]
        return "Bộ lọc job từ câu trả lời của bạn: " + "; ".join(lines) + "."
    lines = [f"posted within {derived['posted_within_days']} days",
             f"drop jobs that state: {join(jd)}", f"title must include: {join(derived['title_include'])}",
             f"title must not include: {join(derived['title_exclude'])}", f"roles: {join(derived['roles'])}",
             f"season: {season}", f"excluded companies: {join(derived['excluded_employers'])}"]
    return "Job filters from your answers: " + "; ".join(lines) + "."


def _posted(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def evaluate(job, filters, today=None):
    """Return {"keep", "reasons"} for one job; unknown job data never excludes it."""
    today = date.fromisoformat(today) if isinstance(today, str) else today or date.today()
    title = str(job.get("title") or job.get("role") or "")
    reasons = []
    company = str(job.get("company") or "").strip().casefold()
    if company and company in {str(item).strip().casefold() for item in filters.get("excluded_employers") or []}:
        reasons.append("excluded_company")
    days, posted = filters.get("posted_within_days"), _posted(job.get("posted_at")) if job.get("posted_at") else None
    if type(days) is int and posted is not None and posted < today - timedelta(days=days):
        reasons.append("posted")
    reasons.extend("title:" + term for term in filters.get("title_exclude") or [] if job_rules.has_term(title, term))
    include = filters.get("title_include") or []
    if include and not any(job_rules.has_term(title, term) for term in include):
        reasons.append("title_type")
    roles = filters.get("roles") or []
    if roles and not any(job_rules.role_matches(title, role) for role in roles):
        reasons.append("role")
    season = filters.get("season")
    seasons = job_rules.title_seasons(title)
    if season in job_rules.SEASONS and seasons and season not in seasons:
        reasons.append("season")
    years = job_rules.title_years(title)
    if type(filters.get("year")) is int and years and filters["year"] not in years:
        reasons.append("year")
    flags = job_rules.jd_flags(job.get("description", ""))
    reasons.extend("jd:" + rule for rule in filters.get("jd_exclude") or [] if rule in flags)
    return {"keep": not reasons, "reasons": reasons}
