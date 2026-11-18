"""Guided setup skips questions it already has answers to or that cannot apply (T24)."""

import json
import unittest

import test_guided_quick
from test_support import ReleaseCase

TERMINAL = ("runtime.language", "runtime.apply_engine", "resumes.main_pdf", "resumes.alternatives",
            "resumes.role_mapping")
GMAIL = ("resources.gmail_account", "resources.gmail_lookback", "resources.gmail_permissions")


class GuidedSkipTests(ReleaseCase):
    # Borrow the helpers without importing the class, so its tests do not run twice.
    prepared = test_guided_quick.GuidedQuickTests.prepared
    run_guided = test_guided_quick.GuidedQuickTests.run_guided
    progress = test_guided_quick.GuidedQuickTests.progress
    document = test_guided_quick.GuidedQuickTests.document
    entry = test_guided_quick.GuidedQuickTests.entry
    reply = test_guided_quick.GuidedQuickTests.reply
    drive = test_guided_quick.GuidedQuickTests.drive

    def walk(self, answers, terminal=True):
        self.prepared(terminal=terminal)
        packet, seen = self.drive(self.run_guided(), answers, "never")
        self.assertEqual(packet["status"], "draft_complete")
        return seen

    def test_terminal_answers_are_not_asked_again(self):
        seen = self.walk({"runtime.mode": "quick"})
        self.assertIn("runtime.mode", seen)
        self.assertFalse(set(TERMINAL) & set(seen), sorted(set(TERMINAL) & set(seen)))
        groups = self.document()
        self.assertEqual((groups["runtime"]["language"], groups["runtime"]["apply_engine"]), ("en", "claude"))
        self.assertTrue(groups["resumes"]["main_pdf"].endswith("alex.txt"))

    def test_without_terminal_setup_language_and_engine_are_still_asked(self):
        seen = self.walk({}, terminal=False)
        for field in ("runtime.language", "runtime.apply_engine", "resumes.main_pdf"):
            self.assertIn(field, seen)

    def test_existing_resumes_mode_skips_every_tailoring_question(self):
        seen = self.walk({"runtime.mode": "quick"})
        tailoring = [field for field in seen if field.startswith("tailor.") or field == "runtime.tailor_engine"]
        self.assertEqual(tailoring, [])

    def test_tailoring_mode_still_asks_tailoring_questions(self):
        seen = self.walk({"runtime.mode": "both"})
        for field in ("runtime.tailor_engine", "tailor.enabled", "tailor.scope", "tailor.missing_facts"):
            self.assertIn(field, seen)

    def test_gmail_no_skips_gmail_permission_questions(self):
        seen = self.walk({"runtime.mode": "quick", "resources.gmail_opt_in": False})
        self.assertIn("resources.gmail_opt_in", seen)
        self.assertFalse(set(GMAIL) & set(seen), sorted(set(GMAIL) & set(seen)))

    def test_gmail_yes_asks_the_gmail_questions(self):
        seen = self.walk({"runtime.mode": "quick", "resources.gmail_opt_in": True})
        for field in GMAIL:
            self.assertIn(field, seen)

    def test_gmail_opt_in_is_answered_before_its_questions_are_shown(self):
        self.prepared()
        packet, _ = self.drive(self.run_guided(), {"runtime.mode": "quick"}, "resources.gmail_opt_in")
        ids = [q["id"] for q in packet["questions"]]
        self.assertEqual(ids[-1], "resources.gmail_opt_in", "Questions it turns off never share its popup")

    def test_visa_status_is_asked_once_and_shared(self):
        seen = self.walk({"runtime.mode": "quick", "authorization.current_status": "F-1 student"})
        self.assertEqual(seen.count("authorization.current_status"), 1)
        self.assertNotIn("international_student.status", seen)
        groups = self.document()
        self.assertEqual(groups["authorization"]["current_status"], "F-1 student")
        self.assertEqual(groups["international_student"]["status"], "F-1 student")

    def test_shared_visa_status_reaches_the_confirmed_profile(self):
        self.prepared()
        packet, _ = self.drive(self.run_guided(), {"runtime.mode": "quick",
                                                   "authorization.current_status": "F-1 student"}, "never")
        self.run_guided(finish=True)
        self.assertTrue(self.run_guided(confirm=True)["applied"])
        profile = json.loads((self.root / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["international_student"]["status"], "F-1 student")


if __name__ == "__main__":
    unittest.main()
