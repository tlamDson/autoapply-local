"""Regressions from complete-plan items 1, 4 and 5; synthetic data only.

Giả định của người thực thi: retain the existing Workflow API. A browser's
``confirmed`` flag is an observation, not authority for identity or answers.
Unknown questions use profile.answers with the exact country/job/employer/time
scope. No implementation or plan is edited in this test-only turn.
"""

import copy
import multiprocessing
from pathlib import Path
from unittest.mock import Mock, patch

import test_workflow
from test_support import NOW, ReleaseCase, fact, job


def submit_in_process(root, live, start, clicks, output):
    """Spawn-safe worker; its only click is a counter on a fake browser."""
    from autoapply_local.storage import Store
    from autoapply_local.workflow import Workflow

    browser = Mock(spec=["snapshot", "submit", "close"])
    browser.snapshot.return_value = live

    def click(*args, **kwargs):
        # A second process can see intent before any simulated side effect.
        state = Store(root).get("synthetic-1")["state"]
        if state != "submit_intent":
            raise AssertionError("Click happened before durable intent")
        with clicks.get_lock():
            clicks.value += 1
        return {
            "status": "confirmed",
            "job_id": "synthetic-1",
            "url": job()["url"] + "/confirmation",
            "text": "Application received",
        }

    browser.submit.side_effect = click
    runner = Workflow(root, browser=browser, engines={}, clock=lambda: NOW)
    if not start.wait(10):
        output.put("start_timeout")
        return
    try:
        runner.submit(["synthetic-1"])
    except ValueError:
        output.put("blocked")
    except BaseException as error:
        output.put(type(error).__name__)
    else:
        output.put("submitted")


