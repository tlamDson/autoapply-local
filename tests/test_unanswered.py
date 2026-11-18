"""Unknown required questions (plan aa-17): collect per run, one popup, answer bank, refill.

Required questions no confirmed fact answers are collected across the whole
apply run, asked once through a native popup packet, saved to the answer bank
in the runtime root and filled into the held tabs. The next job that asks the
same question is filled from the bank without asking again. Nothing here
clicks Submit. Candidate, employer and questions are invented.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from test_support import NOW, ReleaseCase, candidate, job, snapshot  # noqa: E402

from autoapply_local import cli, script_driver  # noqa: E402
from autoapply_local.greenhouse_driver import GreenhouseDriver  # noqa: E402

HEAR = "How did you hear about this role?"
CLUB = "Which robotics club did you lead?"
SHIFT = "Can you work a weekend shift?"
OPTIONS = [{"value": "", "label": "Select..."}, {"value": "11", "label": "Job board"}, {"value": "12", "label": "Friend"}]


def unknown(field_id, label, kind="select", options=None, **extra):
    return {"id": field_id, "label": label, "type": kind, "required": True,
            "options": OPTIONS if options is None and kind == "select" else options or [],
            "reason": "no_confirmed_fact", **extra}


def fill_result(*items):
    return {"driver": "greenhouse", "filled": ["first_name"], "blocked": [], "uploads": [],
            "unknown": list(items), "status": "needs_engine"}


class AnswerBankTests(ReleaseCase):
    def bank(self):
        return self.module("answer_bank").AnswerBank(self.root)

    def test_confirmed_answer_persists_in_the_runtime_root_and_reloads(self):
        field = {"id": "question_1", "label": HEAR, "type": "select", "options": OPTIONS}
        self.assertIsNone(self.bank().lookup(field, job()))
        self.bank().confirm(field, job(), "Friend", source_id="unanswered:batch-1")
        path = self.root / "user/answer-bank.json"
        self.assertTrue(path.is_file())
        found = self.bank().lookup({**field, "id": "question_9", "label": "  how did you HEAR about this role? "}, job("other"))
        self.assertEqual(found["value"], "Friend")
        self.assertEqual(found["status"], "confirmed")
        self.assertEqual(found["provenance"], {"kind": "user_confirmation", "source_id": "unanswered:batch-1"})
        self.assertTrue(found["confirmed_at"])

    def test_a_different_option_set_country_or_kind_is_not_the_same_question(self):
        field = {"id": "q", "label": HEAR, "type": "select", "options": OPTIONS}
        self.bank().confirm(field, job(), "Friend", source_id="s")
        other_options = {**field, "options": OPTIONS[:2] + [{"value": "13", "label": "Career fair"}]}
        self.assertIsNone(self.bank().lookup(other_options, job()))
        self.assertIsNone(self.bank().lookup(field, job(country="CA")))
        self.assertIsNone(self.bank().lookup({**field, "type": "text", "options": []}, job()))

    def test_newer_confirmation_replaces_the_older_one(self):
        field = {"id": "q", "label": CLUB, "type": "text", "options": []}
        self.bank().confirm(field, job(), "Chess", source_id="s1")
        self.bank().confirm(field, job(), "Robotics Society", source_id="s2")
        self.assertEqual(self.bank().lookup(field, job())["value"], "Robotics Society")
        self.assertEqual(len(json.loads((self.root / "user/answer-bank.json").read_text(encoding="utf-8"))["answers"]), 1)

    def test_corrupt_bank_is_refused(self):
        (self.root / "user").mkdir()
        (self.root / "user/answer-bank.json").write_text('{"answers": "nope"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.bank().lookup({"id": "q", "label": CLUB, "type": "text"}, job())


class ReuseOnNextJobTests(ReleaseCase):
    def test_driver_fills_a_banked_answer_on_the_next_job_without_asking(self):
        bank = self.module("answer_bank").AnswerBank(self.root)
        fields = [
            {"id": "question_5", "label": HEAR, "type": "select", "required": True, "options": OPTIONS, "value": ""},
            {"id": "question_6", "label": CLUB, "type": "text", "required": True, "options": [], "value": ""},
            {"id": "question_7", "label": SHIFT, "type": "checkbox", "required": True, "options": [], "value": False},
        ]
        profile = candidate("authorized")
        before = GreenhouseDriver().plan(fields, profile, job("first"), bank=bank)
        self.assertEqual([item["id"] for item in before["unknown"]], ["question_5", "question_6", "question_7"])
        bank.confirm(fields[0], job("first"), "Friend", source_id="s")
        bank.confirm(fields[1], job("first"), "Robotics Society", source_id="s")
        bank.confirm(fields[2], job("first"), True, source_id="s")
        after = GreenhouseDriver().plan(fields, profile, job("second"), bank=bank)
        self.assertEqual(after["unknown"], [])
        self.assertEqual(after["answers"], {"question_5": "12", "question_6": "Robotics Society", "question_7": True})

    def test_plan_without_a_bank_is_unchanged(self):
        fields = [{"id": "question_6", "label": CLUB, "type": "text", "required": True, "options": [], "value": ""}]
        plan = GreenhouseDriver().plan(fields, candidate("authorized"), job())
        self.assertEqual([item["id"] for item in plan["unknown"]], ["question_6"])


class CollectTests(ReleaseCase):
    def module_(self):
        return self.module("unanswered")

    def test_only_required_answerable_fields_are_collected(self):
        items = self.module_().pending_fields(fill_result(
            unknown("q1", HEAR),
            {**unknown("q2", CLUB, "text"), "required": False},
            unknown("cover", "Cover letter", "file"),
            {**unknown("q3", "School", "combobox"), "reason": "agent_interaction_required", "value": "Example U"},
            unknown("q4", SHIFT, "checkbox"),
        ))
        self.assertEqual([item["id"] for item in items], ["q1", "q4"])
        self.assertEqual(self.module_().pending_fields({"status": "filled"}), [])

    def records(self):
        return [
            {**job("job-a"), "state": "needs_manual", "tab_id": "tab-a",
             "unanswered": [unknown("question_1", HEAR), unknown("question_2", CLUB, "text")]},
            {**job("job-b"), "state": "needs_manual", "tab_id": "tab-b",
             "unanswered": [unknown("q_77", " how did you hear about this ROLE? ",
                                    options=[{"value": "a", "label": "Friend"}, {"value": "b", "label": "Job board"}])]},
            {**job("job-c"), "state": "submitted", "tab_id": "tab-c", "unanswered": [unknown("x", "Ignored")]},
        ]

    def test_one_batch_for_the_whole_run_with_shared_questions_deduplicated(self):
        questions = self.module_().Questions(self.root)
        summary = questions.collect(self.records())
        self.assertEqual(summary["pending"], 2)
        saved = json.loads((self.root / "user/unanswered-questions.json").read_text(encoding="utf-8"))
        by_label = {item["label"]: item for item in saved["questions"]}
        self.assertEqual(set(by_label), {HEAR, CLUB})
        self.assertEqual([(t["job_id"], t["field_id"]) for t in by_label[HEAR]["targets"]],
                         [("job-a", "question_1"), ("job-b", "q_77")])
        self.assertEqual(by_label[HEAR]["options"], ["Job board", "Friend"])

    def test_batch_persists_and_collecting_again_keeps_its_id(self):
        first = self.module_().Questions(self.root).collect(self.records())
        again = self.module_().Questions(self.root).collect(self.records())
        self.assertEqual(first["batch_id"], again["batch_id"])
        changed = self.module_().Questions(self.root).collect(self.records()[:1])
        self.assertNotEqual(first["batch_id"], changed["batch_id"])

    def test_banked_question_is_not_asked_again(self):
        self.module("answer_bank").AnswerBank(self.root).confirm(
            {"label": CLUB, "type": "text", "options": []}, job("job-a"), "Robotics Society", source_id="s")
        summary = self.module_().Questions(self.root).collect(self.records())
        self.assertEqual(summary["pending"], 1)


class PacketAndAnswerTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.questions = self.module("unanswered").Questions(self.root)
        records = [
            {**job("job-a"), "state": "needs_manual", "tab_id": "tab-a",
             "unanswered": [unknown("question_1", HEAR), unknown("question_2", CLUB, "text"),
                            unknown("question_3", SHIFT, "checkbox")]},
            {**job("job-b"), "state": "needs_manual", "tab_id": "tab-b",
             "unanswered": [unknown("q_77", HEAR, options=[{"value": "a", "label": "Friend"},
                                                           {"value": "b", "label": "Job board"}])]},
        ]
        for index in range(3):
            records[0]["unanswered"].append(unknown(f"extra_{index}", f"Extra question {index}?", "text"))
        self.summary = self.questions.collect(records)

    def ids(self, packet, label):
        return next(item for item in packet["questions"] if item["label"] == label)

    def test_claude_packet_is_one_popup_of_tool_size(self):
        packet = self.questions.packet("claude")
        self.assertEqual(packet["status"], "questions")
        self.assertEqual(packet["batch_id"], self.summary["batch_id"])
        self.assertEqual(packet["ui"]["tool"], "AskUserQuestion")
        calls = packet["ui"]["calls"]
        self.assertEqual([len(call["questions"]) for call in calls], [4])
        self.assertEqual(packet["deferred"], 2)
        for call in calls:
            for item in call["questions"]:
                self.assertTrue(2 <= len(item["options"]) <= 4)
                self.assertIn("multiSelect", item)
        self.assertEqual(len(packet["ui"]["bindings"]), 4)
        hear = self.ids(packet, HEAR)
        self.assertEqual([choice["label"] for choice in hear["choices"]], ["Job board", "Friend", "Skip for now"])

    def test_codex_packet_has_at_most_three_questions_per_call(self):
        packet = self.questions.packet("codex")
        self.assertEqual(packet["ui"]["tool"], "request_user_input")
        self.assertEqual([len(call["questions"]) for call in packet["ui"]["calls"]], [3])
        self.assertTrue(all(item["id"].startswith("q") for call in packet["ui"]["calls"] for item in call["questions"]))

    def test_no_native_tool_keeps_the_batch(self):
        packet = self.questions.packet("none")
        self.assertEqual(packet["status"], "ui_required")
        self.assertEqual(packet["pending"], 6)
        self.assertEqual(self.questions.packet("claude")["batch_id"], self.summary["batch_id"])

    def test_nothing_pending_needs_no_popup(self):
        empty = self.module("unanswered").Questions(self.root / "empty")
        empty.collect([])
        self.assertEqual(empty.packet("claude")["status"], "none")

    def respond(self, packet, answers, batch=None):
        path = self.root / "response.json"
        path.write_text(json.dumps({"batch_id": batch or packet["batch_id"], "answers": answers}), encoding="utf-8")
        return path

    def test_answers_go_to_the_bank_and_map_to_each_tabs_own_option_values(self):
        packet = self.questions.packet("claude")
        hear, club, shift = (self.ids(packet, label) for label in (HEAR, CLUB, SHIFT))
        friend = next(c["id"] for c in hear["choices"] if c["label"] == "Friend")
        yes = next(c["id"] for c in shift["choices"] if c["label"] == "Yes")
        result = self.questions.answer(self.respond(packet, [
            {"question_id": hear["id"], "option_id": friend},
            {"question_id": club["id"], "text": "  Robotics Society "},
            {"question_id": shift["id"], "option_id": yes},
        ]))
        self.assertEqual(result["saved"], 3)
        self.assertEqual(result["pending"], 3)
        self.assertEqual(result["refill"], {
            "job-a": {"question_1": "12", "question_2": "Robotics Society", "question_3": True},
            "job-b": {"q_77": "a"},
        })
        bank = self.module("answer_bank").AnswerBank(self.root)
        self.assertEqual(bank.lookup({"label": CLUB, "type": "text"}, job("job-z"))["value"], "Robotics Society")
        self.assertIsNone(bank.lookup({"label": "Extra question 0?", "type": "text"}, job("job-z")))
        left = self.questions.packet("claude")
        self.assertEqual(len(left["questions"]), 3)
        self.assertNotEqual(left["batch_id"], packet["batch_id"])

    def test_typed_text_must_match_an_option_of_a_choice_question(self):
        packet = self.questions.packet("claude")
        hear = self.ids(packet, HEAR)
        result = self.questions.answer(self.respond(packet, [{"question_id": hear["id"], "text": "friend"}]))
        self.assertEqual(result["refill"]["job-a"], {"question_1": "12"})
        packet = self.questions.packet("claude")
        with self.assertRaisesRegex(ValueError, "option"):
            self.questions.answer(self.respond(packet, [{"question_id": self.ids(packet, SHIFT)["id"], "text": "Maybe"}]))

    def test_skip_is_not_an_answer_and_keeps_the_question(self):
        packet = self.questions.packet("claude")
        club = self.ids(packet, CLUB)
        skip = next(c["id"] for c in club["choices"] if c["label"] == "Skip for now")
        result = self.questions.answer(self.respond(packet, [{"question_id": club["id"], "option_id": skip}]))
        self.assertEqual((result["saved"], result["pending"], result["refill"]), (0, 6, {}))
        self.assertFalse((self.root / "user/answer-bank.json").exists())

    def test_stale_unknown_duplicate_or_empty_responses_are_refused(self):
        packet = self.questions.packet("claude")
        club = self.ids(packet, CLUB)
        entry = {"question_id": club["id"], "text": "Robotics Society"}
        for answers, batch in (([entry], "old-batch"), ([{"question_id": "q-unknown", "text": "x"}], None),
                               ([entry, entry], None), ([], None), ([{"question_id": club["id"], "text": "  "}], None)):
            with self.assertRaises(ValueError):
                self.questions.answer(self.respond(packet, answers, batch))
        self.assertFalse((self.root / "user/answer-bank.json").exists())


class WorkflowTests(ReleaseCase):
    def runner(self, fill):
        workflow = self.module("workflow")
        self.write_json("profile.json", candidate("authorized"))
        self.pdf = self.root / "synthetic.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n% synthetic\n%%EOF")
        self.browser = Mock(spec=["open", "fill", "upload_file", "snapshot", "refill", "close"])
        self.browser.open.return_value = "tab-1"
        self.browser.fill.return_value = fill
        self.live = snapshot(required_unknown=[CLUB])
        self.browser.snapshot.side_effect = lambda *args, **kwargs: dict(self.live)
        runner = workflow.Workflow(self.root, browser=self.browser, engines={"codex": self.fake_engine()},
                                   default_engine="codex", clock=lambda: NOW)
        runner.store.enqueue(job())
        return runner

    def test_apply_holds_the_tab_with_its_unanswered_questions(self):
        runner = self.runner(fill_result(unknown("question_2", CLUB, "text")))
        result = runner.apply(["synthetic-1"], pdf=self.pdf)
        self.assertEqual(result["jobs"][0]["status"], "needs_manual")
        self.assertEqual(result["jobs"][0]["questions"], 1)
        record = runner.store.get("synthetic-1")
        self.assertEqual(record["state"], "needs_manual")
        self.assertEqual(record["tab_id"], "tab-1")
        self.assertEqual([item["id"] for item in record["unanswered"]], ["question_2"])
        self.browser.upload_file.assert_called_once()
        self.browser.close.assert_not_called()

    def test_refill_fills_answers_into_the_held_tab_and_marks_it_filled(self):
        runner = self.runner(fill_result(unknown("question_2", CLUB, "text")))
        runner.apply(["synthetic-1"], pdf=self.pdf)
        self.browser.refill.return_value = {"filled": ["question_2"], "blocked": []}
        self.live = snapshot(fields=[{"id": "question_2", "label": CLUB, "required": True, "value": "Robotics Society"}])
        result = runner.refill("synthetic-1", {"question_2": "Robotics Society"})
        self.assertEqual(result["status"], "filled")
        args = self.browser.refill.call_args
        self.assertEqual(args.args[0], "tab-1")
        self.assertEqual(args.kwargs["answers"], {"question_2": "Robotics Society"})
        record = runner.store.get("synthetic-1")
        self.assertEqual((record["state"], record["unanswered"]), ("filled", []))
        self.browser.close.assert_not_called()

    def test_refill_keeps_the_tab_held_while_questions_remain(self):
        runner = self.runner(fill_result(unknown("question_2", CLUB, "text"), unknown("question_1", HEAR)))
        runner.apply(["synthetic-1"], pdf=self.pdf)
        self.browser.refill.return_value = {"filled": ["question_2"], "blocked": []}
        result = runner.refill("synthetic-1", {"question_2": "Robotics Society"})
        self.assertEqual(result["status"], "needs_manual")
        record = runner.store.get("synthetic-1")
        # The readback is not clean, so the filled field stays retryable as well.
        self.assertEqual([item["id"] for item in record["unanswered"]], ["question_2", "question_1"])

    def test_refill_refuses_a_job_without_a_held_tab(self):
        runner = self.runner({"status": "filled"})
        with self.assertRaises(ValueError):
            runner.refill("synthetic-1", {"question_2": "x"})


class BrowserRefillTests(unittest.TestCase):
    def test_refill_runs_only_the_adapter_fill_with_confirmed_answers(self):
        from autoapply_local.browser import BrowserBridge

        client = Mock(spec=["evaluate_script", "list_pages"])
        client.evaluate_script.return_value = {"filled": ["question_2"], "blocked": []}
        client.list_pages.return_value = [{"id": "tab-1", "url": job()["url"]}]
        bridge = BrowserBridge(client)
        result = bridge.refill("tab-1", job=job(), answers={"question_2": "Robotics Society"})
        self.assertEqual(result["filled"], ["question_2"])
        script = client.evaluate_script.call_args.args[1]
        self.assertEqual(client.evaluate_script.call_args.kwargs["operation"], "fill")
        self.assertIn('"question_2": {"value": "Robotics Society", "status": "confirmed"}', script)
        self.assertIsNone(script_driver.CLICKS.search(script))
        with self.assertRaises(ValueError):
            bridge.refill("tab-1", job=job(ats="icims"), answers={"q": "x"})


class RuntimeQuestionsTests(ReleaseCase):
    def test_answers_are_saved_then_refilled_through_the_lane_holding_each_tab(self):
        from unittest.mock import patch

        from autoapply_local import runtime
        from autoapply_local.storage import Store

        store = Store(self.root)
        store.enqueue(job("job-a"))
        store.update("job-a", state="needs_manual", tab_id="tab-a", lane="1",
                     unanswered=[unknown("question_2", CLUB, "text")])
        store.enqueue(job("job-b"))
        packet = runtime.questions(root=self.root, ui="claude")
        self.assertEqual([item["label"] for item in packet["questions"]], [CLUB])
        response = self.root / "response.json"
        response.write_text(json.dumps({"batch_id": packet["batch_id"], "answers": [
            {"question_id": packet["questions"][0]["id"], "text": "Robotics Society"}]}), encoding="utf-8")
        runner = Mock()
        runner.refill.return_value = {"job_id": "job-a", "status": "filled", "tab_id": "tab-a"}
        with patch.object(runtime, "_workflow", return_value=(runner, {}, "codex")) as build:
            result = runtime.questions(root=self.root, answer=response)
        runner.refill.assert_called_once_with("job-a", {"question_2": "Robotics Society"})
        self.assertEqual(build.call_args.kwargs["lane"]["lane"], "1")
        self.assertEqual((result["saved"], result["pending"]), (1, 0))
        self.assertEqual(result["refilled"], [{"job_id": "job-a", "status": "filled", "tab_id": "tab-a"}])


class CliTests(unittest.TestCase):
    def test_questions_command_dispatches_packet_and_answer(self):
        calls = []
        services = {"questions": lambda **kwargs: calls.append(kwargs) or {"status": "none"}}
        out, err = Mock(), Mock()
        self.assertEqual(cli.main(["--user-dir", ".", "questions", "--ui", "claude"], services, out, err), 0)
        self.assertEqual(cli.main(["--user-dir", ".", "questions", "--answer", "r.json"], services, out, err), 0)
        self.assertEqual(calls[0]["ui"], "claude")
        self.assertIsNone(calls[0]["answer"])
        self.assertEqual(calls[1]["answer"], Path("r.json"))
        self.assertEqual(calls[1]["ui"], "none")


if __name__ == "__main__":
    unittest.main()
