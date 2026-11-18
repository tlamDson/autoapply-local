"""Unknown required questions (plan aa-17), review fixes for PR #17.

Normal apply goes through the engine MCP client; refill validates before it
writes; a banked answer keeps the scope a profile answer needs; every native
popup call fits its tool's limits; a failed refill is retried from the bank.
Candidate, employer and questions are invented. Nothing here clicks Submit.
"""

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from test_support import NOW, ReleaseCase, candidate, job, snapshot  # noqa: E402
from test_unanswered import CLUB, HEAR, SHIFT, unknown  # noqa: E402

from autoapply_local import cli  # noqa: E402

SPONSOR = "Will you now or in the future require visa sponsorship?"
ENDPOINT = "http://127.0.0.1:9222"


class EnginePathTests(ReleaseCase):
    def engine_bridge(self, value):
        from autoapply_local.browser import BrowserBridge, EngineMCPClient

        engine = Mock(spec=["run"])
        engine.run.return_value = value
        client = EngineMCPClient(engine, ENDPOINT)
        client.list_pages = Mock(return_value=[{"id": "tab-1", "url": job()["url"]}])
        return BrowserBridge(client), engine

    def test_engine_fill_gets_scoped_bank_answers_and_reports_unknown_questions(self):
        bank = self.module("answer_bank").AnswerBank(self.root)
        bank.confirm({"label": CLUB, "type": "text"}, job("job-0"), "Robotics Society", source_id="s")
        bank.confirm({"label": HEAR, "type": "text"}, job("job-0", country="CA"), "Friend", source_id="s")
        bridge, engine = self.engine_bridge({"filled": ["name"], "blocked": [], "status": "needs_manual",
                                             "unknown": [unknown("question_1", HEAR)]})
        result = bridge.fill("tab-1", profile=candidate("authorized"), job=job(), bank=bank)
        task = engine.run.call_args.args[0]
        self.assertIn("unknown", task["schema"]["properties"])
        payload = json.loads(task["prompt"][task["prompt"].index("{"):])
        self.assertEqual([(item["question"], item["value"]) for item in payload["bank_answers"]],
                         [(CLUB, "Robotics Society")])
        self.assertIn("unknown", payload["instructions"])
        self.assertEqual(self.module("unanswered").pending_fields(result)[0]["id"], "question_1")

    def test_engine_needs_manual_result_with_questions_is_held_for_the_popup(self):
        workflow = self.module("workflow")
        self.write_json("profile.json", candidate("authorized"))
        pdf = self.root / "synthetic.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")
        browser = Mock(spec=["open", "fill", "upload_file", "snapshot", "refill", "close", "native_uploads", "answer_bank"])
        browser.native_uploads, browser.answer_bank = True, True
        browser.open.return_value = "tab-1"
        receipt = {"name": "synthetic.pdf", "size": 1, "sha256": "0" * 64}
        browser.fill.return_value = {"filled": [], "blocked": [{"id": "question_1", "reason": "unknown_question"}],
                                     "status": "needs_manual", "upload_receipt": receipt,
                                     "unknown": [unknown("question_1", HEAR)]}
        runner = workflow.Workflow(self.root, browser=browser, engines={"codex": self.fake_engine()},
                                   default_engine="codex", clock=lambda: NOW)
        runner.store.enqueue(job())
        result = runner.apply(["synthetic-1"], pdf=pdf)
        self.assertEqual(result["jobs"][0]["questions"], 1)
        self.assertIn("bank", browser.fill.call_args.kwargs)
        self.assertEqual(runner.store.get("synthetic-1")["upload_receipt"], receipt)
        self.assertEqual(self.module("unanswered").Questions(self.root).collect(runner.store.jobs())["pending"], 1)


