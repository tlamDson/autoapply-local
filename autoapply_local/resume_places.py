"""Tell a resume's place column (a location) from a technology list.

A location is a place: Remote/Hybrid/On-site, or "City, Region[, Country]"
whose last part is a known state, province or country. Anything else is not a
location, so a tech stack in the location slot never becomes one.
"""

import re

REGION_CODES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK "
    "OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR AB BC MB NB NL NS NT NU ON PE QC SK YT US USA UK UAE".split())
REGION_NAMES = {name.casefold() for name in (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida",
    "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska",
    "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
    "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
    "Ontario", "Quebec", "British Columbia", "Alberta",
    "United States", "United States of America", "Canada", "Mexico", "Brazil", "United Kingdom", "England",
    "Ireland", "France", "Germany", "Netherlands", "Spain", "Italy", "Switzerland", "Sweden", "Poland", "India",
    "China", "Hong Kong", "Taiwan", "Japan", "South Korea", "Korea", "Singapore", "Vietnam", "Viet Nam",
    "Thailand", "Malaysia", "Indonesia", "Philippines", "Australia", "New Zealand", "Israel", "Nigeria",
    "Kenya", "Egypt", "Pakistan", "Bangladesh", "Turkey", "United Arab Emirates")}
WORK_MODE = re.compile(r"^(?:remote|hybrid|on-?site)\b", re.I)
PART = re.compile(r"[A-Za-z][A-Za-z.'-]*(?: [A-Za-z][A-Za-z.'-]*){0,4}")


def _region(part):
    return part in REGION_CODES or part.casefold() in REGION_NAMES


def is_place(text):
    """True when text names a place a job was done (never a list of technologies)."""
    text = (text or "").strip()
    parts = [part.strip() for part in text.split(",")]
    if WORK_MODE.match(text):
        return len(parts) <= 3
    if len(parts) == 1:
        return text.casefold() in REGION_NAMES
    return len(parts) <= 3 and all(PART.fullmatch(part) for part in parts) and _region(parts[-1])


def is_stack(text):
    """True for a comma-separated list of three or more short items that is not a place."""
    parts = [part.strip() for part in (text or "").split(",")]
    return len(parts) >= 3 and all(part and len(part.split()) <= 3 for part in parts) and not is_place(text)
