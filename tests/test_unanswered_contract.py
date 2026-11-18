"""Unknown required questions (plan aa-17), second review of PR #17.

The engine is told to report unknown questions and keep filling; Workday tabs
are refilled through native engine interaction; a refill never writes to a tab
that navigated away; pay questions keep the employer and start date; a popup
is one native call with unique buttons; a refill whose readback is not clean
stays retryable. Candidate, employer and questions are invented. Nothing here
clicks Submit.
"""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from test_support import NOW, ReleaseCase, candidate, job, snapshot  # noqa: E402
from test_unanswered import CLUB, HEAR, SHIFT, unknown  # noqa: E402

ENDPOINT = "http://127.0.0.1:9222"


def engine_bridge(value, url=None):
    from autoapply_local.browser import BrowserBridge, EngineMCPClient

    engine = Mock(spec=["run"])
    engine.run.return_value = value
    client = EngineMCPClient(engine, ENDPOINT)
    client.list_pages = Mock(return_value=[{"id": "tab-1", "url": url or job()["url"]}])
    return BrowserBridge(client), engine


def task_payload(engine):
    task = engine.run.call_args.args[0]
    return task, json.loads(task["prompt"][task["prompt"].index("{"):])


class EngineCollectionTests(ReleaseCase):
    def test_engine_is_told_to_list_unknown_questions_and_keep_filling(self):
        bridge, engine = engine_bridge({"filled": [], "blocked": [], "unknown": [], "status": "needs_manual"})
        bridge.fill("tab-1", profile=candidate("authorized"), job=job())
        task, payload = task_payload(engine)
        rules = payload["instructions"]
        self.assertIn("List every required question no confirmed fact or bank answer covers in unknown", rules)
        self.assertIn("keep filling the other fields and upload the resume", rules)
        self.assertNotIn("unknown questions before upload", rules)
        self.assertNotRegex(rules, r"(?i)empty unknown|omit .*unknown|unknown\s*=\s*\[\]")
        self.assertIn("unknown", task["schema"]["required"])
        item = task["schema"]["properties"]["unknown"]["items"]
        self.assertEqual(set(item["required"]), {"id", "label", "type", "required", "options"})

    def test_unknown_entries_without_an_id_or_label_are_refused(self):
        bridge, _ = engine_bridge({"filled": [], "blocked": [], "status": "needs_manual",
                                   "unknown": [{"label": HEAR, "type": "select", "required": True, "options": []}]})
        with self.assertRaises(ValueError):
            bridge.fill("tab-1", profile=candidate("authorized"), job=job())


class WorkdayRefillTests(ReleaseCase):
    def test_workday_refill_goes_through_native_engine_interaction(self):
        bridge, engine = engine_bridge({"filled": ["question_1"], "blocked": [], "status": "filled"},
                                       url=job(ats="workday")["url"])
        result = bridge.refill("tab-1", job=job(ats="workday"), answers={"question_1": "12"})
        self.assertEqual(result["filled"], ["question_1"])
        task, payload = task_payload(engine)
        self.assertEqual(task["kind"], "browser_native_refill")
        self.assertEqual(payload["answers"], {"question_1": "12"})
        self.assertIn("Never click Submit", payload["instructions"])
        self.assertNotIn("mcp__autoapply-local-browser__upload_file", task["allowed_tools"])
        self.assertNotIn("mcp__autoapply-local-browser__new_page", task["allowed_tools"])

    def test_workday_refill_without_the_engine_is_refused_before_writing(self):
        from autoapply_local.browser import BrowserBridge

        client = Mock(spec=["evaluate_script", "list_pages"])
        client.list_pages.return_value = [{"id": "tab-1", "url": job(ats="workday")["url"]}]
        with self.assertRaises(ValueError):
            BrowserBridge(client).refill("tab-1", job=job(ats="workday"), answers={"question_1": "12"})
        client.evaluate_script.assert_not_called()


class NavigatedTabTests(ReleaseCase):
    def test_refill_refuses_a_tab_showing_another_application(self):
        from autoapply_local.browser import BrowserBridge

        client = Mock(spec=["evaluate_script", "list_pages"])
        client.list_pages.return_value = [{"id": "tab-1", "url": "https://jobs.example.invalid/another-job"}]
        with self.assertRaises(ValueError):
            BrowserBridge(client).refill("tab-1", job=job(), answers={"question_2": "x"})
        client.evaluate_script.assert_not_called()

    def test_engine_refill_refuses_a_navigated_tab(self):
        bridge, engine = engine_bridge({"filled": [], "blocked": [], "status": "filled"},
                                       url="https://jobs.example.invalid/another-job")
        with self.assertRaises(ValueError):
            bridge.refill("tab-1", job=job(ats="workday"), answers={"question_1": "12"})
        engine.run.assert_not_called()