class RefillValidationTests(ReleaseCase):
    def held(self):
        workflow = self.module("workflow")
        self.browser = Mock(spec=["refill", "snapshot"])
        self.browser.snapshot.return_value = snapshot()
        runner = workflow.Workflow(self.root, browser=self.browser, engines={}, default_engine="codex", clock=lambda: NOW)
        runner.store.enqueue(job())
        runner.store.update("synthetic-1", state="needs_manual", tab_id="tab-1",
                            unanswered=[unknown("question_1", HEAR), unknown("question_2", CLUB, "text"),
                                        unknown("question_3", SHIFT, "checkbox")])
        return runner

    def test_refill_rejects_bad_answers_before_writing_to_the_tab(self):
        runner = self.held()
        for answers in ({"name": "x"}, {"question_1": "99"}, {"question_1": ""}, {"question_2": "  "},
                        {"question_2": 5}, {"question_3": "yes"}, {"question_2": "x" * 1001}):
            with self.subTest(answers=answers), self.assertRaises(ValueError):
                runner.refill("synthetic-1", answers)
        self.browser.refill.assert_not_called()
        self.assertEqual(len(runner.store.get("synthetic-1")["unanswered"]), 3)

    def test_valid_answers_are_written(self):
        runner = self.held()
        self.browser.refill.return_value = {"filled": ["question_1", "question_2", "question_3"], "blocked": []}
        result = runner.refill("synthetic-1", {"question_1": "12", "question_2": "Robotics Society", "question_3": True})
        self.assertEqual(result["status"], "filled")

    def test_bridge_refill_checks_the_held_tab_before_writing(self):
        from autoapply_local.browser import BrowserBridge

        client = Mock(spec=["evaluate_script", "list_pages"])
        client.list_pages.return_value = []
        with self.assertRaises(ValueError):
            BrowserBridge(client).refill("tab-1", job=job(), answers={"question_2": "x"})
        client.evaluate_script.assert_not_called()


class ScopeTests(ReleaseCase):
    def test_bank_keeps_job_type_scope_and_full_scope_for_sensitive_questions(self):
        bank = self.module("answer_bank").AnswerBank(self.root)
        text = {"label": CLUB, "type": "text"}
        bank.confirm(text, job(), "Robotics Society", source_id="s")
        self.assertIsNone(bank.lookup(text, job(level="full_time")))
        self.assertIsNotNone(bank.lookup(text, job("other", company="Another Co")))
        sponsor = {"label": SPONSOR, "type": "radio",
                   "options": [{"value": "y", "label": "Yes"}, {"value": "n", "label": "No"}]}
        bank.confirm(sponsor, {**job(), "start_date": "2030-06-01"}, "Yes", source_id="s")
        self.assertEqual(bank.lookup(sponsor, {**job("again"), "start_date": "2030-06-01"})["value"], "Yes")
        self.assertIsNone(bank.lookup(sponsor, {**job("other"), "company": "Another Co", "start_date": "2030-06-01"}))
        self.assertIsNone(bank.lookup(sponsor, {**job("later"), "start_date": "2031-06-01"}))


