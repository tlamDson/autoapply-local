"""Fill -> review -> explicitly submit, with fresh browser evidence and durability.

Giả định của người thực thi: Workflow(root,browser,engines,default_engine,clock)
exposes apply(ids,pdf,engine=None), review(ids), submit(ids), status(), and store.
Browser methods are injected; no actual ATS, browser, network or email is used.
"""

import hashlib
from pathlib import Path
from unittest.mock import Mock

from test_support import NOW, ReleaseCase, candidate, job, snapshot


class WorkflowTests(ReleaseCase):
    def prepared(self):
        workflow = self.module("workflow")
        self.write_json("profile.json", candidate("authorized"))
        self.pdf = self.root / "synthetic.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n% synthetic bytes; compilation is mocked\n%%EOF")
        self.digest = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        self.live = snapshot(resume_sha256=self.digest)
        self.live["fields"][0]["value"] = candidate("authorized")["contact"]["legal_name"]["value"]
        self.browser = Mock(spec=["open", "fill", "upload_file", "snapshot", "submit", "close"])
        self.browser.open.return_value = "tab-1"
        self.browser.fill.return_value = {"status": "filled"}
        self.browser.snapshot.side_effect = lambda *args, **kwargs: self.live.copy()
        self.browser.submit.return_value = {
            "status": "confirmed",
            "job_id": "synthetic-1",
            "url": job()["url"] + "/confirmation",
            "text": "Application received",
        }
        self.engines = {"codex": self.fake_engine(), "claude": self.fake_engine()}
        runner = workflow.Workflow(
            self.root, browser=self.browser, engines=self.engines, default_engine="codex", clock=lambda: NOW
        )
        runner.store.enqueue(job())
        return runner

    def reviewed(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf)
        runner.review(["synthetic-1"])
        return runner

    def test_quick_apply_really_fills_uploads_and_holds_without_submitting(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf)
        self.browser.open.assert_called_once()
        self.browser.fill.assert_called_once()
        self.browser.upload_file.assert_called_once()
        self.assertEqual(Path(self.browser.upload_file.call_args.args[1]), self.pdf)
        self.browser.snapshot.assert_called()
        self.browser.submit.assert_not_called()
        self.browser.close.assert_not_called()
        self.assertEqual(runner.store.tracker_rows(), [])
        self.assertEqual(runner.status()["held_tabs"][0]["job_id"], "synthetic-1")

    def test_engine_override_isolated_and_quick_never_tailors(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf, engine="claude")
        self.engines["claude"].run.assert_called()
        self.engines["codex"].run.assert_not_called()
        tasks = [call.args[0] for call in self.engines["claude"].run.call_args_list]
        self.assertTrue(all(task["kind"] != "tailor" for task in tasks))

    def test_default_engine_used_without_cross_engine_fallback(self):
        runner = self.prepared()
        self.engines["codex"].run.side_effect = RuntimeError("synthetic engine unavailable")
        result = runner.apply(["synthetic-1"], pdf=self.pdf)
        self.assertEqual([(item["job_id"], item["status"]) for item in result["jobs"]], [("synthetic-1", "retryable")])
        self.assertEqual(runner.store.get("synthetic-1")["state"], "pending")
        self.engines["codex"].run.assert_called_once()
        self.assertEqual(self.engines["codex"].run.call_args.args[0]["kind"], "apply_analysis")
        self.engines["claude"].run.assert_not_called()
        self.assertEqual(self.browser.mock_calls, [])
        for method in ("open", "fill", "snapshot", "upload_file", "submit", "close"):
            getattr(self.browser, method).assert_not_called()

    def test_apply_requires_explicit_existing_pdf(self):
        runner = self.prepared()
        with self.assertRaises((ValueError, FileNotFoundError)):
            runner.apply(["synthetic-1"], pdf=self.root / "absent.pdf")
        self.browser.open.assert_not_called()

    def test_review_reads_live_browser_and_never_submits_or_closes(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf)
        self.browser.snapshot.reset_mock()
        runner.review(["synthetic-1"])
        self.browser.snapshot.assert_called()
        self.browser.submit.assert_not_called()
        self.browser.close.assert_not_called()
        record = runner.store.get("synthetic-1")["review"]
        self.assertEqual(record["profile_revision"], 1)
        self.assertEqual(record["resume_sha256"], self.digest)
        self.assertEqual(record["job_id"], "synthetic-1")
        self.assertEqual(record["page_id"], "page-1")

    def test_review_blocks_unknown_required_field(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf)
        self.live["fields"].append(
            {"id": "legal", "label": "Legal confirmation", "required": True, "value": None, "status": "unknown"}
        )
        with self.assertRaises(ValueError):
            runner.review(["synthetic-1"])
        self.browser.submit.assert_not_called()
        self.browser.close.assert_not_called()

    def test_submit_requires_previously_reviewed_selection(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf)
        for selection in (["synthetic-1"], "all", []):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                runner.submit(selection)
        self.browser.submit.assert_not_called()

    def test_submit_rechecks_live_values_not_just_saved_approval(self):
        runner = self.reviewed()
        self.live["fields"] = [
            {"id": "name", "label": "Legal name", "required": True, "value": "Changed Name", "status": "confirmed"}
        ]
        with self.assertRaises(ValueError):
            runner.submit(["synthetic-1"])
        self.browser.submit.assert_not_called()
        self.browser.close.assert_not_called()

    def test_changed_job_page_attachment_missing_required_or_error_blocks_submission(self):
        runner = self.reviewed()
        original = self.live.copy()
        changes = (
            {"job_id": "other"},
            {"page_id": "page-2"},
            {"url": "https://jobs.example.invalid/other"},
            {"resume_sha256": "0" * 64},
            {"fields": []},
            {"errors": ["Required field missing"]},
        )
        for change in changes:
            with self.subTest(change=change):
                self.live = {**original, **change}
                with self.assertRaises(ValueError):
                    runner.submit(["synthetic-1"])
        self.browser.submit.assert_not_called()

    def test_profile_edit_invalidates_submission_even_if_browser_unchanged(self):
        runner = self.reviewed()
        changed = candidate("authorized")
        changed["revision"] = 2
        changed["targets"]["roles"] = ["frontend"]
        self.write_json("profile.json", changed)
        with self.assertRaises(ValueError):
            runner.submit(["synthetic-1"])
        self.browser.submit.assert_not_called()

    def test_pdf_contents_changed_under_same_filename_blocks_submission(self):
        runner = self.reviewed()
        self.pdf.write_bytes(b"%PDF-1.4\nchanged synthetic resume\n%%EOF")
        with self.assertRaises(ValueError):
            runner.submit(["synthetic-1"])
        self.browser.submit.assert_not_called()

    def test_fabricated_approval_file_cannot_replace_a_fresh_browser_read(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf)
        self.write_json(
            "reviews.json",
            [
                {
                    "job_id": "synthetic-1",
                    "approved": True,
                    "profile_revision": 1,
                    "resume_sha256": self.digest,
                    "page_id": "page-1",
                }
            ],
        )
        self.browser.snapshot.side_effect = RuntimeError("synthetic live read unavailable")
        with self.assertRaises((ValueError, RuntimeError)):
            runner.submit(["synthetic-1"])
        self.browser.submit.assert_not_called()

    def test_submit_intent_is_durable_before_click_and_confirmation_updates_tracker(self):
        runner = self.reviewed()
        storage = self.module("storage")

        def click(*args, **kwargs):
            reopened = storage.Store(self.root)
            self.assertEqual(reopened.get("synthetic-1")["state"], "submit_intent")
            self.assertEqual(reopened.tracker_rows(), [])
            return {
                "status": "confirmed",
                "job_id": "synthetic-1",
                "url": job()["url"] + "/confirmation",
                "text": "Application received",
            }

        self.browser.submit.side_effect = click
        runner.submit(["synthetic-1"])
        self.browser.submit.assert_called_once()
        self.assertEqual(runner.store.get("synthetic-1")["state"], "submitted")
        self.assertEqual(len(runner.store.tracker_rows()), 1)

    def test_unknown_submit_outcome_blocks_retry_and_preserves_tab_across_restart(self):
        runner = self.reviewed()
        self.browser.submit.side_effect = TimeoutError("synthetic response lost after click")
        runner.submit(["synthetic-1"])
        self.assertEqual(runner.store.get("synthetic-1")["state"], "needs_reconciliation")
        self.assertEqual(runner.store.tracker_rows(), [])
        restarted = self.module("workflow").Workflow(
            self.root, browser=self.browser, engines=self.engines, default_engine="codex", clock=lambda: NOW
        )
        with self.assertRaises(ValueError):
            restarted.submit(["synthetic-1"])
        self.assertEqual(self.browser.submit.call_count, 1)
        self.browser.close.assert_not_called()
        self.assertIn("synthetic-1", [item["job_id"] for item in restarted.status()["unresolved"]])

    def test_all_reviewed_does_not_submit_unreviewed_jobs(self):
        runner = self.reviewed()
        runner.store.enqueue(job("synthetic-2"))
        runner.submit("all-reviewed")
        self.browser.submit.assert_called_once()
        self.assertNotEqual(runner.store.get("synthetic-2")["state"], "submitted")

    def test_resumed_apply_never_reopens_an_already_held_filled_job(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf)
        runner.apply(["synthetic-1"], pdf=self.pdf)
        self.assertEqual(self.browser.open.call_count, 1)
        self.browser.submit.assert_not_called()
        self.browser.close.assert_not_called()

    def test_review_reads_resume_parser_changes_and_does_not_reuse_fill_snapshot(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf)
        self.live["fields"] = [
            {"id": "name", "label": "Legal name", "required": True, "value": "", "status": "unknown"}
        ]
        with self.assertRaises(ValueError):
            runner.review(["synthetic-1"])
        self.browser.submit.assert_not_called()
        self.browser.close.assert_not_called()

    def test_review_rejects_missing_attachment_and_live_validation_errors(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf)
        original = self.live.copy()
        for change in ({"resume_sha256": None}, {"errors": ["Invalid email"]}):
            with self.subTest(change=change):
                self.live = {**original, **change}
                with self.assertRaises(ValueError):
                    runner.review(["synthetic-1"])
        self.browser.submit.assert_not_called()
        self.browser.close.assert_not_called()

    def test_unconfirmed_click_result_requires_reconciliation_without_tracker_write(self):
        runner = self.reviewed()
        self.browser.submit.return_value = {"status": "clicked"}
        runner.submit(["synthetic-1"])
        self.assertEqual(runner.store.get("synthetic-1")["state"], "needs_reconciliation")
        self.assertEqual(runner.store.tracker_rows(), [])
        with self.assertRaises(ValueError):
            runner.submit(["synthetic-1"])
        self.browser.submit.assert_called_once()
        self.browser.close.assert_not_called()

    def test_crash_after_durable_intent_cannot_be_retried_automatically(self):
        runner = self.reviewed()
        self.browser.submit.side_effect = KeyboardInterrupt("synthetic process termination after intent")
        with self.assertRaises(KeyboardInterrupt):
            runner.submit(["synthetic-1"])
        restarted = self.module("workflow").Workflow(
            self.root, browser=self.browser, engines=self.engines, default_engine="codex", clock=lambda: NOW
        )
        with self.assertRaises(ValueError):
            restarted.submit(["synthetic-1"])
        self.assertEqual(self.browser.submit.call_count, 1)
        self.assertEqual(restarted.store.tracker_rows(), [])
        self.assertIn("synthetic-1", [item["job_id"] for item in restarted.status()["unresolved"]])
        self.browser.close.assert_not_called()
