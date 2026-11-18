"""Screen fetched jobs with the filters the user confirmed in setup, and report the result.

The earlier evidence screen (``fetch.evaluate_job``: country, job type, season,
eligibility, sponsorship conflict) runs first; the derived filters
(``job_filters.evaluate``: posting window, job description rules, title terms,
roles, excluded companies) then add their reasons. A filter the user never
confirmed is empty, and unknown job data never excludes a job.
"""

from collections import Counter

from . import job_filters
from .fetch import evaluate_job

# The same check under the two screens' names counts once.
_SAME = {"excluded_company": "excluded_employer"}


def _list(value):
    value = job_filters.raw(value)
    return [str(item) for item in value] if isinstance(value, list) else []


def profile_filters(profile):
    """The ``job_filters.evaluate`` filters held by a saved profile."""
    preferences, targets = profile.get("preferences") or {}, profile.get("targets") or {}
    days = job_filters.raw(preferences.get("posted_within_days"))
    year = job_filters.raw(targets.get("year"))
    return {
        "posted_within_days": days if type(days) is int else None,
        "jd_exclude": _list(preferences.get("jd_exclude")),
        "title_include": _list(preferences.get("title_include")),
        "title_exclude": _list(preferences.get("title_exclude")),
        "excluded_employers": _list(preferences.get("excluded_employers")),
        "roles": list(dict.fromkeys([*_list(targets.get("roles")), *_list(targets.get("include_roles"))])),
        "season": job_filters.raw(targets.get("season")),
        "year": year if type(year) is int else None,
    }


def screen(job, profile, today=None):
    """``{"decision", "reasons", "unknowns"}`` for one fetched job."""
    earlier = evaluate_job(job, profile)
    derived = job_filters.evaluate(job, profile_filters(profile), today=today)
    reasons = []
    for reason in [*earlier["reasons"], *(_SAME.get(item, item) for item in derived["reasons"])]:
        if reason not in reasons:
            reasons.append(reason)
    return {
        "decision": "exclude" if reasons else earlier["decision"],
        "reasons": reasons,
        "unknowns": earlier["unknowns"],
    }


def _plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def report(*, boards, failed, fetched, passed, excluded, language="en"):
    """Counts plus one summary line; ``excluded`` is the list of excluded verdicts."""
    reasons = Counter(reason for item in excluded for reason in item["reasons"])
    reasons = dict(sorted(reasons.items()))
    read = boards - failed
    listed = ", ".join(f"{reason} {count}" for reason, count in reasons.items())
    if language == "vi":
        summary = (f"Lần fetch đầu: {fetched} job từ {read}/{boards} board; {passed} job qua bộ lọc, "
                   f"{len(excluded)} job bị lọc" + (f" ({listed})" if listed else "") + ".")
        if failed:
            summary += f" {failed} board không đọc được."
    else:
        summary = (f"First fetch: {_plural(fetched, 'job')} from {read} of {_plural(boards, 'board')}; "
                   f"{passed} passed your filters, {len(excluded)} filtered out" + (f" ({listed})" if listed else "") + ".")
        if failed:
            summary += f" {_plural(failed, 'board')} could not be read."
    return {
        "boards": boards,
        "boards_failed": failed,
        "fetched": fetched,
        "passed": passed,
        "excluded": len(excluded),
        "reasons": reasons,
        "summary": summary,
    }
