"""Workday account step (plan aa-12): sign in, create the account, verification code from the Gmail tab.

``FakeAccountTab`` plays the held Workday tab: each press moves it to the
next recorded page of tests/workday_flow.json. ``FakeGmail`` stands in for
the Gmail tab of the tool's own Chrome profile. Credentials come from the
runtime's secrets.json; the password and the code must never come back in a
result, an error or a file. Everyone and everything here is invented.
"""

import copy
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from test_support import ReleaseCase  # noqa: E402

FLOW = json.loads((RELEASE / "tests" / "workday_flow.json").read_text(encoding="utf-8"))
EMAIL = "alex.rivera@example.com"
PASSWORD = "synthetic-passphrase-42"
CODE = "482913"
JOB = {
    "id": "wd-1",
    "ats": "workday",
    "url": "https://exampleco.wd5.myworkdayjobs.com/en-US/careers/job/Software-Engineer-Intern_R-0001",
    "company": "Example Co",
}
STEP_CALL = re.compile(r"\nreturn await globalThis\.AutoApplyWorkdaySteps\.(\w+)\(document(?:, (.*))?\);\n\}$", re.S)


def page(name):
    if name == "form":
        return copy.deepcopy(FLOW["steps"][0]["where"])
    return copy.deepcopy(FLOW["pages"][name])


class FakeAccountTab:
    """Pages move on press; ``moves`` maps an action to the pages it leads to, one per press."""

    def __init__(self, start, moves):
        self.where = page(start)
        self.moves = {key: list(value) for key, value in moves.items()}
        self.log, self.secrets_seen = [], []

    def __call__(self, script):
        found = STEP_CALL.search(script)
        if not found:
            raise AssertionError("the account step only runs Workday step scripts")
        name, args = found.group(1), json.loads("[" + (found.group(2) or "") + "]")
        if name == "where":
            self.log.append(("where", self.where["page"]))
            return copy.deepcopy(self.where)
        if name == "press":
            self.log.append(("press", args[0]))
            target = self.moves[args[0]].pop(0)
            self.where = page(target)
            return {"pressed": True, "moved": True, "where": copy.deepcopy(self.where)}
        if name == "credentials":
            values = args[0]
            self.secrets_seen.append(values["password"])
            self.log.append(("credentials", values["email"], bool(values.get("confirm"))))
            ids = ["email", "password"] + (["verifyPassword", "createAccountCheckbox"] if values.get("confirm") else [])
            return {"typed": ids, "failed": []}
        if name == "code":
            self.log.append(("code", args[0]))
            return {"typed": True}
        raise AssertionError(f"unexpected call {name}")

    def actions(self):
        return [entry for entry in self.log if entry[0] != "where"]


class FakeGmail:
    """Codes visible in Gmail search, one list per call (the last one repeats)."""

    def __init__(self, *views):
        self.views = [list(view) for view in views] or [[]]
        self.calls = []

    def verification_codes(self, company):
        self.calls.append(company)
        return list(self.views.pop(0) if len(self.views) > 1 else self.views[0])


