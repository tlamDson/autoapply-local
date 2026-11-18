"""Workday account step: get a held tab from the posting to the first form step.

From the posting it presses Apply and Apply Manually, signs in with the
tenant's credential from ``secrets.json`` (``wd_cred``), creates the account
when sign-in fails, and enters the verification code read from the Gmail tab
of the tool's own Chrome profile. The credential and the code are typed only
on the page just checked and only when the tab's host is the job's tenant.
The tenant's account terms are never accepted for the user: an unticked
terms box holds the filled create-account form. Scripts run over loopback CDP straight in
the held tab, so the password never passes through an engine. A result never
carries the password or the code; error text is masked with ``wd_cred.redact``.
Anything else (CAPTCHA, a link-only verification mail, an unknown page, a
refused press) holds the tab for the user.
"""

import time
from urllib.parse import urlsplit

from . import cdp as cdp_module
from . import wd_cred, workday_steps
from .cdp import CDPError

SIGNED_IN = ("form", "review")
WRITES = ("sign_in", "create_account", "verify_code")


class TabClosed(ValueError):
    """The held Workday tab is no longer open."""


class _Hold(Exception):
    def __init__(self, reason, errors=()):
        super().__init__(reason)
        self.reason, self.errors = reason, list(errors)


class WorkdayAccount:
    def __init__(self, root, evaluate, gmail=None, *, sleep=time.sleep, code_wait=120, code_poll=10, max_moves=12):
        self.root, self.evaluate, self.gmail = root, evaluate, gmail
        self.sleep, self.code_wait, self.code_poll, self.max_moves = sleep, code_wait, code_poll, max_moves

    @classmethod
    def for_tab(cls, root, debug_url, tab_id, gmail=None, cdp=cdp_module, **options):
        """Bind the account step to one held tab of the tool's own Chrome."""
        parsed = urlsplit(debug_url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or not parsed.port:
            raise ValueError("The Workday tab needs the tool's loopback Chrome debugging URL")

        def evaluate(script):
            pages = [page for page in cdp.pages(debug_url) if str(page.get("id")) == str(tab_id)]
            if len(pages) != 1:
                raise TabClosed("The held Workday tab is closed")
            return cdp.evaluate(pages[0]["ws"], "(" + script + ")()", timeout=60)

        return cls(root, evaluate, gmail, **options)

    def ensure_signed_in(self, job):
        """Move the tab to a form step; returns status signed_in, already_applied or blocked."""
        self._moves, self._stale = [], set()
        self._attempts = {"sign_in": 0, "create_account": 0, "verify": 0}
        page = None
        try:
            self._tenant = wd_cred.tenant_name(job.get("url", ""))
        except ValueError:
            return self._result("blocked", page, "tenant_mismatch")
        try:
            for _ in range(self.max_moves + 1):
                where = self._where()
                page = where.get("page")
                if page in SIGNED_IN:
                    return self._result("signed_in", page)
                if page == "applied":
                    return self._result("already_applied", page)
                if len(self._moves) >= self.max_moves:
                    raise _Hold("too_many_moves")
                self._step(page, where, job)
            raise _Hold("too_many_moves")
        except _Hold as hold:
            return self._result("blocked", page, hold.reason, hold.errors)
        except TabClosed:
            return self._result("blocked", page, "tab_closed")
        except (CDPError, OSError) as error:
            return self._result("blocked", page, "browser_error", [str(error)])

    def _step(self, page, where, job):
        if page in WRITES and where.get("host") != self._tenant:
            # The credential and code belong to the job's tenant: never type them on another host.
            raise _Hold("tenant_mismatch")
        if page == "job":
            return self._press("apply")
        if page == "start":
            return self._press("apply_manually")
        if page == "sign_in":
            return self._sign_in(where, job)
        if page == "create_account":
            return self._create(where, job)
        if page == "verify_code":
            return self._verify(job)
        if page == "captcha":
            raise _Hold("captcha")
        if page == "verify_email":
            raise _Hold("email_verification_required")
        raise _Hold("page_not_recognized")

    def _sign_in(self, where, job):
        if where.get("sign_in_with_email"):
            return self._press("sign_in_with_email")
        allowed = 2 if self._attempts["create_account"] else 1
        if self._attempts["sign_in"] >= allowed:
            if not self._attempts["create_account"] and where.get("create_link"):
                return self._press("create_account_link")
            raise _Hold("sign_in_failed", where.get("errors") or [])
        credential = self._credential(job)
        self._attempts["sign_in"] += 1
        self._snapshot(job)
        self._type(credential, "sign_in")
        self._press("sign_in")

    def _create(self, where, job):
        if self._attempts["create_account"]:
            raise _Hold("create_account_failed", where.get("errors") or [])
        credential = self._credential(job)
        self._attempts["create_account"] += 1
        self._snapshot(job)
        typed = self._type(credential, "create_account")
        if typed.get("terms") not in ("absent", "checked"):
            # The tenant's terms are the user's to accept: hold the filled form for them.
            raise _Hold("account_terms_consent_required")
        self._press("create_account")

    def _verify(self, job):
        if self._attempts["verify"]:
            raise _Hold("verification_code_rejected")
        code = self._fresh_code(job)
        if code is None:
            raise _Hold("verification_code_not_found")
        self._attempts["verify"] += 1
        typed = self.evaluate(workday_steps.script("code", {"code": code, "host": self._tenant}))
        if not (isinstance(typed, dict) and typed.get("typed")):
            raise _Hold("verification_field_not_typed")
        self._press("verify")

    def _credential(self, job):
        credential = wd_cred.load(self.root, job["url"])
        if credential is None:
            raise _Hold("no_workday_credential")
        return credential

    def _type(self, credential, page):
        values = {
            "email": credential.email,
            "password": credential.password,
            "confirm": page == "create_account",
            "page": page,
            "host": self._tenant,
        }
        result = self.evaluate(workday_steps.script("credentials", values))
        if not isinstance(result, dict) or result.get("refused") or result.get("failed") or not result.get("typed"):
            raise _Hold("credential_fields_not_typed")
        return result

    def _press(self, action):
        result = self.evaluate(workday_steps.press_script(action))
        self._moves.append(action)
        if not isinstance(result, dict) or not result.get("pressed"):
            raise _Hold((result.get("reason") if isinstance(result, dict) else None) or "press_failed")

    def _where(self):
        where = self.evaluate(workday_steps.script("where"))
        if not isinstance(where, dict):
            raise _Hold("page_not_recognized")
        return where

    def _codes(self, job):
        return list(self.gmail.verification_codes(job.get("company") or "")) if self.gmail is not None else []

    def _snapshot(self, job):
        # Codes already in Gmail before this press belong to an earlier attempt.
        self._stale |= set(self._codes(job))

    def _fresh_code(self, job):
        if self.gmail is None:
            return None
        polls = max(1, self.code_wait // max(1, self.code_poll) + 1)
        for attempt in range(polls):
            for code in self._codes(job):
                if code not in self._stale:
                    return code
            if attempt < polls - 1:
                self.sleep(self.code_poll)
        return None

    def _result(self, status, page, reason=None, errors=()):
        result = {"status": status, "page": page, "moves": list(self._moves)}
        if reason:
            result["reason"] = reason
        if errors:
            result["errors"] = [str(item)[:200] for item in wd_cred.redact(self.root, list(errors))]
        return result