class PayScopeTests(ReleaseCase):
    def test_pay_questions_keep_employer_and_start_date(self):
        bank = self.module("answer_bank").AnswerBank(self.root)
        start = {**job(), "start_date": "2030-06-01"}
        for label in ("What hourly rate do you expect?", "Desired wage?", "Expected stipend (USD)?",
                      "What are your pay expectations?", "Minimum acceptable rate per hour?"):
            field = {"label": label, "type": "text"}
            bank.confirm(field, start, "40", source_id="s")
            with self.subTest(label=label):
                self.assertEqual(bank.lookup(field, {**start, "id": "again"})["value"], "40")
                self.assertIsNone(bank.lookup(field, {**start, "company": "Another Co"}))
                self.assertIsNone(bank.lookup(field, {**start, "start_date": "2031-06-01"}))

    def test_pay_ranges_in_the_options_keep_the_full_scope(self):
        bank = self.module("answer_bank").AnswerBank(self.root)
        field = {"label": "Which range fits you?", "type": "select",
                 "options": [{"value": "a", "label": "$20-$30"}, {"value": "b", "label": "$30-$40"}]}
        bank.confirm(field, job(), "$20-$30", source_id="s")
        self.assertIsNone(bank.lookup(field, job(company="Another Co")))

    def test_an_ordinary_question_is_still_reused_by_the_next_employer(self):
        bank = self.module("answer_bank").AnswerBank(self.root)
        field = {"label": CLUB, "type": "text"}
        bank.confirm(field, job(), "Robotics Society", source_id="s")
        self.assertEqual(bank.lookup(field, job("next", company="Another Co"))["value"], "Robotics Society")


class OnePopupTests(ReleaseCase):
    def records(self, count):
        fields = [unknown(f"extra_{n}", f"Extra question {n}?", "text") for n in range(count)]
        return [{**job("job-a"), "state": "needs_manual", "tab_id": "tab-a", "unanswered": fields}]

    def test_every_ui_gets_exactly_one_native_call(self):
        questions = self.module("unanswered").Questions(self.root)
        questions.collect(self.records(7))
        for ui, per_call in (("claude", 4), ("codex", 3), ("codex-async", 7)):
            packet = questions.packet(ui)
            with self.subTest(ui=ui):
                self.assertEqual(len(packet["ui"]["calls"]), 1)
                self.assertEqual(len(packet["ui"]["calls"][0]["questions"]), per_call)
                self.assertEqual(len(packet["questions"]), per_call)
                self.assertEqual(len(packet["ui"]["bindings"]), per_call)
                self.assertEqual(packet["deferred"], 7 - per_call)

    def test_deferred_questions_come_in_the_next_popup(self):
        questions = self.module("unanswered").Questions(self.root)
        questions.collect(self.records(5))
        packet = questions.packet("claude")
        path = self.root / "r.json"
        path.write_text(json.dumps({"batch_id": packet["batch_id"], "answers": [
            {"question_id": item["id"], "text": "Answer"} for item in packet["questions"]]}), encoding="utf-8")
        self.assertEqual(questions.answer(path)["pending"], 1)
        later = questions.packet("claude")
        self.assertEqual([item["label"] for item in later["questions"]], ["Extra question 4?"])
        self.assertEqual(later["deferred"], 0)

    def test_questions_asked_by_more_jobs_come_first(self):
        questions = self.module("unanswered").Questions(self.root)
        records = self.records(4)
        records.append({**job("job-b"), "state": "needs_manual", "tab_id": "tab-b",
                        "unanswered": [unknown("q9", "Extra question 3?", "text")]})
        questions.collect(records)
        self.assertEqual(questions.packet("codex")["questions"][0]["label"], "Extra question 3?")

    def test_an_option_named_like_a_skip_button_never_duplicates_a_button(self):
        questions = self.module("unanswered").Questions(self.root)
        options = [{"value": "a", "label": "Yes"}, {"value": "b", "label": "Skip for now"}]
        questions.collect([{**job("job-a"), "state": "needs_manual", "tab_id": "tab-a",
                            "unanswered": [unknown("f1", "Start now?", options=options)]}])
        for ui in ("claude", "codex", "codex-async"):
            packet = questions.packet(ui)
            item = packet["ui"]["calls"][0]["questions"][0]
            labels = item["options"] if ui == "codex-async" else [option["label"] for option in item["options"]]
            with self.subTest(ui=ui):
                self.assertEqual(len({label.casefold() for label in labels}), len(labels))
        packet = questions.packet("claude")
        path = self.root / "r.json"
        path.write_text(json.dumps({"batch_id": packet["batch_id"], "answers": [
            {"question_id": packet["questions"][0]["id"], "text": "Skip for now"}]}), encoding="utf-8")
        self.assertEqual(questions.answer(path)["refill"], {"job-a": {"f1": "b"}})


