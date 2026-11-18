"""Terminal setup steps 7-8: open the clean Chrome profile, wait for Gmail sign-in, scan six months of confirmations."""

import io

from test_terminal_setup import READY, Base, Script


def mail(thread, name, email, subject, snippet=""):
    return {"thread": thread, "from": name, "email": email, "subject": subject, "snippet": snippet,
            "date": "Tue, Nov 10, 2026, 9:14 AM", "unread": False}


ROWS = [
    mail("t1", "Northwind Labs Recruiting", "no-reply@northwind.example", "Thank you for applying to Northwind Labs"),
    mail("t2", "Contoso Careers", "careers@contoso.example", "Verify your candidate account", "security code"),
]


class FakeGmail:
    def __init__(self, signed=(True,), rows=ROWS, error=None):
        self.signed, self.rows, self.error = list(signed), rows, error
        self.calls = []

    def open(self):
        self.calls.append("open")
        if self.error:
            raise self.error
        return "/runtime/chrome/lane-1"

    def signed_in(self):
        self.calls.append("signed_in")
        return self.signed.pop(0) if len(self.signed) > 1 else self.signed[0]

    def history(self, progress=None):
        self.calls.append("history")
        if progress:
            progress(1)
        return list(self.rows)


BEFORE = ["1", "1", "", None, "SWE", "n", ""]


class GmailStep(Base):
    def setup_run(self, gmail_answers, gmail, before=None):
        before = BEFORE if before is None else before
        answers = [str(self.pdf("cv.pdf")) if item is None else item for item in before] + gmail_answers
        self.script, self.out = Script(answers), io.StringIO()
        return self.ts.run(self.runtime_root, ask=self.script, out=self.out, probe=self.probe((READY,)),
                           launch=self.launch, memory_mb=4096, gmail=gmail)

    def tracker(self):
        return self.module("storage").Store(self.runtime_root).tracker_rows()

    def test_signed_in_profile_scans_history_into_the_tracker(self):
        gmail = FakeGmail(signed=(False, True))
        result = self.setup_run(["", ""], gmail)
        self.assertEqual(gmail.calls, ["open", "signed_in", "signed_in", "history"])
        self.assertEqual([(row["company"], row["status"]) for row in self.tracker()], [("Northwind Labs", "submitted")])
        state = self.config()["gmail"]
        self.assertEqual(state["status"], "signed_in")
        self.assertEqual(state["history_days"], 183)
        self.assertEqual(state["history_added"], 1)
        self.assertRegex(state["history_scanned_at"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("chrome", self.out.getvalue())
        self.assertEqual(result["gmail"], "signed_in")
        self.assertEqual(self.launches, [("claude", self.runtime_root)])

    def test_opening_the_profile_warns_to_sign_in_on_the_web_only_without_chrome_sync(self):
        # T24: Chrome sync in the tool's profile installed the user's own extensions and opened their tabs.
        self.setup_run([""], FakeGmail(signed=(True,)))
        shown = self.out.getvalue()
        self.assertTrue("web page only" in shown, "setup output lacks the web-only sign-in hint")
        self.assertTrue("do NOT turn on Chrome sync" in shown, "setup output lacks the Chrome sync warning")
        vi = self.ts.MESSAGES["vi"]["gmail_opened"]
        self.assertIn("trang web", vi)
        self.assertIn("KHÔNG bật đồng bộ Chrome", vi)

    def test_already_signed_in_does_not_wait(self):
        gmail = FakeGmail(signed=(True,))
        self.setup_run([""], gmail)
        self.assertEqual(gmail.calls, ["open", "signed_in", "history"])

    def test_not_signed_in_is_said_and_asked_again(self):
        gmail = FakeGmail(signed=(False, False, True))
        self.setup_run(["", "", ""], gmail)
        self.assertIn(self.ts.MESSAGES["en"]["gmail_not_signed_in"], self.out.getvalue())
        self.assertEqual(gmail.calls.count("history"), 1)

    def test_skip_while_waiting_keeps_setup_going(self):
        gmail = FakeGmail(signed=(False,))
        result = self.setup_run(["", "s"], gmail)
        self.assertNotIn("history", gmail.calls)
        self.assertEqual(self.config()["gmail"]["status"], "skipped")
        self.assertEqual(result["status"], "launched")

    def test_declining_gmail_does_not_open_chrome(self):
        gmail = FakeGmail()
        self.setup_run(["n"], gmail)
        self.assertEqual(gmail.calls, [])
        self.assertEqual(self.config()["gmail"]["status"], "skipped")

    def test_chrome_problem_is_reported_and_setup_continues(self):
        gmail = FakeGmail(error=FileNotFoundError("Chrome not found"))
        result = self.setup_run([""], gmail)
        self.assertIn("Chrome not found", self.out.getvalue())
        self.assertEqual(self.config()["gmail"]["status"], "unavailable")
        self.assertEqual(result["status"], "launched")

    def test_rescan_adds_nothing_twice(self):
        self.setup_run([""], FakeGmail())
        self.setup_run([""], FakeGmail(), before=["1", "1", "", "", ""])
        self.assertEqual(len(self.tracker()), 1)
        self.assertEqual(self.config()["gmail"]["history_added"], 0)

    def test_vietnamese_text_has_every_gmail_message(self):
        for key in self.ts.MESSAGES["en"]:
            self.assertIn(key, self.ts.MESSAGES["vi"])
        self.assertIn("gmail_connect", self.ts.MESSAGES["vi"])

    def test_without_a_gmail_session_the_step_is_skipped(self):
        self.wizard(["1", "1", "", str(self.pdf("cv.pdf")), "SWE", "n", ""])
        self.assertNotIn("gmail", self.config())

    def test_runtime_terminal_setup_passes_a_gmail_session_for_its_root(self):
        from unittest.mock import patch

        runtime, seen = self.module("runtime"), {}
        with patch.object(self.ts, "run", lambda root, **kwargs: seen.update(kwargs) or {"status": "ready"}):
            runtime.setup(root=self.root, terminal=True, launch=False)
        session = seen["gmail"]
        self.assertIsInstance(session, self.module("gmail_tab").GmailSession)
        self.assertEqual(session.root, self.root.resolve())
