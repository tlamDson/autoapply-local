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
        self.assertEqual(runner.store.get("synthetic-1")["held_at"], NOW, "track compares email dates to this")

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