class WorkflowCompleteTests(ReleaseCase):
    prepared = test_workflow.WorkflowTests.prepared
    reviewed = test_workflow.WorkflowTests.reviewed

    def filled(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf)
        return runner

    def assert_review_blocked(self, runner):
        with self.assertRaises(ValueError):
            runner.review(["synthetic-1"])
        self.browser.submit.assert_not_called()
        self.browser.close.assert_not_called()
        self.assertNotEqual(runner.store.get("synthetic-1")["state"], "reviewed")

    def test_wrong_legal_name_cannot_be_reviewed_even_if_browser_says_confirmed(self):
        runner = self.filled()
        self.live["fields"][0].update(value="Alex Example", status="confirmed")
        self.assert_review_blocked(runner)

    def test_wrong_email_cannot_be_reviewed_even_if_browser_says_confirmed(self):
        runner = self.filled()
        self.live["fields"].append(
            {
                "id": "email",
                "label": "Application email",
                "type": "email",
                "required": True,
                "value": "other@example.invalid",
                "status": "confirmed",
            }
        )
        self.assert_review_blocked(runner)

    def test_present_optional_identity_must_still_match_confirmed_profile(self):
        runner = self.filled()
        self.live["fields"][0].update(required=False, value="Alex Example", status="confirmed")
        self.assert_review_blocked(runner)

    def test_matching_identity_email_is_reviewable(self):
        runner = self.filled()
        self.live["fields"].append(
            {
                "id": "email",
                "label": "Application email",
                "type": "email",
                "required": True,
                "value": "alex@example.invalid",
                "status": "confirmed",
            }
        )
        runner.review(["synthetic-1"])
        self.assertEqual(runner.store.get("synthetic-1")["state"], "reviewed")
        self.browser.submit.assert_not_called()

    def test_native_observation_is_confirmed_by_profile_without_agent_status_assertion(self):
        runner = self.filled()
        self.live["fields"][0].pop("status")
        self.live["fields"][0]["observed"] = True
        runner.review(["synthetic-1"])
        self.assertEqual(runner.store.get("synthetic-1")["state"], "reviewed")
        self.browser.submit.assert_not_called()

    def test_unknown_required_question_cannot_self_confirm(self):
        runner = self.filled()
        self.live["fields"].append(
            {
                "id": "consent",
                "label": "May we retain your application for two years?",
                "type": "checkbox",
                "required": True,
                "value": True,
                "status": "confirmed",
            }
        )
        self.assert_review_blocked(runner)

    def test_answer_from_another_employer_cannot_authorize_required_question(self):
        runner = self.filled()
        question = "Authorized for any employer without restrictions?"
        profile = runner._profile()
        profile["answers"] = [
            {
                "question": question,
                **fact(
                    "Yes",
                    scope={
                        "country": "US",
                        "job_type": "internship",
                        "employer": "Other Example Labs",
                        "question": question,
                        "effective_at": "2030-06-01",
                    },
                ),
            }
        ]
        self.write_json("profile.json", profile)
        self.live["fields"].append(
            {
                "id": "auth",
                "label": question,
                "required": True,
                "value": "Yes",
                "status": "confirmed",
            }
        )
        self.assert_review_blocked(runner)

    def test_exact_question_scope_and_observed_answer_are_reviewable(self):
        runner = self.filled()
        question = "Authorized for any employer without restrictions?"
        profile = runner._profile()
        scope = {
            "country": "US",
            "job_type": "internship",
            "employer": "Example Labs",
            "question": question,
            "effective_at": "2030-06-01",
        }
        profile["answers"] = [{"question": question, **fact("Yes", scope=scope)}]
        self.write_json("profile.json", profile)
        runner.store.update("synthetic-1", start_date="2030-06-01")
        self.live["fields"].append(
            {
                "id": "auth",
                "label": question,
                "required": True,
                "value": "Yes",
                "status": "confirmed",
            }
        )
        runner.review(["synthetic-1"])
        self.assertEqual(runner.store.get("synthetic-1")["state"], "reviewed")
        self.browser.submit.assert_not_called()

    def test_exact_scoped_confirmation_does_not_authorize_a_different_live_answer(self):
        runner = self.filled()
        question = "Authorized for any employer without restrictions?"
        profile = runner._profile()
        scope = {
            "country": "US",
            "job_type": "internship",
            "employer": "Example Labs",
            "question": question,
            "effective_at": "2030-06-01",
        }
        profile["answers"] = [{"question": question, **fact("No", scope=scope)}]
        self.write_json("profile.json", profile)
        runner.store.update("synthetic-1", start_date="2030-06-01")
        self.live["fields"].append(
            {
                "id": "auth",
                "label": question,
                "required": True,
                "value": "Yes",
                "status": "confirmed",
            }
        )
        self.assert_review_blocked(runner)

    def test_required_unchecked_checkbox_is_not_verified(self):
        runner = self.filled()
        self.live["fields"].append(
            {
                "id": "consent",
                "label": "Consent",
                "type": "checkbox",
                "required": True,
                "value": False,
                "status": "confirmed",
            }
        )
        self.assert_review_blocked(runner)

    def test_unseen_required_control_cannot_be_marked_verified(self):
        runner = self.filled()
        self.live["fields"][0].update(observed=False, status="confirmed")
        self.assert_review_blocked(runner)

    def test_custom_required_select_placeholder_cannot_be_marked_verified(self):
        runner = self.filled()
        self.live["fields"].append(
            {
                "id": "school",
                "label": "School",
                "type": "combobox",
                "required": True,
                "value": "Select One",
                "status": "confirmed",
            }
        )
        self.assert_review_blocked(runner)

    def test_attachment_digest_cannot_hide_different_actual_selected_file(self):
        runner = self.filled()
        self.live["fields"].append(
            {
                "id": "resume",
                "label": "Resume PDF",
                "type": "file",
                "required": True,
                "value": "other-resume.pdf",
                "status": "confirmed",
                "files": [{"name": "other-resume.pdf", "size": 17}],
            }
        )
        self.assert_review_blocked(runner)

    def test_initial_review_must_bind_actual_job_url_not_just_claimed_job_id(self):
        runner = self.filled()
        self.live["url"] = "https://jobs.example.invalid/another-job"
        self.assert_review_blocked(runner)

    def test_apply_claims_job_before_selected_engine_can_perform_side_effects(self):
        runner = self.prepared()

        def observe(task):
            self.assertEqual(runner.store.get("synthetic-1")["state"], "claimed")
            return {"status": "completed"}

        self.engines["codex"].run.side_effect = observe
        runner.apply(["synthetic-1"], pdf=self.pdf)
        self.browser.submit.assert_not_called()

    def test_pre_fill_engine_step_is_explicitly_analysis_only(self):
        runner = self.prepared()
        runner.apply(["synthetic-1"], pdf=self.pdf)
        task = self.engines["codex"].run.call_args.args[0]
        # The actual form is opened/filled/uploaded by browser after this task;
        # this call must not independently open and fill a duplicate form.
        self.assertEqual(task["kind"], "apply_analysis")
        self.assertEqual(task["allowed_tools"], [])
        self.browser.open.assert_called_once()
        self.browser.fill.assert_called_once()
        self.browser.upload_file.assert_called_once()

    def test_two_processes_submit_one_reviewed_job_with_one_durable_click_and_row(self):
        runner = self.reviewed()
        context = multiprocessing.get_context("spawn")
        start, clicks, output = context.Event(), context.Value("i", 0), context.Queue()
        processes = [
            context.Process(
                target=submit_in_process,
                args=(
                    str(self.root),
                    copy.deepcopy(self.live),
                    start,
                    clicks,
                    output,
                ),
            )
            for _ in range(2)
        ]
        try:
            for process in processes:
                process.start()
            start.set()
            results = [output.get(timeout=20) for _ in processes]
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
            self.assertCountEqual(results, ["submitted", "blocked"])
            self.assertEqual(clicks.value, 1)
            reopened = self.module("storage").Store(self.root)
            self.assertEqual(reopened.get("synthetic-1")["state"], "submitted")
            self.assertEqual([row["id"] for row in reopened.tracker_rows()], ["synthetic-1"])
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(5)
            output.close()
            output.join_thread()

    def test_restart_after_xlsx_commit_and_ledger_failure_preserves_proof_without_reclick(self):
        runner = self.reviewed()
        storage = self.module("storage")
        real_write = storage.atomic_write_json

        def interrupted_commit(path, data):
            if Path(path).name == "ledger.json" and data["jobs"]["synthetic-1"]["state"] == "submitted":
                raise KeyboardInterrupt("synthetic crash after XLSX commit before JSON commit")
            return real_write(path, data)

        with patch.object(storage, "atomic_write_json", side_effect=interrupted_commit):
            with self.assertRaises(KeyboardInterrupt):
                runner.submit(["synthetic-1"])
        self.assertEqual(runner.store.get("synthetic-1")["state"], "submit_intent")
        self.assertEqual(len(runner.store.tracker_rows()), 1)
        restarted = self.module("workflow").Workflow(
            self.root,
            browser=self.browser,
            engines=self.engines,
            clock=lambda: NOW,
        )
        with self.assertRaises(ValueError):
            restarted.submit(["synthetic-1"])
        self.browser.submit.assert_called_once()
        self.assertEqual(restarted.store.get("synthetic-1")["state"], "submitted")
        self.assertIn("Application received", str(restarted.store.get("synthetic-1")["confirmation"]))
        self.assertEqual(restarted.store.tracker_rows()[0]["confirmation"], "Application received")

    def test_authoritative_tracker_survives_restored_stale_ledger_without_losing_job(self):
        runner = self.reviewed()
        storage = self.module("storage")
        stale = copy.deepcopy(storage.read_json(runner.store.path))
        stale["jobs"] = {}
        runner.submit(["synthetic-1"])
        # A valid but stale recovered ledger may predate enqueueing this job.
        storage.atomic_write_json(runner.store.path, stale)
        restarted = self.module("workflow").Workflow(
            self.root,
            browser=self.browser,
            engines=self.engines,
            clock=lambda: NOW,
        )
        records = {item["id"]: item for item in restarted.store.jobs()}
        self.assertIn("synthetic-1", records)
        self.assertEqual(records["synthetic-1"]["state"], "submitted")
        self.assertEqual(restarted.store.tracker_rows()[0]["confirmation"], "Application received")
        self.browser.submit.assert_called_once()
