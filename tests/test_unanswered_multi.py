"""A required checkbox-group question takes several options (PR #17 review).

"Which offices would you work in? (select all)" is one question with several
answers: the popup lets the user pick more than one option, the answer bank
keeps the picks as a list of option labels, the refill sets every chosen box
and the fresh readback must show exactly the saved set before the tab counts as
filled. Everything is synthetic and offline; nothing here clicks Submit.
"""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from test_support import NOW, ReleaseCase, job, snapshot  # noqa: E402

OFFICES = "Which offices would you work in? (select all that apply)"
BOXES = [{"value": "nyc", "label": "New York"}, {"value": "aus", "label": "Austin"},
         {"value": "rem", "label": "Remote"}]


def group(field_id="offices", options=None):
    return {"id": field_id, "label": OFFICES, "type": "checkbox-group", "required": True,
            "options": BOXES if options is None else options, "reason": "no_confirmed_fact"}


def held(job_id="job-a", field=None):
    return {**job(job_id), "state": "needs_manual", "tab_id": "tab-" + job_id, "unanswered": [field or group()]}


class MultiPopupTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.questions = self.module("unanswered").Questions(self.root)
        self.questions.collect([held()])

    def respond(self, packet, answers):
        path = self.root / "response.json"
        path.write_text(json.dumps({"batch_id": packet["batch_id"], "answers": answers}), encoding="utf-8")
        return path

    def test_claude_popup_item_for_a_checkbox_group_is_multi_select(self):
        packet = self.questions.packet("claude")
        item = packet["ui"]["calls"][0]["questions"][0]
        self.assertIs(item["multiSelect"], True)
        self.assertEqual([option["label"] for option in item["options"]], ["New York", "Austin", "Remote", "Skip for now"])
        self.assertEqual(packet["questions"][0]["kind"], "multi")

    def test_a_single_choice_question_stays_single_select(self):
        questions = self.module("unanswered").Questions(self.root / "single")
        questions.collect([held(field={**group(), "type": "radio", "label": "Which office is closest?"})])
        self.assertIs(questions.packet("claude")["ui"]["calls"][0]["questions"][0]["multiSelect"], False)

    def test_codex_popups_ask_for_every_option_that_applies_as_text(self):
        for ui in ("codex", "codex-async"):
            packet = self.questions.packet(ui)
            item = packet["ui"]["calls"][0]["questions"][0]
            prompt = item.get("question", item.get("title"))
            self.assertIn("New York | Austin | Remote", prompt)
            self.assertIn("every option that applies", prompt)
            labels = [option["label"] if isinstance(option, dict) else option for option in item["options"]]
            self.assertNotIn("Austin", labels)

    def test_two_picked_options_are_stored_as_a_list_and_map_to_both_tab_values(self):
        packet = self.questions.packet("claude")
        choices = {choice["label"]: choice["id"] for choice in packet["questions"][0]["choices"]}
        result = self.questions.answer(self.respond(packet, [
            {"question_id": packet["questions"][0]["id"], "option_ids": [choices["Remote"], choices["New York"]]}]))
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["refill"], {"job-a": {"offices": ["nyc", "rem"]}})
        bank = json.loads((self.root / "user/answer-bank.json").read_text(encoding="utf-8"))
        self.assertEqual(bank["answers"][0]["value"], ["New York", "Remote"])
        self.assertEqual(bank["answers"][0]["kind"], "multi")
        found = self.module("answer_bank").AnswerBank(self.root).lookup(group(), job("job-a"))
        self.assertEqual(found["value"], ["New York", "Remote"])

    def test_typed_options_separated_by_bars_are_stored_as_a_list(self):
        packet = self.questions.packet("codex")
        result = self.questions.answer(self.respond(packet, [
            {"question_id": packet["questions"][0]["id"], "text": " austin |  Remote "}]))
        self.assertEqual(result["refill"], {"job-a": {"offices": ["aus", "rem"]}})

    def test_an_unknown_typed_option_or_skip_mixed_with_options_is_refused(self):
        packet = self.questions.packet("claude")
        qid = packet["questions"][0]["id"]
        choices = {choice["label"]: choice["id"] for choice in packet["questions"][0]["choices"]}
        for entry in ({"question_id": qid, "text": "Austin | Paris"},
                      {"question_id": qid, "option_ids": [choices["Austin"], choices["Skip for now"]]},
                      {"question_id": qid, "option_ids": []}):
            with self.assertRaises(ValueError):
                self.questions.answer(self.respond(packet, [entry]))
        self.assertFalse((self.root / "user/answer-bank.json").exists())

    def test_skip_alone_is_not_an_answer(self):
        packet = self.questions.packet("claude")
        skip = next(c["id"] for c in packet["questions"][0]["choices"] if c["label"] == "Skip for now")
        result = self.questions.answer(self.respond(packet, [
            {"question_id": packet["questions"][0]["id"], "option_ids": [skip]}]))
        self.assertEqual((result["saved"], result["pending"]), (0, 1))

    def test_a_banked_list_refills_the_next_held_tab_with_its_own_values(self):
        bank = self.module("answer_bank").AnswerBank(self.root)
        bank.confirm(group(), job("job-b"), ["Austin", "Remote"], source_id="s")
        other = group("q_9", [{"value": "1", "label": "New York"}, {"value": "2", "label": "Austin"},
                              {"value": "3", "label": "Remote"}])
        plan = self.questions.refill_plan([held("job-b", other)])
        self.assertEqual(plan, {"job-b": {"q_9": ["2", "3"]}})

    def test_the_bank_refuses_an_empty_or_unlisted_multi_answer(self):
        bank = self.module("answer_bank").AnswerBank(self.root)
        for value in ([], "Austin", ["Paris"], ["Austin", "Austin"]):
            with self.assertRaises(ValueError):
                bank.confirm(group(), job(), value, source_id="s")


