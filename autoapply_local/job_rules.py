"""Offline text rules for job descriptions and titles; pure functions, no I/O.

The description patterns are the generic restriction rules of the earlier
pipeline's prefilter: they flag a job, they never establish a candidate fact.
"""

import re


JD_RULES = ("no_sponsorship", "us_citizen", "clearance", "itar")

# Form-style lines that state the restriction does NOT apply.
NEUTRAL = re.compile(
    r"(U\.?S\.? )?citizenship required:\s*no\b|clearance (level )?must (currently possess|be able to obtain):\s*none"
    r"|security clearance (type|status):\s*(none|not required)[/a-z ]*|clearance required for start:\s*no\b"
    r"|minimum clearance required to start:\s*none|public trust/other required:\s*none"
    r"|except for U\.?S\.? citizens[^.]{0,200}",
    re.I,
)
JD_PATTERNS = {
    "no_sponsorship": re.compile(
        r"\b(no|not|unable to|will not|cannot) (provide |offer )?(visa )?sponsor(ship)?\b|\bdo(es)? not sponsor\b", re.I),
    "us_citizen": re.compile(
        r"U\.?S\.? citizen(ship)? (is )?(required|only)|must be (a )?U\.?S\.? citizen|requires? (U\.?S\.? )?citizenship|"
        r"citizens? (or|and) (lawful )?permanent residents?|green card holders?|permanent residen(t|cy) (is )?required|"
        r"citizens?\b[^.]{0,50}\b(?:lawful )?permanent resident|permanent resident[^.]{0,50}\bcitizen", re.I),
    "clearance": re.compile(
        r"(active|current|obtain|eligib\w* (for|to obtain)|requires?|maintain)[^.]{0,40}(security )?clearance|"
        r"TS/SCI|secret clearance|clearance required to start:\s*(?:public trust|secret|top secret|ts\b|confidential)|"
        r"security clearance (?:is )?(?:required|mandatory)", re.I),
    # A leading word boundary keeps "curious person" from reading as "U.S. person".
    "itar": re.compile(r"\bITAR\b|\bU\.?S\.? persons?\b", re.I),
}

# Title terms with known spellings; any other term matches as a whole word.
TERM_PATTERNS = {
    "intern": r"intern(?:ship)?s?",
    "co-op": r"co-?ops?",
    "sr.": r"sr\.?",
    "full-time": r"full[- ]?time",
    "new grad": r"new[- ]grad(?:uate)?s?",
}
ROLE_ALIASES = {
    "swe": ("software", "developer", "swe", "sde", "programmer"),
    "backend": ("backend", "back-end", "back end"),
    "frontend": ("frontend", "front-end", "front end"),
    "fullstack": ("full stack", "full-stack", "fullstack"),
    "mobile": ("mobile", "ios", "android"),
    "platform": ("platform",),
    "sre": ("site reliability", "sre"),
    "devops": ("devops", "dev ops"),
    "data": ("data",),
    "ai": ("artificial intelligence", "ai"),
    "ml": ("machine learning", "ml"),
    "research": ("research",),
    "qa": ("qa", "quality assurance", "test"),
    "sdet": ("sdet", "in test"),
    "security": ("security",),
}
SEASONS = ("spring", "summer", "fall", "autumn", "winter")
SEASON_RE = re.compile(r"\b(spring|summer|fall|autumn|winter)\b", re.I)
YEAR_RE = re.compile(r"\b(20[2-4]\d)\b")


def _normalize(text):
    return str(text or "").replace("’", "'").replace("‘", "'").replace(" ", " ")


def jd_flags(text):
    """Return the restriction rule IDs a job description states, in JD_RULES order."""
    text = NEUTRAL.sub(" ", _normalize(text))
    return [rule for rule in JD_RULES if JD_PATTERNS[rule].search(text)]


def _term_regex(term):
    term = str(term).strip().casefold()
    body = TERM_PATTERNS.get(term, re.escape(term) + r"s?")
    return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])", re.I)


def has_term(title, term):
    return bool(str(term).strip()) and bool(_term_regex(term).search(_normalize(title)))


def role_matches(title, role):
    tokens = ROLE_ALIASES.get(str(role).strip().casefold(), (str(role).strip().casefold(),))
    return any(has_term(title, token) for token in tokens if token)


def title_seasons(title):
    return {("fall" if s.casefold() == "autumn" else s.casefold()) for s in SEASON_RE.findall(_normalize(title))}


def title_years(title):
    return {int(year) for year in YEAR_RE.findall(_normalize(title))}
