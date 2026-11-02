"""Conservative shared ATS identity for discovery, held tabs and live review."""

import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


def canonical_url(url):
    value = str(url)
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(c.isspace() for c in value)
        or "\\" in value
    ):
        raise ValueError("Malformed application URL")
    # Accessing port also rejects invalid/non-numeric ports.
    parsed.port
    path = parsed.path.rstrip("/")
    decoded = unquote(path)
    if any(part in (".", "..") for part in decoded.split("/")) or "//" in decoded:
        raise ValueError("Malformed application route")
    host = parsed.hostname.lower()
    if host.endswith(".myworkdayjobs.com") and "/job/" in path:
        path = re.sub(r"/(?:apply/applyManually|applyManually|apply)$", "", path)
    elif host == "ats.rippling.com" and re.fullmatch(r"/[^/]+/jobs/[^/]+/apply", path):
        path = path[: -len("/apply")]
    # Discard only explicit tracking keys; embedded token/gh_jid/tenant remain.
    query = sorted(
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "source" and not key.startswith("utm_")
    )
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urlencode(query), ""))
