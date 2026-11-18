"""`apply [N]` end to end (plan aa-19, flow 3.4).

Unit tests cover the run plan (dedup against the tracker, unsupported ATS to the
submit-by-hand list, reopening held tabs Chrome lost) and the closing table. The
integration test drives runtime.apply over the synthetic Greenhouse fixture with a
recording browser client: track, fetch, filter, resume, script driver, questions and
table, and proves no script or call could click a Submit control. People and
employers are invented.
"""

import copy
import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from test_support import RELEASE, ReleaseCase, candidate, job

from autoapply_local import apply_run, cli, script_driver
from autoapply_local.storage import Store

FIXTURE = json.loads((RELEASE / "tests" / "greenhouse_form.json").read_text(encoding="utf-8"))
GH = "https://job-boards.greenhouse.io/exampleco/jobs/"
ENDPOINT = "http://127.0.0.1:9440"


def gh_job(number, **changes):
    fields = {"company": "Example Co", "url": GH + str(number), "role": "Backend Software Engineering Intern"}
    return job(f"greenhouse:exampleco:{number}", **{**fields, **changes})


class RunPlanTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.store = Store(self.root)

    def test_count_picks_supported_pending_jobs_in_order(self):
        for number in (1, 2, 3):
            self.store.enqueue(gh_job(number))
        plan = apply_run.pick(self.store, 2)
        self.assertEqual(plan["selected"], ["greenhouse:exampleco:1", "greenhouse:exampleco:2"])
        self.assertEqual(plan["by_hand"], [])
        self.assertEqual(self.store.get("greenhouse:exampleco:3")["state"], "pending")

    def test_unsupported_ats_goes_to_the_by_hand_list_and_is_never_selected(self):
        self.store.enqueue(job("icims-1", ats="icims", url="https://careers-example.icims.com/jobs/1/job"))
        self.store.enqueue(gh_job(1))
        plan = apply_run.pick(self.store, 5)
        self.assertEqual(plan["selected"], ["greenhouse:exampleco:1"])
        self.assertEqual([item["id"] for item in plan["by_hand"]], ["icims-1"])
        self.assertEqual(self.store.get("icims-1")["state"], "by_hand")
        # The next run does not list or select it again.
        again = apply_run.pick(self.store, 5)
        self.assertEqual(again["by_hand"], [])

    def test_every_script_driver_ats_is_supported(self):
        for ats in ("greenhouse", "workday", "rippling", "ashby", "lever", "workable"):
            self.assertTrue(apply_run.supported({"ats": ats}), ats)
        for ats in ("icims", "smartrecruiters", "taleo", "", None):
            self.assertFalse(apply_run.supported({"ats": ats}), ats)

    def test_job_already_in_the_tracker_by_company_and_role_is_skipped(self):
        self.history("mail-1", "Backend Software Engineering Intern")
        self.store.enqueue(gh_job(1, role="Backend Software Engineering Intern (Summer 2030)"))
        self.store.enqueue(gh_job(2, role="Data Engineering Intern"))
        plan = apply_run.pick(self.store, 5)
        self.assertEqual(plan["selected"], ["greenhouse:exampleco:2"])
        self.assertEqual([(item["id"], item["reason"]) for item in plan["skipped"]],
                         [("greenhouse:exampleco:1", "already in the tracker")])
        self.assertEqual(self.store.get("greenhouse:exampleco:1")["state"], "skipped")

    def history(self, job_id, role, url="https://mail.google.com/mail/u/0/?th=t1#all/t1"):
        self.store.enqueue(job(job_id, company="Example Co", role=role, url=url))
        self.store.mark_submitted(job_id, {"status": "confirmed", "job_id": job_id, "channel": "gmail",
                                           "account": "gmail", "message_id": job_id, "text": "Gmail: thanks",
                                           "url": url})

    def test_a_job_the_tool_applied_to_at_another_url_is_not_a_duplicate(self):
        # Held-tab submissions carry their posting URL; the ledger dedups those by URL.
        self.history("applied", "Backend Software Engineering Intern", url=GH + "55")
        self.store.enqueue(gh_job(56))
        self.assertEqual(apply_run.pick(self.store, 5)["selected"], ["greenhouse:exampleco:56"])

    def test_named_job_ids_are_not_deduplicated(self):
        self.history("mail-1", "Backend Software Engineering Intern")
        self.store.enqueue(gh_job(1))
        plan = apply_run.pick(self.store, ids=["greenhouse:exampleco:1"])
        self.assertEqual((plan["selected"], plan["skipped"]), (["greenhouse:exampleco:1"], []))

    def test_explicit_ids_keep_held_jobs_and_send_unsupported_ones_by_hand(self):
        self.store.enqueue(job("icims-1", ats="icims", url="https://careers-example.icims.com/jobs/1/job"))
        self.store.enqueue(gh_job(1))
        plan = apply_run.pick(self.store, ids=["icims-1", "greenhouse:exampleco:1"])
        self.assertEqual(plan["selected"], ["greenhouse:exampleco:1"])
        self.assertEqual([item["id"] for item in plan["by_hand"]], ["icims-1"])

    def test_reopen_resets_lost_held_tabs_so_they_are_filled_again_first(self):
        self.store.enqueue(gh_job(1))
        self.store.update("greenhouse:exampleco:1", state="filled", tab_id="old-tab", lane="1", debug_url=ENDPOINT)
        self.store.enqueue(gh_job(2))
        reopened = apply_run.reopen(self.store, [{"job_id": "greenhouse:exampleco:1"}])
        self.assertEqual(reopened, ["greenhouse:exampleco:1"])
        record = self.store.get("greenhouse:exampleco:1")
        self.assertEqual((record["state"], record["tab_id"], record["previous_tab_id"]), ("pending", None, "old-tab"))
        plan = apply_run.pick(self.store, 1, first=reopened)
        # Reopened tabs come first and do not use up the count of new jobs.
        self.assertEqual(plan["selected"], ["greenhouse:exampleco:1", "greenhouse:exampleco:2"])

    def test_reopen_ignores_jobs_no_longer_held(self):
        self.store.enqueue(gh_job(1))
        self.assertEqual(apply_run.reopen(self.store, [{"job_id": "greenhouse:exampleco:1"}]), [])

    def test_questions_are_required_fields_with_no_confirmed_fact(self):
        record = {"id": "j1", "fill_result": {"unknown": [
            {"id": "q1", "label": "Why us?", "type": "textarea", "required": True, "options": [],
             "reason": "no_confirmed_fact"},
            {"id": "q2", "label": "Website", "type": "text", "required": False, "options": [],
             "reason": "no_confirmed_fact"},
            {"id": "q3", "label": "Location", "type": "combobox", "required": True, "options": [],
             "reason": "agent_interaction_required", "value": "Springfield"},
        ]}}
        self.assertEqual(apply_run.questions([record]),
                         [{"job_id": "j1", "id": "q1", "label": "Why us?", "type": "textarea", "options": []}])
        self.assertEqual(apply_run.fields_to_check(record), ["Why us?", "Location"])


