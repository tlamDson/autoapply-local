"""Bounded public-board HTTP access and evidence-based, user-configured filters."""

import html
import json
import re
import time
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


class Transport:
    def _json(self, url, timeout, data=None):
        if urlparse(url).scheme != "https":
            raise ValueError("Public job endpoints require HTTPS")
        request = Request(
            url,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"User-Agent": "autoapply-local/0.1", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=timeout) as response:
            body = response.read(10_000_001)
        if len(body) > 10_000_000:
            raise ValueError("Job response exceeds 10 MB")
        return json.loads(body)

    def get_json(self, url, timeout):
        return self._json(url, timeout)

    def post_json(self, url, timeout, data):
        return self._json(url, timeout, data)


def _text(value):
    if isinstance(value, dict):
        value = value.get("name", value.get("label", ""))
    return html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).strip()


def _country(location):
    return "US" if re.search(r"\b(US|USA|United States)\b", location, re.I) else None


def _rippling_parts(path):
    parts = path.strip("/").split("/")
    return parts[1:] if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]) else parts


class Fetcher:
    def __init__(self, transport=None, timeout=10, retries=2):
        if not 0 < timeout <= 60 or not 0 <= retries <= 5:
            raise ValueError("Bound timeout to 60 seconds and retries to five")
        self.transport, self.timeout, self.retries = transport or Transport(), timeout, retries

    def _request(self, method, url, **kwargs):
        for attempt in range(self.retries + 1):
            try:
                return getattr(self.transport, method)(url, timeout=self.timeout, **kwargs)
            except (TimeoutError, URLError, ConnectionError) as error:
                if isinstance(error, HTTPError) and error.code not in (429, 500, 502, 503, 504):
                    raise
                if attempt == self.retries:
                    raise
                # No unbounded retry or request flood; tests inject a transport.
                if isinstance(self.transport, Transport):
                    time.sleep(min(2**attempt, 4))

    def fetch(self, ats, board):
        postings = []
        if ats == "greenhouse":
            url = (
                board.get("endpoint")
                or f"https://boards-api.greenhouse.io/v1/boards/{quote(board['board'], safe='')}/jobs?content=true"
            )
            payload = self._request("get_json", url)
            postings = payload.get("jobs", [])
        elif ats == "workday":
            host, tenant, site = board["host"], board["tenant"], board["site"]
            if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
                raise ValueError("Invalid Workday host")
            endpoint = (
                board.get("endpoint") or f"https://{host}/wday/cxs/{quote(tenant, safe='')}/{quote(site, safe='')}/jobs"
            )
            pages = set()
            for offset in range(0, 2000, 20):
                payload = self._request(
                    "post_json",
                    endpoint,
                    data={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": board.get("search", "")},
                )
                page = payload.get("jobPostings", [])
                signature = json.dumps(page, sort_keys=True)
                if page and signature in pages:
                    raise ValueError("Workday repeated pagination page; narrow board search")
                pages.add(signature)
                postings.extend(page)
                if not page or len(postings) >= payload.get("total", len(postings)):
                    break
            else:
                raise ValueError("Workday pagination exceeds 2,000 postings; narrow board search")
        elif ats == "rippling":
            url = (
                board.get("endpoint")
                or f"https://api.rippling.com/platform/api/ats/v1/board/{quote(board['board'], safe='')}/jobs"
            )
            payload = self._request("get_json", url)
            postings = payload if isinstance(payload, list) else payload.get("jobs", payload.get("data", []))
        else:
            raise ValueError(f"Unsupported ATS: {ats}")
        result = {}
        for item in postings:
            role = _text(item.get("title", item.get("name")))
            location = _text(item.get("location", item.get("locationsText")))
            if ats == "rippling":
                location = _text(item.get("workLocation")) or location
            if ats == "workday":
                identity = (item.get("bulletFields") or [item["externalPath"]])[0]
                url = f"https://{board['host']}/en-US/{board['site']}" + item["externalPath"]
            else:
                identity = item.get("id", item.get("uuid"))
                url = item.get("absolute_url", item.get("url"))
            if not identity or not url or not role:
                raise ValueError("Public posting missing identity, URL or title")
            description = _text(item.get("content", item.get("description")))
            season = next(
                (
                    word
                    for word in ("summer", "winter", "spring", "fall")
                    if re.search(rf"\b{word}\b", role + " " + description, re.I)
                ),
                None,
            )
            year = re.search(r"\b(20\d{2})\b", role + " " + description)
            job_id = f"{ats}:{board.get('board', board.get('tenant', 'board'))}:{identity}"
            result[job_id] = {
                "id": job_id,
                "ats": ats,
                "company": board.get("company", board.get("board", "")),
                "role": role,
                "location": location,
                "country": _country(location),
                "url": url,
                "description": description,
                "eligibility": "unknown",
                "status": "pending",
                "season": season,
                "year": int(year[1]) if year else None,
                "level": "internship"
                if re.search(r"intern|co-op", role, re.I)
                else "newgrad"
                if re.search(r"new grad|graduate", role, re.I)
                else None,
            }
        return list(result.values())

    def import_url(self, url):
        parsed = urlparse(url)
        board = None
        if parsed.hostname in ("job-boards.greenhouse.io", "boards.greenhouse.io", "job-boards.eu.greenhouse.io"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 3 and parts[1] == "jobs":
                ats, board, identity = "greenhouse", {"board": parts[0], "company": parts[0]}, parts[2]
        elif parsed.hostname and parsed.hostname.endswith(".myworkdayjobs.com"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 3 and "job" in parts:
                site = parts[1] if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]) else parts[0]
                ats, identity = "workday", parts[-1]
                board = {
                    "host": parsed.hostname,
                    "tenant": parsed.hostname.split(".")[0],
                    "site": site,
                    "company": parsed.hostname.split(".")[0],
                }
        elif parsed.hostname == "ats.rippling.com":
            parts = _rippling_parts(parsed.path)
            if len(parts) >= 3 and parts[1] == "jobs":
                ats, board, identity = "rippling", {"board": parts[0], "company": parts[0]}, parts[2]
        if board is None:
            return {"status": "unsupported", "url": url}
        try:
            postings = self.fetch(ats, board)
        except HTTPError as error:
            return {"status": "closed" if error.code in (404, 410) else "unknown", "url": url, "reason": str(error)}
        for item in postings:
            if ats == "rippling":
                posting = urlparse(item["url"])
                route = _rippling_parts(posting.path)
                if (
                    item["id"].split(":")[-1] == identity
                    and posting.scheme == "https"
                    and posting.hostname == "ats.rippling.com"
                    and route == [board["board"], "jobs", identity]
                ):
                    return {"status": "open", "job": item, "url": url}
                continue
            if item["id"].split(":")[-1] == identity or item["url"].rstrip("/").endswith(identity):
                return {"status": "open", "job": item, "url": url}
        return {"status": "closed", "url": url}


def _confirmed_fact(value):
    if not isinstance(value, dict):
        return False
    provenance = value.get("provenance")
    return (
        value.get("status") == "confirmed"
        and value.get("valid") is not False
        and bool(value.get("confirmed_at"))
        and isinstance(provenance, dict)
        and provenance.get("kind") == "user_confirmation"
        and bool(provenance.get("source_id"))
    )


def _context_text(value):
    if not isinstance(value, str):
        return None
    value = " ".join(value.casefold().split())
    return None if value in ("", "unknown", "declined", "not_applicable") else value


def _job_date(value):
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def _scope_value_match(key, value, context):
    if key in ("country", "job_type", "employer"):
        required, actual = _context_text(value), _context_text(context.get(key))
        return required == actual if required is not None and actual is not None else None
    if key in ("effective_at", "valid_from", "valid_to"):
        required, actual = _job_date(value), _job_date(context.get("start_date"))
        if required is None or actual is None:
            return None
        if key == "effective_at":
            return actual == required
        if key == "valid_from":
            return actual >= required
        return actual <= required
    # Definitions/questions not understood by the fetcher are not global.
    return None


def _scope_match(scope, context):
    """False outweighs uncertainty; every restriction must match for True."""
    if not isinstance(scope, dict):
        return None
    matches = []
    for key, value in scope.items():
        if key == "scope":
            matches.append(_scope_match(value, context))
            continue
        if isinstance(value, dict):
            matches.append(_scope_match(value.get("scope", {}), context))
            value = value.get("value") if _confirmed_fact(value) else None
        matches.append(_scope_value_match(key, value, context))
    return False if False in matches else None if None in matches else True


def _sponsorship_needs(job, records):
    context = {
        "country": job.get("country"),
        "job_type": job.get("level") or job.get("job_type"),
        "employer": job.get("company") or job.get("employer"),
        "start_date": job.get("start_date"),
    }
    # These describe other facts, never sponsorship context or date substitutes.
    other_facts = {
        "id",
        "status",
        "current_status",
        "mechanism",
        "mechanism_status",
        "authorized_now",
        "authorized_at_start",
        "visa_expiry",
        "program_end",
        "work_auth_expiry",
        "sponsorship_now",
        "sponsorship_future",
    }
    resolved = {}
    for key in ("sponsorship_now", "sponsorship_future"):
        values, ambiguous = set(), False
        for record in records:
            evidence = record.get(key)
            if not _confirmed_fact(evidence) or type(evidence.get("value")) is not bool:
                continue
            record_scope = {name: value for name, value in record.items() if name not in other_facts}
            matches = (
                _scope_match(record_scope, context),
                _scope_match(evidence.get("scope", {}), context),
            )
            if False in matches:
                continue
            if None in matches:
                ambiguous = True
            else:
                values.add(evidence["value"])
        resolved[key] = next(iter(values)) if len(values) == 1 and not ambiguous else None
    return resolved


def evaluate_job(job, profile):
    targets, preferences = profile.get("targets", {}), profile.get("preferences", {})
    unknowns, reasons = [], []

    def compare(key, allowed):
        if not allowed:
            return
        if job.get(key) in (None, "", "unknown"):
            unknowns.append(key)
        elif job[key] not in allowed:
            reasons.append(key)

    compare("country", targets.get("countries", ["US"]))
    compare("level", targets.get("job_types", []))
    logistics = profile.get("logistics", {})
    for key in ("season", "year"):
        value = targets.get(key, logistics.get(key))
        if value:
            compare(key, [value])
    role = job.get("role", "").casefold()
    aliases = {
        "swe": ("software", "developer"),
        "backend": ("backend", "back-end", "software"),
        "frontend": ("frontend", "front-end"),
        "fullstack": ("full stack", "full-stack", "fullstack"),
        "ai": ("artificial intelligence", "ai"),
        "ml": ("machine learning", "ml"),
    }
    wanted = targets.get("roles", []) + targets.get("include_roles", [])
    if wanted and not any(
        any(token in role for token in aliases.get(str(w).lower(), (str(w).lower(),))) for w in wanted
    ):
        reasons.append("role")
    if any(str(word).casefold() in role for word in targets.get("exclude_roles", [])):
        reasons.append("excluded_role")
    company = job.get("company", "").casefold()
    if company in [str(item).casefold() for item in preferences.get("excluded_employers", [])]:
        reasons.append("excluded_employer")
    if preferences.get("include_big_tech") is False and job.get("employer_group") == "big_tech":
        reasons.append("big_tech")
    location = job.get("location", "").casefold()
    if any(str(item).casefold() in location for item in logistics.get("excluded_locations", [])):
        reasons.append("excluded_location")
    if job.get("eligibility", "unknown") == "unknown":
        unknowns.append("eligibility")
        if preferences.get("eligibility") == "explicit_only":
            reasons.append("explicit_eligibility_required")
    sponsorship = _sponsorship_needs(job, profile.get("authorization", []))
    unresolved = [key for key, value in sponsorship.items() if value is None]
    unknowns.extend(unresolved)
    restricted = job.get("sponsorship") == "not_supported" or bool(
        re.search(r"no sponsorship|cannot sponsor|do not sponsor", job.get("description", ""), re.I)
    )
    conflict = restricted and any(value is True for value in sponsorship.values())
    if conflict and preferences.get("no_sponsor_conflict") == "exclude":
        reasons.append("sponsorship_conflict")
    return {
        "decision": "exclude" if reasons else "review" if restricted and (conflict or unresolved) else "keep",
        "unknowns": unknowns,
        "reasons": reasons,
    }
