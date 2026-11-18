"""The Gmail tab of the tool's own Chrome profile (``chrome/lane-1`` in the runtime root).

The user signs in to Gmail once in that clean profile. Afterwards the tool
only searches and reads result rows through the read-only ``gmail.cjs``
adapter: confirmation search for one company, and the history scan that pages
through six months of confirmations, plus the Workday account step's search
for a verification code, read from the subject and snippet of the result
rows. It never opens, sends, drafts, archives or labels a message, and a code
is only returned to the caller, never stored.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from . import cdp as cdp_module
from .mail_history import HISTORY_DAYS, classify, parse

GMAIL_URL = "https://mail.google.com/mail/u/0/"
MAX_PAGES = 20
_VERIFY_TERMS = ('"candidate account"', "subject:verify", "subject:verification", "subject:activate", '"security code"')
_CONFIRM_TERMS = ("subject:application", "subject:applying", "subject:applied", 'subject:"thank you"',
                  '"received your application"', '"got your application"')


_CODE_AFTER_WORD = re.compile(r"\b(?:code|passcode|pin)\b(?=(?:\s*(?:is|:|-))?\s*([A-Z0-9]{4,8})\b)", re.I)
_SIX_DIGITS = re.compile(r"(?<![\d-])\d{6}(?![\d-])")


def verification_code(row):
    """The one-time code in a result row's subject or snippet, or None."""
    text = " ".join(str(row.get(key) or "") for key in ("subject", "snippet"))
    for match in _CODE_AFTER_WORD.finditer(text):
        if any(ch.isdigit() for ch in match.group(1)):
            return match.group(1)
    found = _SIX_DIGITS.search(text)
    return found.group(0) if found else None


def search_query(kind="confirm", company="", days=2):
    """Gmail search over every folder (confirmations often skip the inbox) for one kind of mail."""
    who = []
    if company:
        who = ['"' + company.replace('"', "") + '"',
               "from:" + "".join(ch for ch in company.lower() if ch.isalnum() or ch in ".-")]
    if kind == "verify":
        return f"in:anywhere newer_than:{days}d ({' OR '.join((*_VERIFY_TERMS, *who))})"
    scope = f" ({' OR '.join(who)})" if who else ""
    return f"in:anywhere newer_than:{days}d{scope} ({' OR '.join(_CONFIRM_TERMS)})"


def _adapter():
    return Path(__file__).with_name("adapters").joinpath("gmail.cjs").read_text(encoding="utf-8")


def _company_matches(company, row):
    wanted = "".join(ch for ch in company.casefold() if ch.isalnum())
    found = "".join(ch for ch in parse(row)["company"].casefold() if ch.isalnum())
    return bool(wanted and found) and (wanted in found or found in wanted)


class GmailTab:
    def __init__(self, debug_url, cdp=cdp_module):
        parsed = urlsplit(debug_url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or not parsed.port:
            raise ValueError("The Gmail tab needs the tool's loopback Chrome debugging URL")
        self.debug_url, self.cdp = debug_url.rstrip("/"), cdp

    def page(self):
        """The existing Gmail (or Google sign-in) tab, or a new Gmail tab."""
        for page in self.cdp.pages(self.debug_url):
            host = urlsplit(page["url"]).hostname or ""
            if host in ("mail.google.com", "accounts.google.com"):
                return page
        return self.cdp.open_page(self.debug_url, GMAIL_URL)

    def signed_in(self):
        page = self.page()
        if not page["url"].startswith("https://mail.google.com/mail/"):
            return False
        return self.cdp.evaluate(page["ws"], "!!document.querySelector('div[role=main]')", timeout=10) is True

    def run(self, call, timeout=60):
        expression = "(async () => {\n" + _adapter() + "\nreturn await globalThis.AutoApplyGmail." + call + ";\n})()"
        return self.cdp.evaluate(self.page()["ws"], expression, timeout=timeout)

    def search(self, q, page=1):
        options = json.dumps({"page": page})
        return self.run(f"search(window, {json.dumps(q, ensure_ascii=False)}, {options})")

    def history(self, days=HISTORY_DAYS, max_pages=MAX_PAGES, progress=None):
        """Every confirmation-like row of the last ``days`` days, paging until Gmail runs out."""
        q = search_query("confirm", days=days)
        rows, seen, previous = [], set(), None
        for number in range(1, max_pages + 1):
            if progress:
                progress(number)
            result = self.search(q, page=number)
            page_rows = result.get("rows") or []
            marker = [(row.get("thread"), row.get("subject")) for row in page_rows]
            if result.get("empty") or not page_rows or marker == previous:
                break
            previous = marker
            for row in page_rows:
                key = parse(row)["key"]
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
        return rows

    def verification_codes(self, company, days=1):
        """Codes in the newest verification mails (newest first); nothing is kept."""
        codes = []
        for row in self.search(search_query("verify", company, days)).get("rows") or []:
            code = verification_code(row)
            if code and code not in codes:
                codes.append(code)
        return codes

    def find_confirmation(self, company, days=2):
        """The newest confirmation from ``company`` in the last ``days`` days, or None."""
        for row in self.search(search_query("confirm", company, days)).get("rows") or []:
            if classify(row) == "confirm" and _company_matches(company, row):
                return row
        return None


class GmailSession:
    """Setup's view: open the clean profile with a Gmail tab, check sign-in, scan history."""

    def __init__(self, root, memory_mb=None, launcher=None, cdp=cdp_module):
        self.root, self.memory_mb, self.cdp = Path(root).resolve(), memory_mb, cdp
        if launcher is None:
            from .browser import launch_chrome as launcher
        self.launcher, self.tab = launcher, None

    def open(self):
        lane = self.launcher(self.root, lanes=1, available_memory_mb=self.memory_mb)[0]
        self.tab = GmailTab(lane["debug_url"], cdp=self.cdp)
        self.tab.page()
        return lane["profile_dir"]

    def signed_in(self):
        return self.tab.signed_in()

    def history(self, progress=None):
        return self.tab.history(progress=progress)