class NativePacketTests(ReleaseCase):
    def test_native_packets_are_valid_for_every_ui(self):
        many = [{"value": str(n), "label": f"Option {n}"} for n in range(6)]
        dup = [{"value": "a", "label": "Same"}, {"value": "b", "label": "Same"}]
        three = [{"value": str(n), "label": f"Pick {n}"} for n in range(3)]
        records = [{**job("job-a"), "state": "needs_manual", "tab_id": "tab-a", "unanswered": [
            unknown("f1", "Pick a lot of things?", options=many), unknown("f2", "Duplicates?", options=dup),
            unknown("f3", "Three picks?", options=three), unknown("f4", "Nothing to pick?", options=[]),
            unknown("f5", "Long option?", options=[{"value": "z", "label": "word " * 30}, {"value": "y", "label": "Short"}]),
            unknown("f6", CLUB, "text"), unknown("f7", SHIFT, "checkbox")]}]
        questions = self.module("unanswered").Questions(self.root)
        questions.collect(records)
        for ui, (per_call, per_question) in (("claude", (4, 4)), ("codex", (3, 3)), ("codex-async", (None, None))):
            packet = questions.packet(ui)
            natives = [item for call in packet["ui"]["calls"] for item in call["questions"]]
            self.assertEqual(len(packet["ui"]["calls"]), 1)
            self.assertEqual(len(natives), min(7, per_call or 7))
            self.assertEqual(len(natives) + packet["deferred"], 7)
            for item in natives:
                labels = item["options"] if ui == "codex-async" else [option["label"] for option in item["options"]]
                with self.subTest(ui=ui, item=item.get("question", item.get("title"))):
                    self.assertGreaterEqual(len(labels), 2)
                    self.assertLessEqual(len(labels), per_question or 99)
                    self.assertEqual(len(set(labels)), len(labels))
                    self.assertTrue(all(0 < len(label) <= 60 for label in labels))
                    if ui != "codex-async":
                        self.assertLessEqual(len(item["header"]), 12)
            for binding in packet["ui"]["bindings"]:
                self.assertEqual(len(binding["option_ids"]), len(set(binding["option_ids"])))
        packet = questions.packet("claude")
        pick = next(item for item in packet["questions"] if item["label"] == "Pick a lot of things?")
        path = self.root / "r.json"
        path.write_text(json.dumps({"batch_id": packet["batch_id"], "answers": [
            {"question_id": pick["id"], "text": "option 4"}]}), encoding="utf-8")
        self.assertEqual(questions.answer(path)["refill"], {"job-a": {"f1": "4"}})

    def test_duplicate_option_labels_cannot_be_answered_ambiguously(self):
        dup = [{"value": "a", "label": "Same"}, {"value": "b", "label": "Same"}]
        questions = self.module("unanswered").Questions(self.root)
        questions.collect([{**job("job-a"), "state": "needs_manual", "tab_id": "tab-a",
                            "unanswered": [unknown("f2", "Duplicates?", options=dup)]}])
        packet = questions.packet("claude")
        path = self.root / "r.json"
        path.write_text(json.dumps({"batch_id": packet["batch_id"], "answers": [
            {"question_id": packet["questions"][0]["id"], "text": "Same"}]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            questions.answer(path)


class RetryTests(ReleaseCase):
    def test_failed_refill_is_retried_from_the_bank(self):
        from autoapply_local import runtime
        from autoapply_local.storage import Store

        store = Store(self.root)
        store.enqueue(job("job-a"))
        store.update("job-a", state="needs_manual", tab_id="tab-a", unanswered=[unknown("question_1", HEAR)])
        packet = runtime.questions(root=self.root, ui="claude")
        response = self.root / "response.json"
        response.write_text(json.dumps({"batch_id": packet["batch_id"], "answers": [
            {"question_id": packet["questions"][0]["id"], "text": "Friend"}]}), encoding="utf-8")
        runner = Mock()
        runner.refill.side_effect = [RuntimeError("Native Chrome target disappeared"),
                                     {"job_id": "job-a", "status": "filled", "tab_id": "tab-a"}]
        with patch.object(runtime, "_workflow", return_value=(runner, {}, "codex")):
            failed = runtime.questions(root=self.root, answer=response)
            self.assertEqual(failed["refilled"][0]["status"], "needs_manual")
            self.assertEqual(runtime.questions(root=self.root, ui="claude")["refill_ready"], 1)
            retried = runtime.questions(root=self.root, refill=True)
        self.assertEqual(runner.refill.call_args_list[-1].args, ("job-a", {"question_1": "12"}))
        self.assertEqual(retried["refilled"], [{"job_id": "job-a", "status": "filled", "tab_id": "tab-a"}])

    def test_cli_refill_flag(self):
        calls = []
        services = {"questions": lambda **kwargs: calls.append(kwargs) or {}}
        self.assertEqual(cli.main(["--user-dir", ".", "questions", "--refill"], services, Mock(), Mock()), 0)
        self.assertIs(calls[0]["refill"], True)
        self.assertEqual(cli.main(["--user-dir", ".", "questions", "--refill", "--answer", "r.json"],
                                  services, Mock(), Mock()), 2)
