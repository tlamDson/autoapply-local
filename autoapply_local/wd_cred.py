"""Workday credentials kept in ``<runtime root>/secrets.json``.

One email and password per Workday tenant. The password is read only by the code
that types it into the sign-in form: it never appears in a repr, error message,
log line or printed value, and ``redact`` masks it in any text that might echo it.
The file is private runtime data, ignored by git and refused by the release audit.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .storage import _atomic_bytes, lock

SECRETS_FILE = "secrets.json"
SECTION = "workday"
MASK = "***"
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True)
class Credential:
    tenant: str
    email: str
    password: str

    def public(self):
        """Fields that are safe to print or store in a log."""
        return {"tenant": self.tenant, "email": self.email}

    def __repr__(self):
        return f"Credential(tenant={self.tenant!r}, email={self.email!r}, password={MASK!r})"

    __str__ = __repr__


def tenant_name(value):
    """Lowercase host of a Workday URL or bare host name."""
    text = value.strip() if isinstance(value, str) else ""
    host = (urlsplit(text).hostname or "") if "://" in text else text
    host = host.lower()
    if not _HOST.fullmatch(host):
        raise ValueError("A Workday tenant host or URL is required")
    return host


def _path(root):
    return Path(root) / SECRETS_FILE


def _read(root):
    path = _path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        section = data.get(SECTION, {})
        if not isinstance(section, dict) or any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("email"), str)
            or not isinstance(entry.get("password"), str)
            for entry in section.values()
        ):
            raise ValueError
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError("secrets.json is unreadable or malformed; fix or remove it") from None
    return data


def save(root, tenant, email, password):
    """Store or replace the credential of one tenant; other tenants are kept."""
    name = tenant_name(tenant)
    if not isinstance(email, str) or not _EMAIL.fullmatch(email.strip()):
        raise ValueError("A valid account email is required")
    if not isinstance(password, str) or not password.strip() or any(c in password for c in "\r\n\0"):
        raise ValueError("A single-line, non-empty password is required")
    path = _path(root)
    with lock(path.with_name(path.name + ".lock")):
        data = _read(root)
        data.setdefault(SECTION, {})[name] = {"email": email.strip(), "password": password}
        _atomic_bytes(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"), backup=False)
        if os.name != "nt":
            path.chmod(0o600)
    return name


def load(root, tenant):
    """Return the tenant's Credential or None."""
    name = tenant_name(tenant)
    entry = _read(root).get(SECTION, {}).get(name)
    return Credential(name, entry["email"], entry["password"]) if entry else None


def tenants(root):
    """Tenant names only, never a secret."""
    return sorted(_read(root).get(SECTION, {}))


def redact(root, value):
    """Mask every stored password inside a string, list or dict."""
    try:
        passwords = sorted(
            (entry["password"] for entry in _read(root).get(SECTION, {}).values()), key=len, reverse=True
        )
    except ValueError:
        passwords = []

    def clean(item):
        if isinstance(item, str):
            for password in passwords:
                item = item.replace(password, MASK)
            return item
        if isinstance(item, list):
            return [clean(entry) for entry in item]
        if isinstance(item, dict):
            return {key: clean(entry) for key, entry in item.items()}
        return item

    return clean(value)