class AccountCase(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.wd_cred = self.module("wd_cred")
        self.account_module = self.module("workday_account")
        self.sleep = Mock()

    def save(self):
        self.wd_cred.save(self.root, JOB["url"], EMAIL, PASSWORD)

    def account(self, tab, gmail=None, **options):
        options.setdefault("code_wait", 30)
        options.setdefault("code_poll", 10)
        return self.account_module.WorkdayAccount(self.root, tab, gmail, sleep=self.sleep, **options)

    def assert_private(self, result, tab=None):
        text = json.dumps(result)
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(CODE, text)
        written = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*") if path.is_file())
        self.assertEqual([name for name in written if not name.startswith("secrets.json")], [])


class SignInTests(AccountCase):
    def test_posting_apply_manually_and_sign_in_reach_the_form(self):
        self.save()
        tab = FakeAccountTab("job", {"apply": ["start"], "apply_manually": ["sign_in"], "sign_in": ["form"]})
        result = self.account(tab).ensure_signed_in(JOB)
        self.assertEqual(result["status"], "signed_in")
        self.assertEqual(result["page"], "form")
        self.assertEqual(
            tab.actions(),
            [("press", "apply"), ("press", "apply_manually"), ("credentials", EMAIL, False), ("press", "sign_in")],
        )
        self.assertEqual(tab.secrets_seen, [PASSWORD])
        self.assertEqual(result["moves"], ["apply", "apply_manually", "sign_in"])
        self.assert_private(result)

    def test_an_already_signed_in_tab_needs_no_action(self):
        tab = FakeAccountTab("form", {})
        result = self.account(tab).ensure_signed_in(JOB)
        self.assertEqual((result["status"], tab.actions()), ("signed_in", []))

    def test_the_sign_in_with_email_option_is_opened_first(self):
        self.save()
        tab = FakeAccountTab("sign_in_email_only", {"sign_in_with_email": ["sign_in"], "sign_in": ["form"]})
        result = self.account(tab).ensure_signed_in(JOB)
        self.assertEqual(result["status"], "signed_in")
        self.assertEqual(tab.actions()[0], ("press", "sign_in_with_email"))

    def test_no_stored_credential_holds_the_tab_without_typing(self):
        tab = FakeAccountTab("sign_in", {})
        result = self.account(tab).ensure_signed_in(JOB)
        self.assertEqual((result["status"], result["reason"]), ("blocked", "no_workday_credential"))
        self.assertEqual(tab.actions(), [])

    def test_credentials_of_another_tenant_are_not_used(self):
        self.wd_cred.save(self.root, "other.wd1.myworkdayjobs.com", EMAIL, PASSWORD)
        tab = FakeAccountTab("sign_in", {})
        result = self.account(tab).ensure_signed_in(JOB)
        self.assertEqual(result["reason"], "no_workday_credential")

    def test_wrong_sign_in_then_existing_account_holds_with_redacted_errors(self):
        self.save()
        tab = FakeAccountTab(
            "sign_in",
            {"sign_in": ["sign_in_failed"], "create_account_link": ["create_account"], "create_account": ["create_account_failed"]},
        )
        result = self.account(tab).ensure_signed_in(JOB)
        self.assertEqual((result["status"], result["reason"]), ("blocked", "create_account_failed"))
        self.assertEqual(result["errors"], ["Error: An account already exists for this email"])
        self.assert_private(result)

    def test_captcha_already_applied_and_link_only_verification_hold_the_tab(self):
        self.save()
        for start, status, reason in (
            ("captcha", "blocked", "captcha"),
            ("verify_email", "blocked", "email_verification_required"),
            ("unknown", "blocked", "page_not_recognized"),
        ):
            with self.subTest(start=start):
                result = self.account(FakeAccountTab(start, {})).ensure_signed_in(JOB)
                self.assertEqual((result["status"], result["reason"]), (status, reason))
        result = self.account(FakeAccountTab("applied", {})).ensure_signed_in(JOB)
        self.assertEqual(result["status"], "already_applied")

    def test_a_refused_press_holds_the_tab_with_its_reason(self):
        self.save()

        def tab(script):
            name = STEP_CALL.search(script).group(1)
            if name == "where":
                return page("job")
            return {"pressed": False, "reason": "submit_control"}

        result = self.account(tab).ensure_signed_in(JOB)
        self.assertEqual((result["status"], result["reason"]), ("blocked", "submit_control"))

    def test_a_page_that_never_settles_stops_after_the_move_limit(self):
        self.save()
        tab = FakeAccountTab("job", {"apply": ["job"] * 50})
        result = self.account(tab, max_moves=5).ensure_signed_in(JOB)
        self.assertEqual(result["reason"], "too_many_moves")
        self.assertEqual(len(tab.actions()), 5)

    def test_a_browser_error_is_reported_with_the_password_masked(self):
        self.save()
        from autoapply_local.cdp import CDPError

        def tab(script):
            name = STEP_CALL.search(script).group(1)
            if name == "where":
                return page("sign_in")
            raise CDPError(f"TypeError while typing {PASSWORD}")

        result = self.account(tab).ensure_signed_in(JOB)
        self.assertEqual((result["status"], result["reason"]), ("blocked", "browser_error"))
        self.assertIn("***", result["errors"][0])
        self.assert_private(result)


class CreateAccountTests(AccountCase):
    def flow(self):
        return FakeAccountTab(
            "sign_in",
            {
                "sign_in": ["sign_in_failed"],
                "create_account_link": ["create_account"],
                "create_account": ["verify_code"],
                "verify": ["form"],
            },
        )

    def test_failed_sign_in_creates_the_account_and_enters_the_fresh_gmail_code(self):
        self.save()
        tab = self.flow()
        gmail = FakeGmail(["111111"], ["111111"], ["111111"], [CODE, "111111"])
        result = self.account(tab, gmail).ensure_signed_in(JOB)
        self.assertEqual(result["status"], "signed_in")
        self.assertEqual(
            tab.actions(),
            [
                ("credentials", EMAIL, False),
                ("press", "sign_in"),
                ("press", "create_account_link"),
                ("credentials", EMAIL, True),
                ("press", "create_account"),
                ("code", CODE),
                ("press", "verify"),
            ],
        )
        self.assertEqual(set(gmail.calls), {"Example Co"})
        self.sleep.assert_called_with(10)
        self.assertEqual(result["moves"], ["sign_in", "create_account_link", "create_account", "verify"])
        self.assert_private(result)

    def test_a_create_account_page_reached_first_creates_the_account(self):
        self.save()
        tab = FakeAccountTab("create_account", {"create_account": ["sign_in"], "sign_in": ["form"]})
        result = self.account(tab, FakeGmail()).ensure_signed_in(JOB)
        self.assertEqual(result["status"], "signed_in")
        self.assertEqual(
            tab.actions(),
            [("credentials", EMAIL, True), ("press", "create_account"), ("credentials", EMAIL, False), ("press", "sign_in")],
        )

    def test_sign_in_is_tried_once_more_after_the_account_is_created(self):
        self.save()
        tab = FakeAccountTab(
            "sign_in",
            {"sign_in": ["sign_in_failed", "form"], "create_account_link": ["create_account"], "create_account": ["sign_in"]},
        )
        result = self.account(tab, FakeGmail()).ensure_signed_in(JOB)
        self.assertEqual(result["status"], "signed_in")
        self.assertEqual([entry for entry in tab.actions() if entry == ("press", "sign_in")], [("press", "sign_in")] * 2)

    def test_sign_in_that_keeps_failing_after_creation_holds_the_tab(self):
        self.save()
        tab = FakeAccountTab(
            "sign_in",
            {
                "sign_in": ["sign_in_failed", "sign_in_failed"],
                "create_account_link": ["create_account"],
                "create_account": ["sign_in"],
            },
        )
        result = self.account(tab, FakeGmail()).ensure_signed_in(JOB)
        self.assertEqual((result["status"], result["reason"]), ("blocked", "sign_in_failed"))
        self.assertEqual(result["errors"], ["Error: Wrong email or password"])

    def test_no_fresh_code_within_the_wait_holds_the_tab(self):
        self.save()
        tab = self.flow()
        gmail = FakeGmail(["111111"])
        result = self.account(tab, gmail, code_wait=30, code_poll=10).ensure_signed_in(JOB)
        self.assertEqual((result["status"], result["reason"]), ("blocked", "verification_code_not_found"))
        self.assertNotIn("code", [entry[0] for entry in tab.actions()])
        self.assertEqual(self.sleep.call_count, 3)

    def test_without_the_gmail_tab_a_code_page_holds_the_tab(self):
        self.save()
        tab = FakeAccountTab("verify_code", {})
        result = self.account(tab, None).ensure_signed_in(JOB)
        self.assertEqual((result["status"], result["reason"]), ("blocked", "verification_code_not_found"))
        self.sleep.assert_not_called()

    def test_a_rejected_code_is_not_retried(self):
        self.save()
        tab = FakeAccountTab("verify_code", {"verify": ["verify_code"]})
        result = self.account(tab, FakeGmail([CODE])).ensure_signed_in(JOB)
        self.assertEqual((result["status"], result["reason"]), ("blocked", "verification_code_rejected"))
        self.assertEqual([entry[0] for entry in tab.actions()].count("code"), 1)
        self.assert_private(result)


class CDPBindingTests(AccountCase):
    def test_for_tab_evaluates_step_scripts_in_the_held_tab_over_loopback_cdp(self):
        cdp = Mock()
        cdp.pages.return_value = [
            {"id": "G1", "url": "https://mail.google.com/mail/u/0/", "ws": "ws://127.0.0.1:9333/devtools/page/G1"},
            {"id": "W1", "url": JOB["url"], "ws": "ws://127.0.0.1:9333/devtools/page/W1"},
        ]
        cdp.evaluate.return_value = page("form")
        account = self.account_module.WorkdayAccount.for_tab(self.root, "http://127.0.0.1:9333", "W1", cdp=cdp)
        self.assertEqual(account.ensure_signed_in(JOB)["status"], "signed_in")
        (ws, expression), _ = cdp.evaluate.call_args
        self.assertEqual(ws, "ws://127.0.0.1:9333/devtools/page/W1")
        self.assertTrue(expression.startswith("(async () => {"))
        self.assertTrue(expression.endswith("})()"))
        self.assertIn("AutoApplyWorkdaySteps.where(document)", expression)

    def test_for_tab_refuses_a_remote_endpoint_and_a_closed_tab(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            self.account_module.WorkdayAccount.for_tab(self.root, "http://192.0.2.1:9333", "W1", cdp=Mock())
        cdp = Mock()
        cdp.pages.return_value = []
        account = self.account_module.WorkdayAccount.for_tab(self.root, "http://127.0.0.1:9333", "W1", cdp=cdp)
        result = account.ensure_signed_in(JOB)
        self.assertEqual((result["status"], result["reason"]), ("blocked", "tab_closed"))


class VerificationCodeTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.gmail_tab = self.module("gmail_tab")

    def row(self, subject, snippet):
        return {"thread": "t1", "from": "Example Co", "email": "no-reply@example.invalid", "subject": subject,
                "snippet": snippet, "date": "", "unread": True}

    def test_the_code_is_read_from_the_subject_or_snippet(self):
        cases = [
            ("Verify your candidate account", f"Your verification code is {CODE}. It expires in 10 minutes.", CODE),
            ("Your one-time passcode", "Use code: A7K9Q2 to finish signing in", "A7K9Q2"),
            (f"{CODE} is your Example Co verification code", "", CODE),
            ("Verify your candidate account", f"Enter {CODE} on the sign-in page", CODE),
            ("Verify your candidate account", "Your code is below. Thank you for applying in 2026.", None),
            ("Verify your email", "Click the link to verify your account", None),
            ("Call us", "Reach us at 555 0100 ext 12", None),
        ]
        for subject, snippet, expected in cases:
            with self.subTest(subject=subject, snippet=snippet):
                self.assertEqual(self.gmail_tab.verification_code(self.row(subject, snippet)), expected)

    def test_gmail_tab_lists_codes_newest_first_from_the_verify_search(self):
        tab = self.gmail_tab.GmailTab("http://127.0.0.1:9333", cdp=Mock())
        rows = [
            self.row("Verify your candidate account", f"Your verification code is {CODE}"),
            self.row("Verify your email", "Click the link"),
            self.row("Verify your candidate account", "Your verification code is 111111"),
        ]
        with patch.object(tab, "search", return_value={"rows": rows}) as search:
            self.assertEqual(tab.verification_codes("Example Co"), [CODE, "111111"])
        search.assert_called_once_with(self.gmail_tab.search_query("verify", "Example Co", 1))


if __name__ == "__main__":
    unittest.main()