class ReadbackRetryTests(ReleaseCase):
    def held(self):
        workflow = self.module("workflow")
        self.browser = Mock(spec=["refill", "snapshot"])
        runner = workflow.Workflow(self.root, browser=self.browser, engines={}, default_engine="codex", clock=lambda: NOW)
        runner.store.enqueue(job())
        runner.store.update("synthetic-1", state="needs_manual", tab_id="tab-1",
                            unanswered=[unknown("question_1", HEAR), unknown("question_2", CLUB, "text"),
                                        unknown("question_3", SHIFT, "checkbox")])
        return runner

    def test_unclean_readback_keeps_every_field_for_the_next_refill(self):
        runner = self.held()
        self.browser.refill.return_value = {"filled": ["question_1", "question_2"], "blocked": []}
        self.browser.snapshot.return_value = snapshot(required_unknown=["question_1"])
        result = runner.refill("synthetic-1", {"question_1": "12", "question_2": "Robotics Society"})
        self.assertEqual(result["status"], "needs_manual")
        kept = [item["id"] for item in runner.store.get("synthetic-1")["unanswered"]]
        self.assertEqual(kept, ["question_1", "question_2", "question_3"])

    def test_validation_errors_keep_every_field(self):
        runner = self.held()
        self.browser.refill.return_value = {"filled": ["question_1", "question_2", "question_3"], "blocked": []}
        self.browser.snapshot.return_value = snapshot(errors=["question_2 is required"])
        runner.refill("synthetic-1", {"question_1": "12", "question_2": "Robotics Society", "question_3": True})
        self.assertEqual(len(runner.store.get("synthetic-1")["unanswered"]), 3)
        self.assertEqual(runner.store.get("synthetic-1")["state"], "needs_manual")

    def test_failed_readback_keeps_the_tab_retryable(self):
        runner = self.held()
        self.browser.refill.return_value = {"filled": ["question_1"], "blocked": []}
        self.browser.snapshot.side_effect = ValueError("Live form readback unavailable")
        result = runner.refill("synthetic-1", {"question_1": "12"})
        self.assertEqual(result["status"], "needs_manual")
        self.assertIn("readback", result["reason"])
        record = runner.store.get("synthetic-1")
        self.assertEqual(len(record["unanswered"]), 3)
        bank = self.module("answer_bank").AnswerBank(self.root)
        bank.confirm(unknown("question_1", HEAR), record, "Friend", source_id="s")
        plan = self.module("unanswered").Questions(self.root).refill_plan([record])
        self.assertEqual(plan, {"synthetic-1": {"question_1": "12"}})


class DispatchIdentityTests(ReleaseCase):
    """The tab can navigate between the bridge's URL check and the engine dispatch."""

    def moving_bridge(self, ats):
        from autoapply_local.browser import BrowserBridge, EngineMCPClient

        engine = Mock(spec=["run"])
        engine.run.return_value = {"filled": ["question_1"], "blocked": [], "status": "filled"}
        client = EngineMCPClient(engine, ENDPOINT)
        reads = []

        def pages():
            reads.append(1)
            url = job(ats=ats)["url"] if len(reads) == 1 else job("synthetic-2", ats=ats)["url"]
            return [{"id": "tab-1", "url": url}]

        client.list_pages = Mock(side_effect=pages)
        return BrowserBridge(client), engine

    def test_native_refill_rejects_a_tab_that_moved_after_the_check(self):
        bridge, engine = self.moving_bridge("workday")
        with self.assertRaisesRegex(ValueError, "another application"):
            bridge.refill("tab-1", job=job(ats="workday"), answers={"question_1": "12"})
        engine.run.assert_not_called()

    def test_script_refill_rejects_a_tab_that_moved_after_the_check(self):
        bridge, engine = self.moving_bridge("greenhouse")
        with self.assertRaisesRegex(ValueError, "another application"):
            bridge.refill("tab-1", job=job(), answers={"question_2": "Robotics Society"})
        engine.run.assert_not_called()

    def test_the_write_carries_the_verified_application_url(self):
        for ats, answers in (("workday", {"question_1": "12"}), ("greenhouse", {"question_2": "Robotics Society"})):
            bridge, engine = engine_bridge({"filled": [], "blocked": [], "status": "filled", "result": {}},
                                           url=job(ats=ats)["url"])
            bridge.refill("tab-1", job=job(ats=ats), answers=answers)
            _, payload = task_payload(engine)
            with self.subTest(ats=ats):
                self.assertEqual(payload["expected_url"], job(ats=ats)["url"])
                self.assertIn("expected_url", payload["target_instructions"])


class WorkEligibilityScopeTests(ReleaseCase):
    def test_work_eligibility_answers_keep_employer_and_start_date(self):
        bank = self.module("answer_bank").AnswerBank(self.root)
        yes_no = [{"value": "1", "label": "Yes"}, {"value": "0", "label": "No"}]
        may = {**job(), "start_date": "2030-05-01"}
        for label in ("Are you legally eligible to work in the United States?",
                      "Are you eligible to work in the US without restriction?",
                      "Are you lawfully permitted to work in this country?",
                      "Are you a U.S. citizen or permanent resident?"):
            field = {"label": label, "type": "select", "options": yes_no}
            bank.confirm(field, may, "Yes", source_id="s")
            with self.subTest(label=label):
                self.assertEqual(bank.lookup(field, {**may, "id": "again"})["value"], "Yes")
                for other in ({**may, "company": "Another Co"}, {**may, "start_date": "2030-08-01"},
                              {**may, "company": "Another Co", "start_date": "2030-08-01"}):
                    self.assertIsNone(bank.lookup(field, other))
                    self.assertNotIn(label, [item["question"] for item in bank.entries_for(other)])
