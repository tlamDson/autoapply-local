"""Posting dates from public board payloads; an unreadable date is None, never a guess."""

import re
from datetime import date, datetime, timedelta

# Workday CXS list rows say "Posted Today", "Posted Yesterday", "Posted 3 Days Ago"
# or "Posted 30+ Days Ago" (read as exactly 30 days, the newest it can be).
_WORKDAY_DAYS = re.compile(r"^posted\s+(\d+)\+?\s+days?\s+ago$", re.I)
_ISO_KEYS = {
    "greenhouse": ("first_published", "updated_at"),
    "rippling": ("createdAt", "created_at", "publishedAt", "published_at"),
}


def _iso_day(value):
    if not isinstance(value, str) or not re.match(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            return None


def _workday(text, today):
    text = " ".join(str(text or "").split())
    if text.casefold() == "posted today":
        return today.isoformat()
    if text.casefold() == "posted yesterday":
        return (today - timedelta(days=1)).isoformat()
    match = _WORKDAY_DAYS.match(text)
    return (today - timedelta(days=int(match[1]))).isoformat() if match else None


def posted_on(ats, item, today):
    """The ISO day a posting went up, or None when the payload does not say."""
    if ats == "workday":
        return _workday(item.get("postedOn"), today)
    for key in _ISO_KEYS.get(ats, ()):
        day = _iso_day(item.get(key))
        if day:
            return day
    return None