class TableTests(unittest.TestCase):
    def test_table_lists_jobs_then_confirmations_skips_and_by_hand(self):
        report = {
            "rows": [{"job_id": "j1", "company": "Example Co", "role": "Backend Intern", "resume": "backend.pdf",
                      "tab": "tab-7", "state": "filled", "check": ["Why us?"]}],
            "track": {"submitted": [{"company": "Sample Labs", "role": "Data Intern", "via": "email"}],
                      "history_entries": [{"company": "Other Inc", "role": "ML Intern", "date": "2030-01-02"}]},
            "skipped": [{"id": "j2", "company": "Example Co", "role": "Intern", "reason": "already in the tracker"}],
            "fetch": {"report": {"summary": "First fetch: 3 jobs from 1 of 1 board; 2 passed your filters, "
                                            "1 filtered out (senior 1)."}},
            "by_hand": [{"id": "j3", "company": "Taleo Corp", "role": "SWE Intern",
                         "url": "https://example.taleo.net/j3", "resume": "main.pdf"}],
            "questions": {"pending": 1, "batch_id": "b1",
                          "next": "Run questions --ui UI, show its native popup once and pass the user's answers "
                                  "to questions --answer PATH"},
        }
        text = apply_run.table(report)
        lines = text.splitlines()
        header = next(line for line in lines if "Company" in line)
        for column in ("Company", "Role", "Resume", "Tab", "Fields to check"):
            self.assertIn(column, header)
        row = next(line for line in lines if "tab-7" in line)
        for cell in ("Example Co", "Backend Intern", "backend.pdf", "Why us?"):
            self.assertIn(cell, row)
        self.assertLess(text.index("tab-7"), text.index("Confirmations found in Gmail"))
        self.assertIn("Sample Labs", text)
        self.assertIn("Other Inc", text)
        self.assertIn("already in the tracker", text)
        self.assertIn("senior 1", text)
        by_hand = text[text.index("Submit by hand"):]
        for cell in ("Taleo Corp", "https://example.taleo.net/j3", "main.pdf"):
            self.assertIn(cell, by_hand)
        self.assertIn("1 question needs", text)
        self.assertIn("questions --ui UI", text)
        self.assertIn("Never submitted", text)

    def test_empty_run_still_says_nothing_was_opened(self):
        text = apply_run.table({"rows": [], "track": {}, "skipped": [], "by_hand": [],
                                 "questions": {"pending": 0, "batch_id": "b0"}})
        self.assertIn("No form was opened", text)


