"""A form the user submitted by hand leaves the held-tab count once recorded.

The tool never clicks Submit, so `submitted ID,ID` is the only way a held job
becomes submitted: it writes the tracker row and frees held-tab capacity
without touching the browser or an engine.
"""

import io
from unittest.mock import patch

from test_support import ReleaseCase, job

HELD_STATES = ("filled", "reviewed", "needs_manual")


class ManualSubmissionTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.store = self.module("storage").Store(self.root)
        self.runtime = self.module("runtime")
        self.manual = self.module("manual_submission")

    def held(self, job_id, state="filled"):
        self.store.enqueue(job(job_id))
        self.store.update(job_id, state=state, tab_id="tab-" + job_id)

    def held_ids(self):
        return [item["job_id"] for item in self.runtime.status(root=self.root)["held_tabs"]]

    def test_recording_frees_the_held_tab_and_writes_one_tracker_row(self):
        for state in HELD_STATES:
            with self.subTest(state=state):
                job_id = "held-" + state
                self.held(job_id, state)
                self.assertIn(job_id, self.held_ids())
                with (
                    patch("autoapply_local.browser.BrowserBridge", side_effect=AssertionError("no browser")),
                    patch("autoapply_local.engines.Engine", side_effect=AssertionError("no engine")),
                ):
                    result = self.runtime.submitted(root=self.root, selection=[job_id])
                self.assertEqual(result["jobs"], [{"job_id": job_id, "status": "submitted"}])
                self.assertEqual(self.store.get(job_id)["state"], "submitted")
                self.assertNotIn(job_id, self.held_ids())
                rows = [row for row in self.store.tracker_rows() if str(row["id"]) == job_id]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["status"], "submitted")

    def test_held_tab_cap_admits_new_work_after_recording(self):
        self.held("first")
        held = lambda: [item for item in self.store.jobs() if item.get("tab_id") and item["state"] != "submitted"]
        self.assertEqual(len(held()), 1)
        self.runtime.submitted(root=self.root, selection=["first"])
        self.assertEqual(held(), [])

    def test_jobs_without_a_held_form_are_refused_without_changes(self):
        self.store.enqueue(job("pending-job"))
        self.held("done")
        # Submitted through another flow (for example a matched confirmation email).
        self.store.update("done", state="submitted", confirmation={"text": "Matched confirmation email"})
        for job_id in ("pending-job", "done"):
            with self.subTest(job_id=job_id):
                before = self.store.get(job_id)
                rows = len(self.store.tracker_rows())
                with self.assertRaises(ValueError):
                    self.manual.record(self.store, job_id)
                self.assertEqual(self.store.get(job_id), before)
                self.assertEqual(len(self.store.tracker_rows()), rows)
        with self.assertRaises(KeyError):
            self.runtime.submitted(root=self.root, selection=["unknown"])

    def test_selection_is_checked_before_any_record_changes(self):
        self.held("good")
        self.store.enqueue(job("pending-job"))
        with self.assertRaises(ValueError):
            self.runtime.submitted(root=self.root, selection=["good", "pending-job"])
        self.assertEqual(self.store.get("good")["state"], "filled")

    def interrupt_once(self, name):
        cls = self.store.__class__
        original = getattr(cls, name)
        calls = []

        def flaky(store, *args, **kwargs):
            if not calls:
                calls.append(name)
                raise OSError("interrupted: " + name)
            return original(store, *args, **kwargs)

        return patch.object(cls, name, flaky)

    def test_interrupted_recording_can_be_retried_and_frees_the_slot(self):
        for step in ("record_submit_intent", "mark_submitted", "_save_workbook"):
            for state in HELD_STATES:
                with self.subTest(step=step, state=state):
                    job_id = f"{step}-{state}"
                    self.held(job_id, state)
                    with self.interrupt_once(step):
                        with self.assertRaises(OSError):
                            self.runtime.submitted(root=self.root, selection=[job_id])
                    self.assertIn(job_id, self.held_ids())
                    result = self.runtime.submitted(root=self.root, selection=[job_id])
                    self.assertEqual(result["jobs"], [{"job_id": job_id, "status": "submitted"}])
                    self.assertEqual(self.store.get(job_id)["state"], "submitted")
                    self.assertNotIn(job_id, self.held_ids())
                    rows = [row for row in self.store.tracker_rows() if str(row["id"]) == job_id]
                    self.assertEqual(len(rows), 1)

    def test_claims_of_other_flows_are_not_taken_for_manual_submissions(self):
        self.held("filling", "claimed")
        self.held("intent", "submit_intent")
        self.store.update("intent", submit_intent={"review_id": "another-flow"})
        for job_id in ("filling", "intent"):
            with self.subTest(job_id=job_id):
                before = self.store.get(job_id)
                with self.assertRaises(ValueError):
                    self.runtime.submitted(root=self.root, selection=[job_id])
                self.assertEqual(self.store.get(job_id), before)
                self.assertFalse(self.store.has_tracker_job(job_id))

    def test_interrupted_multi_id_recording_resumes_with_the_same_command(self):
        for step in ("record_submit_intent", "mark_submitted", "_save_workbook"):
            with self.subTest(step=step):
                ids = [f"multi-{step}-a", f"multi-{step}-b"]
                for job_id in ids:
                    self.held(job_id)
                original = self.manual.record
                done = []

                def second_fails(store, job_id, clock=None):
                    if done:
                        with self.interrupt_once(step):
                            return original(store, job_id, clock)
                    done.append(job_id)
                    return original(store, job_id, clock)

                with patch.object(self.manual, "record", second_fails):
                    with self.assertRaises(OSError):
                        self.runtime.submitted(root=self.root, selection=ids)
                self.assertEqual(self.store.get(ids[0])["state"], "submitted")
                self.assertIn(ids[1], self.held_ids())
                result = self.runtime.submitted(root=self.root, selection=ids)
                self.assertEqual(result["jobs"], [{"job_id": job_id, "status": "submitted"} for job_id in ids])
                for job_id in ids:
                    self.assertEqual(self.store.get(job_id)["state"], "submitted")
                    self.assertNotIn(job_id, self.held_ids())
                    rows = [row for row in self.store.tracker_rows() if str(row["id"]) == job_id]
                    self.assertEqual(len(rows), 1)

    def test_recording_an_already_recorded_job_changes_nothing(self):
        self.held("again")
        self.runtime.submitted(root=self.root, selection=["again"])
        before = self.store.get("again")
        rows = self.store.tracker_rows()
        result = self.runtime.submitted(root=self.root, selection=["again"])
        self.assertEqual(result["jobs"], [{"job_id": "again", "status": "submitted"}])
        self.assertEqual(self.store.get("again"), before)
        self.assertEqual(self.store.tracker_rows(), rows)
        # The same holds after the ledger is rebuilt from the tracker row.
        self.store.update("again", state="submit_intent", confirmation=None)
        reopened = self.module("storage").Store(self.root)
        self.assertEqual(reopened.get("again")["state"], "submitted")
        result = self.runtime.submitted(root=self.root, selection=["again"])
        self.assertEqual(result["jobs"], [{"job_id": "again", "status": "submitted"}])

    def test_cli_dispatches_explicit_ids_only(self):
        cli = self.module("cli")
        calls = []
        services = {"submitted": lambda **kwargs: calls.append(kwargs) or {"jobs": []}}
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["--user-dir", str(self.root), "submitted", "a,b,a"], services=services, stdout=out, stderr=err)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(calls, [{"root": self.root.resolve(), "selection": ["a", "b"]}])
        for value in ("all", "all-reviewed", "a,,b"):
            with self.subTest(value=value):
                code = cli.main(["--user-dir", str(self.root), "submitted", value], services=services, stdout=out, stderr=err)
                self.assertEqual(code, 2)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    import unittest

    unittest.main()