class MultiRefillTests(ReleaseCase):
    def runner(self):
        workflow = self.module("workflow")
        self.browser = Mock(spec=["refill", "snapshot"])
        self.browser.refill.return_value = {"filled": ["offices"], "blocked": []}
        runner = workflow.Workflow(self.root, browser=self.browser, engines={}, default_engine="codex", clock=lambda: NOW)
        runner.store.enqueue(job())
        runner.store.update("synthetic-1", state="needs_manual", tab_id="tab-1", unanswered=[group()])
        return runner

    def readback(self, value):
        return snapshot(fields=[{"id": "offices", "label": OFFICES, "type": "checkbox-group", "required": True,
                                 "value": value}])

    def test_refill_with_only_one_of_the_two_boxes_checked_stays_needs_manual(self):
        runner = self.runner()
        self.browser.snapshot.return_value = self.readback(["nyc"])
        result = runner.refill("synthetic-1", {"offices": ["nyc", "rem"]})
        self.assertEqual((result["status"], result["questions"]), ("needs_manual", 1))
        self.assertEqual([item["id"] for item in runner.store.get("synthetic-1")["unanswered"]], ["offices"])

    def test_an_extra_checked_box_is_not_the_saved_set(self):
        runner = self.runner()
        self.browser.snapshot.return_value = self.readback(["nyc", "aus", "rem"])
        self.assertEqual(runner.refill("synthetic-1", {"offices": ["nyc", "rem"]})["status"], "needs_manual")

    def test_both_boxes_checked_marks_the_tab_filled(self):
        runner = self.runner()
        self.browser.snapshot.return_value = self.readback(["rem", "nyc"])
        result = runner.refill("synthetic-1", {"offices": ["nyc", "rem"]})
        self.assertEqual(result["status"], "filled")
        self.assertEqual(runner.store.get("synthetic-1")["state"], "filled")
        self.browser.refill.assert_called_once()
        self.assertEqual(self.browser.refill.call_args.kwargs["answers"], {"offices": ["nyc", "rem"]})

    def test_a_review_row_listing_both_labels_counts(self):
        runner = self.runner()
        self.browser.snapshot.return_value = snapshot(fields=[
            {"id": "review-1", "type": "summary", "label": OFFICES, "required": True, "value": ["Remote", "New York"]}])
        self.assertEqual(runner.refill("synthetic-1", {"offices": ["nyc", "rem"]})["status"], "filled")

    def test_refill_refuses_a_list_with_a_value_the_group_does_not_have(self):
        runner = self.runner()
        for answer in (["nyc", "paris"], [], "nyc", ["nyc", "nyc"]):
            with self.assertRaises(ValueError):
                runner.refill("synthetic-1", {"offices": answer})
        self.browser.refill.assert_not_called()