class RecordingClient:
    """A browser that only knows tab listing, opening, scripts and file upload; anything else fails."""

    def __init__(self):
        self.pages, self.scripts, self.calls, self.uploaded = [], [], [], set()
        self.counter = 0

    def __getattr__(self, name):
        raise AssertionError(f"browser call {name} is not allowed")

    def list_pages(self):
        self.calls.append("list_pages")
        return copy.deepcopy(self.pages)

    def new_page(self, url):
        self.calls.append("new_page")
        self.counter += 1
        page = {"id": f"tab-{self.counter}", "url": url}
        self.pages.append(page)
        return dict(page)

    def evaluate_script(self, tab_id, function, *, operation="audit"):
        self.calls.append("evaluate_script:" + operation)
        self.scripts.append(function)
        if operation == "fill":
            return {"filled": list(FIXTURE["answers"]), "blocked": [], "uploads": []}
        fields = copy.deepcopy(FIXTURE["scan"])
        for field in fields:
            if field["id"] == "resume" and str(tab_id) in self.uploaded:
                field["files"] = [dict(self.receipt)]
        return {"fields": fields, "errors": [], "text": ""}

    def upload_file(self, tab_id, path):
        import hashlib
        from pathlib import Path

        self.calls.append("upload_file")
        data = Path(path).read_bytes()
        self.receipt = {"name": Path(path).name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.uploaded.add(str(tab_id))


class ApplyEndToEndTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.write_json("profile.json", candidate("authorized"))
        self.pdf = self.root / "resumes" / "backend.pdf"
        self.pdf.parent.mkdir(parents=True)
        self.pdf.write_bytes(b"%PDF-1.4\nSynthetic resume for Alex Rivera\n%%EOF")
        self.write_json("config.json", {
            "runtime": {"apply_engine": "codex", "jobs_per_run": 2},
            "resumes": {"main_pdf": str(self.pdf), "main_tags": ["backend"]},
            "resources": {"lanes": 1, "held_tab_cap": 5},
            "boards": [{"ats": "greenhouse", "board": "exampleco", "company": "Example Co"}],
        })
        self.store = Store(self.root)
        self.client = RecordingClient()
        self.chrome_up = True
        self.launches = 0

    def pages(self, url):
        if not self.chrome_up:
            raise OSError("connection refused")
        return [{"id": page["id"], "url": page["url"], "type": "page"} for page in self.client.pages]

    def launch(self, root, **kwargs):
        self.launches += 1
        self.chrome_up = True
        return [{"lane": 1, "debug_url": ENDPOINT}]

    def run_apply(self, postings=(), **kwargs):
        from autoapply_local import browser, engines, runtime

        class Engine:
            def __init__(self, *args, **kw):
                self.requests = []

            def run(self, request):
                self.requests.append(request)
                return {"status": "completed"}

        err, real = io.StringIO(), browser.BrowserBridge
        with (
            patch("autoapply_local.fetch.Fetcher.fetch", return_value=[copy.deepcopy(p) for p in postings]),
            patch("autoapply_local.cdp.pages", side_effect=self.pages),
            patch("autoapply_local.track._gmail_rows", return_value=[]),
            patch.object(browser, "launch_chrome", side_effect=self.launch),
            patch.object(browser, "available_memory_mb", return_value=16384),
            patch.object(browser, "BrowserBridge", side_effect=lambda *a, **kw: real(self.client)),
            patch.object(engines, "Engine", side_effect=Engine),
            redirect_stderr(err),
        ):
            report = runtime.apply(root=self.root, **kwargs)
        return report, err.getvalue()

    def assert_never_clicked(self, report):
        for function in self.client.scripts:
            self.assertIsNone(script_driver.CLICKS.search(function), "a fill script could click or submit")
        self.assertTrue(set(self.client.calls) <= {"list_pages", "new_page", "evaluate_script:audit",
                                                  "evaluate_script:fill", "upload_file"}, self.client.calls)
        for record in self.store.jobs():
            self.assertNotIn(record["state"], ("submitted", "submit_intent"))
        self.assertNotIn("submit", json.dumps(report.get("rows", [])).casefold())

    def test_apply_n_fetches_filters_picks_resume_fills_and_prints_the_table(self):
        self.store.enqueue(job("icims-1", ats="icims", company="Taleo Corp",
                               url="https://careers-example.icims.com/jobs/1/job"))
        postings = [gh_job(n, role=f"Backend Software Engineering Intern {n}") for n in (101, 102, 103)]
        report, err = self.run_apply(postings, selection=None)
        # jobs_per_run (2) new forms; the third stays pending for the next run.
        # The fixture has required questions no fact answers: aa-17 holds those tabs for the popup.
        filled = [row for row in report["rows"] if row["state"] == "needs_manual"]
        self.assertEqual([row["job_id"] for row in filled], ["greenhouse:exampleco:101", "greenhouse:exampleco:102"])
        self.assertEqual(self.store.get("greenhouse:exampleco:103")["state"], "pending")
        self.assertEqual({row["resume"] for row in filled}, {"backend.pdf"})
        self.assertEqual(report["fetch"]["report"]["passed"], 3)
        self.assertEqual([item["id"] for item in report["by_hand"]], ["icims-1"])
        self.assertEqual(report["by_hand"][0]["resume"], "backend.pdf")
        labels = {item["label"] for item in report["unanswered"]}
        self.assertGreaterEqual(report["questions"]["pending"], 1)
        self.assertIn("Why do you want to join Example Co?", filled[0]["check"])
        self.assertIn("Why do you want to join Example Co?", labels)
        self.assertIn("Fields to check", err)
        self.assertIn("Submit by hand", err)
        self.assertIn("https://careers-example.icims.com/jobs/1/job", err)
        self.assertEqual(report["table"].strip(), err[err.index(report["table"].splitlines()[0]):].strip())
        self.assert_never_clicked(report)

    def test_tabs_lost_in_a_chrome_restart_are_reopened_and_refilled(self):
        self.store.enqueue(gh_job(7))
        self.store.update("greenhouse:exampleco:7", state="filled", tab_id="old-tab", lane="1", debug_url=ENDPOINT,
                          pdf=str(self.pdf))
        self.write_json("browser/active-lanes.json", [{"lane": 1, "debug_url": ENDPOINT}])
        self.chrome_up = False
        report, _ = self.run_apply(selection=1)
        self.assertGreaterEqual(self.launches, 1)
        self.assertEqual(report["reopened"], ["greenhouse:exampleco:7"])
        record = self.store.get("greenhouse:exampleco:7")
        self.assertEqual(record["state"], "needs_manual")
        self.assertTrue(record["unanswered"])
        self.assertNotEqual(record["tab_id"], "old-tab")
        self.assertEqual(record["previous_tab_id"], "old-tab")
        self.assertIn(record["url"], [page["url"] for page in self.client.pages])
        self.assert_never_clicked(report)

    def test_explicit_id_of_an_unsupported_job_opens_nothing(self):
        self.store.enqueue(job("taleo-1", ats="taleo", url="https://example.taleo.net/careersection/1"))
        report, err = self.run_apply(selection=["taleo-1"])
        self.assertEqual(self.client.calls.count("new_page"), 0)
        self.assertEqual([item["id"] for item in report["by_hand"]], ["taleo-1"])
        self.assertIn("No form was opened", err)


class ApplyCommandTests(unittest.TestCase):
    def invoke(self, *argv):
        calls = []
        services = {"apply": lambda **kw: calls.append(kw) or {}}
        code = cli.main(["--user-dir", "x", *argv], services=services, stdout=io.StringIO(), stderr=io.StringIO())
        return code, calls

    def test_apply_takes_an_optional_count_ids_and_engine(self):
        cases = {
            (): (None, None),
            ("3",): (3, None),
            ("codex", "3"): (3, "codex"),
            ("3", "claude"): (3, "claude"),
            ("job-1,job-2",): (["job-1", "job-2"], None),
            ("codex", "--quick", "2"): (2, "codex"),
        }
        for argv, (selection, engine) in cases.items():
            with self.subTest(argv=argv):
                code, calls = self.invoke("apply", *argv)
                self.assertEqual(code, 0)
                self.assertEqual((calls[0]["selection"], calls[0]["engine"]), (selection, engine))

    def test_bad_counts_and_two_selections_are_refused(self):
        for argv in (("0",), ("-1",), ("2", "3"), ("2", "--quick", "3")):
            with self.subTest(argv=argv):
                code, calls = self.invoke("apply", *argv)
                self.assertEqual(code, 2)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
