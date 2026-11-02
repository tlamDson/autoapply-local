"""Native connector only, explicit read permission and evidence-based updates.

Giả định của người thực thi: gmail.MailChecker(root, runtime, clock).check(config)
returns status/events/ambiguous; verification_code(config, job_id) returns a
transient string. runtime exposes discover_capabilities(), search_mail(account,
query, lookback_days), read_mail(account, message_id); config pins account and
permissions. Persistent event summaries omit raw verification bodies/codes.
"""

import copy
from unittest.mock import Mock

from test_support import NOW, ReleaseCase, job


ACCOUNT = "alex@example.invalid"


def message(message_id="mail-1", **changes):
    data = {
        "id": message_id,
        "account": ACCOUNT,
        "from": "recruiting@example.invalid",
        "subject": "Example Labs application received",
        "body": "We received your application for Backend Software Engineering Intern. "
        "Reference: synthetic-1. https://jobs.example.invalid/synthetic-1",
        "received_at": NOW,
    }
    data.update(changes)
    return data


class GmailTests(ReleaseCase):
    def prepared(self):
        gmail = self.module("gmail")
        self.store = self.module("storage").Store(self.root)
        self.store.enqueue(job())
        self.runtime = Mock(
            spec=[
                "discover_capabilities",
                "search_mail",
                "read_mail",
                "open_browser",
                "send_mail",
                "draft_mail",
                "archive_mail",
                "label_mail",
            ]
        )
        self.runtime.discover_capabilities.return_value = {
            "gmail": {"accounts": [ACCOUNT], "permissions": ["search", "read"]}
        }
        self.runtime.search_mail.return_value = [{"id": "mail-1", "account": ACCOUNT}]
        self.runtime.read_mail.return_value = message()
        self.config = {
            "gmail_opt_in": True,
            "gmail_account": ACCOUNT,
            "gmail_permissions": ["search", "read"],
            "gmail_lookback": 7,
        }
        return gmail.MailChecker(self.root, runtime=self.runtime, clock=lambda: NOW)

    def assert_no_mutating_or_browser_calls(self):
        for method in ("open_browser", "send_mail", "draft_mail", "archive_mail", "label_mail"):
            getattr(self.runtime, method).assert_not_called()

    def test_no_opt_in_never_searches_or_reads_mail(self):
        checker = self.prepared()
        result = checker.check({**self.config, "gmail_opt_in": False})
        self.assertEqual(result["status"], "disabled")
        self.runtime.search_mail.assert_not_called()
        self.runtime.read_mail.assert_not_called()
        self.assert_no_mutating_or_browser_calls()

    def test_missing_connector_returns_manual_fallback_without_browser_gmail(self):
        checker = self.prepared()
        self.runtime.discover_capabilities.return_value = {}
        self.assertEqual(checker.check(self.config)["status"], "manual_required")
        self.runtime.search_mail.assert_not_called()
        self.runtime.read_mail.assert_not_called()
        self.assert_no_mutating_or_browser_calls()

    def test_configured_account_must_be_explicitly_available(self):
        checker = self.prepared()
        self.config["gmail_account"] = "unapproved@example.invalid"
        self.assertEqual(checker.check(self.config)["status"], "manual_required")
        self.runtime.search_mail.assert_not_called()
        self.runtime.read_mail.assert_not_called()

    def test_absent_account_does_not_choose_the_first_signed_in_account(self):
        checker = self.prepared()
        self.config["gmail_account"] = None
        self.assertEqual(checker.check(self.config)["status"], "manual_required")
        self.runtime.search_mail.assert_not_called()
        self.runtime.read_mail.assert_not_called()

    def test_each_permission_needed_in_both_user_config_and_discovered_capability(self):
        checker = self.prepared()
        for source in ("config", "capability"):
            for missing in ("search", "read"):
                with self.subTest(source=source, missing=missing):
                    config = copy.deepcopy(self.config)
                    capabilities = {"gmail": {"accounts": [ACCOUNT], "permissions": ["search", "read"]}}
                    target = config if source == "config" else capabilities["gmail"]
                    key = "gmail_permissions" if source == "config" else "permissions"
                    target[key].remove(missing)
                    self.runtime.discover_capabilities.return_value = capabilities
                    self.assertEqual(checker.check(config)["status"], "manual_required")
                    self.runtime.search_mail.assert_not_called()
                    self.runtime.read_mail.assert_not_called()

    def test_search_and_read_use_selected_account_and_configured_lookback(self):
        checker = self.prepared()
        checker.check(self.config)
        self.runtime.discover_capabilities.assert_called_once()
        self.runtime.search_mail.assert_called_once()
        self.assertEqual(self.runtime.search_mail.call_args.kwargs["account"], ACCOUNT)
        self.assertEqual(self.runtime.search_mail.call_args.kwargs["lookback_days"], 7)
        self.runtime.read_mail.assert_called_once_with(account=ACCOUNT, message_id="mail-1")
        self.assert_no_mutating_or_browser_calls()

    def test_response_from_a_different_account_cannot_update_tracker(self):
        checker = self.prepared()
        self.runtime.read_mail.return_value = message(account="other@example.invalid")
        result = checker.check(self.config)
        self.assertEqual(result["status"], "manual_required")
        self.assertEqual(self.store.tracker_rows(), [])

    def test_confirmed_application_mail_is_matched_and_deduplicated_across_restart(self):
        checker = self.prepared()
        first = checker.check(self.config)
        self.assertEqual(len(first["events"]), 1)
        self.assertEqual(first["events"][0]["job_id"], "synthetic-1")
        self.assertEqual(len(self.store.tracker_rows()), 1)
        restarted = self.module("gmail").MailChecker(self.root, runtime=self.runtime, clock=lambda: NOW)
        self.assertEqual(restarted.check(self.config)["events"], [])
        self.assertEqual(len(self.store.tracker_rows()), 1)

    def test_ambiguous_company_only_mail_does_not_mark_a_job_submitted(self):
        checker = self.prepared()
        self.store.enqueue(job("synthetic-2", role="Frontend Engineering Intern"))
        self.runtime.read_mail.return_value = message(body="Thank you for applying to Example Labs.")
        result = checker.check(self.config)
        self.assertEqual(len(result["ambiguous"]), 1)
        self.assertEqual(self.store.tracker_rows(), [])

    def test_unmatched_mail_never_creates_a_fabricated_application(self):
        checker = self.prepared()
        self.runtime.read_mail.return_value = message(
            subject="Other Example application received",
            body="Thank you for applying for a Research Internship at Other Example.",
        )
        checker.check(self.config)
        self.assertEqual(self.store.tracker_rows(), [])
        self.assertEqual([item["id"] for item in self.store.jobs()], ["synthetic-1"])

    def test_no_confirmation_mail_is_not_evidence_that_an_application_failed(self):
        checker = self.prepared()
        before = copy.deepcopy(self.store.get("synthetic-1"))
        self.runtime.search_mail.return_value = []
        result = checker.check(self.config)
        self.assertEqual(result["events"], [])
        self.assertEqual(self.store.get("synthetic-1"), before)
        self.assertEqual(self.store.tracker_rows(), [])

    def test_connector_failure_preserves_tracker_and_never_falls_back_to_browser(self):
        checker = self.prepared()
        self.runtime.search_mail.side_effect = RuntimeError("synthetic permission revoked")
        self.assertEqual(checker.check(self.config)["status"], "manual_required")
        self.runtime.read_mail.assert_not_called()
        self.assertEqual(self.store.tracker_rows(), [])
        self.assert_no_mutating_or_browser_calls()

    def test_current_job_verification_code_is_transient_and_requires_verification_permission(self):
        checker = self.prepared()
        self.runtime.read_mail.return_value = message(
            subject="Verify current application synthetic-1",
            body="Verification code: 91827463. Job: https://jobs.example.invalid/synthetic-1",
        )
        config = {**self.config, "gmail_permissions": ["search", "read", "current_job_verification"]}
        self.assertEqual(checker.verification_code(config, "synthetic-1"), "91827463")
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"91827463", path.read_bytes(), str(path.relative_to(self.root)))
        self.assertEqual(self.store.tracker_rows(), [])
        self.assert_no_mutating_or_browser_calls()

    def test_general_read_permission_does_not_authorize_code_extraction(self):
        checker = self.prepared()
        with self.assertRaises(PermissionError):
            checker.verification_code(self.config, "synthetic-1")
        self.runtime.search_mail.assert_not_called()
        self.runtime.read_mail.assert_not_called()

    def test_code_for_another_job_is_not_returned_for_current_job(self):
        checker = self.prepared()
        self.runtime.read_mail.return_value = message(
            subject="Verify synthetic-other application",
            body="Verification code: 91827463. Job: https://jobs.example.invalid/synthetic-other",
        )
        config = {**self.config, "gmail_permissions": ["search", "read", "current_job_verification"]}
        self.assertIsNone(checker.verification_code(config, "synthetic-1"))
        self.assertEqual(self.store.tracker_rows(), [])
