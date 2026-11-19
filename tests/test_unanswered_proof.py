"""Unknown required questions (plan aa-17): a refill counts only when it is read back.

A refilled field is done only when the fresh readback of the held tab shows the
user's own answer for it; a wrong value, an empty value or no evidence keeps the
field for ``questions --refill``. A banked answer crosses employers only for a
question about the candidate alone: an agreement, a yes/no question and a
question that refers to the employer or the role stay with that employer and
start date. Everything is synthetic and offline; nothing here clicks Submit.
"""

import hashlib
import sys
from pathlib import Path
from unittest.mock import Mock

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from test_support import NOW, ReleaseCase, candidate, job, snapshot  # noqa: E402
from test_unanswered import CLUB, HEAR, OPTIONS, SHIFT, unknown  # noqa: E402
from test_unanswered_flow import EngineLoopTests, FakeChrome  # noqa: E402

ANSWERS = {"question_1": "12", "question_2": "Robotics Society", "question_3": True}
YES_NO = [{"value": "1", "label": "Yes"}, {"value": "0", "label": "No"}]


def shown(**values):
    """A clean readback whose fields show these values."""
    labels = {"question_1": HEAR, "question_2": CLUB, "question_3": SHIFT}
    return snapshot(fields=[{"id": field_id, "label": labels[field_id], "required": True, "value": value}
                            for field_id, value in values.items()])


class RefillProofTests(ReleaseCase):
    def held(self):
        workflow = self.module("workflow")
        self.browser = Mock(spec=["refill", "snapshot"])
        self.browser.refill.return_value = {"filled": list(ANSWERS), "blocked": []}
        runner = workflow.Workflow(self.root, browser=self.browser, engines={}, default_engine="codex", clock=lambda: NOW)
        runner.store.enqueue(job())
        runner.store.update("synthetic-1", state="needs_manual", tab_id="tab-1",
                            unanswered=[unknown("question_1", HEAR), unknown("question_2", CLUB, "text"),
                                        unknown("question_3", SHIFT, "checkbox")])
        return runner

    def kept(self, runner):
        return [item["id"] for item in runner.store.get("synthetic-1")["unanswered"]]

    def test_matching_readback_marks_the_tab_filled(self):
        runner = self.held()
        self.browser.snapshot.return_value = shown(question_1="12", question_2="Robotics Society", question_3=True)
        self.assertEqual(runner.refill("synthetic-1", ANSWERS)["status"], "filled")
        self.assertEqual(self.kept(runner), [])

    def test_a_clean_readback_with_another_option_keeps_that_field(self):
        runner = self.held()
        self.browser.snapshot.return_value = shown(question_1="11", question_2="Robotics Society", question_3=True)
        result = runner.refill("synthetic-1", ANSWERS)
        self.assertEqual((result["status"], result["questions"]), ("needs_manual", 1))
        self.assertEqual(runner.store.get("synthetic-1")["state"], "needs_manual")
        self.assertEqual(self.kept(runner), ["question_1"])

    def test_wrong_text_or_checkbox_keeps_those_fields(self):
        runner = self.held()
        self.browser.snapshot.return_value = shown(question_1="12", question_2="Chess Club", question_3=False)
        self.assertEqual(runner.refill("synthetic-1", ANSWERS)["status"], "needs_manual")
        self.assertEqual(self.kept(runner), ["question_2", "question_3"])

    def test_a_field_the_readback_does_not_show_is_not_proven(self):
        runner = self.held()
        self.browser.snapshot.return_value = shown(question_1="12", question_3=True)
        self.assertEqual(runner.refill("synthetic-1", ANSWERS)["status"], "needs_manual")
        self.assertEqual(self.kept(runner), ["question_2"])

    def test_an_empty_or_unobserved_value_is_not_proven(self):
        runner = self.held()
        live = shown(question_1="12", question_2="", question_3=True)
        live["fields"][0]["observed"] = False
        self.browser.snapshot.return_value = live
        runner.refill("synthetic-1", ANSWERS)
        self.assertEqual(self.kept(runner), ["question_1", "question_2"])

    def test_the_option_label_proves_a_choice_and_a_review_row_is_matched_by_its_question(self):
        runner = self.held()
        # A multipage review page lists each answer under its question, with the option's label.
        self.browser.snapshot.return_value = snapshot(fields=[
            {"id": "review-1", "type": "summary", "label": HEAR, "value": "Friend"},
            {"id": "review-2", "type": "summary", "label": CLUB, "value": " Robotics  Society "},
            {"id": "review-3", "type": "summary", "label": SHIFT, "value": "Yes"}])
        self.assertEqual(runner.refill("synthetic-1", ANSWERS)["status"], "filled")

    def test_another_options_label_or_two_rows_that_disagree_prove_nothing(self):
        runner = self.held()
        self.browser.snapshot.return_value = snapshot(fields=[
            {"id": "review-1", "type": "summary", "label": HEAR, "value": "Job board"},
            {"id": "question_2", "label": CLUB, "value": "Robotics Society"},
            {"id": "question_2", "label": CLUB, "value": "Chess Club"},
            {"id": "question_3", "label": SHIFT, "value": True}])
        runner.refill("synthetic-1", ANSWERS)
        self.assertEqual(self.kept(runner), ["question_1", "question_2"])

    def test_a_kept_field_is_retried_from_the_bank(self):
        runner = self.held()
        self.browser.snapshot.return_value = shown(question_1="11", question_2="Robotics Society", question_3=True)
        runner.refill("synthetic-1", ANSWERS)
        record = runner.store.get("synthetic-1")
        self.module("answer_bank").AnswerBank(self.root).confirm(unknown("question_1", HEAR), record, "Friend", source_id="s")
        plan = self.module("unanswered").Questions(self.root).refill_plan([record])
        self.assertEqual(plan, {"synthetic-1": {"question_1": "12"}})

    def test_the_engine_is_told_to_finish_where_the_answers_can_be_read_back(self):
        from autoapply_local.browser import REFILL_RULE

        self.assertIn("Review", REFILL_RULE)
        self.assertIn("Never click Submit", REFILL_RULE)


class BlockedRefillTests(ReleaseCase):
    """The engine reports blocked although the readback proves every answer."""

    held = RefillProofTests.held
    kept = RefillProofTests.kept

    def blocked(self, field_id, reason="control_not_supported"):
        runner = self.held()
        self.browser.refill.return_value = {"filled": list(ANSWERS), "blocked": [{"id": field_id, "reason": reason}]}
        self.browser.snapshot.return_value = shown(question_1="12", question_2="Robotics Society", question_3=True)
        result = runner.refill("synthetic-1", ANSWERS)
        return runner, result

    def test_a_blocked_field_stays_held_listed_and_retryable(self):
        runner, result = self.blocked("question_2")
        record = runner.store.get("synthetic-1")
        self.assertEqual((result["status"], result["questions"]), ("needs_manual", 1))
        self.assertEqual((record["state"], self.kept(runner)), ("needs_manual", ["question_2"]))
        self.assertIn("control_not_supported", record["problem"])
        questions = self.module("unanswered").Questions(self.root)
        self.assertEqual([item["id"] for item in questions._held([record])], ["synthetic-1"])
        self.assertEqual(questions.collect([record])["pending"], 1)

    def test_an_unknown_blocked_id_keeps_every_field(self):
        runner, result = self.blocked(None, "form_not_readable")
        self.assertEqual(result["questions"], 3)
        self.assertEqual(self.kept(runner), ["question_1", "question_2", "question_3"])
        self.assertIn("form_not_readable", runner.store.get("synthetic-1")["problem"])

    def test_a_later_clean_refill_moves_the_blocked_job_to_filled(self):
        runner, _ = self.blocked("question_2")
        self.browser.refill.return_value = {"filled": ["question_2"], "blocked": []}
        result = runner.refill("synthetic-1", {"question_2": "Robotics Society"})
        record = runner.store.get("synthetic-1")
        self.assertEqual((result["status"], record["state"], record["unanswered"]), ("filled", "filled", []))


class WrongOptionChrome(FakeChrome):
    """The engine reports the field filled, yet the tab shows the other option."""

    def run(self, task):
        result = super().run(task)
        if task["kind"] == "browser_native_refill":
            self.question_value = "11"
        return result


class EngineWrongValueTests(EngineLoopTests):
    test_workday_question_is_collected_asked_once_banked_refilled_and_reused = None

    def test_workday_refill_reading_back_another_option_stays_held_and_retryable(self):
        workflow, unanswered = self.module("workflow"), self.module("unanswered")
        self.write_json("profile.json", candidate("authorized"))
        pdf = self.root / "synthetic.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")
        posting = job(ats="workday")
        chrome = WrongOptionChrome(pdf, posting["url"])
        self.assertEqual(chrome.receipt["sha256"], hashlib.sha256(pdf.read_bytes()).hexdigest())
        runner = workflow.Workflow(self.root, browser=self.bridge(chrome), engines={"codex": self.fake_engine()},
                                   default_engine="codex", clock=lambda: NOW)
        runner.store.enqueue(posting)
        runner.apply(["synthetic-1"], pdf=pdf)
        result = runner.refill("synthetic-1", {"question_1": "12"})
        self.assertEqual(result["status"], "needs_manual")
        record = runner.store.get("synthetic-1")
        self.assertEqual((record["state"], [item["id"] for item in record["unanswered"]]), ("needs_manual", ["question_1"]))
        self.module("answer_bank").AnswerBank(self.root).confirm(record["unanswered"][0], record, "Friend", source_id="s")
        self.assertEqual(unanswered.Questions(self.root).refill_plan([record]), {"synthetic-1": {"question_1": "12"}})


class EmployerScopeTests(ReleaseCase):
    def stays_with_the_employer(self, field, value):
        bank = self.module("answer_bank").AnswerBank(self.root)
        june = {**job(), "start_date": "2030-06-01"}
        bank.confirm(field, june, value, source_id="s")
        self.assertEqual(bank.lookup(field, {**june, "id": "again"})["value"], value)
        for other in ({**june, "company": "Another Co"}, {**june, "start_date": "2030-09-01"}):
            self.assertIsNone(bank.lookup(field, other))
            self.assertNotIn(field["label"], [item["question"] for item in bank.entries_for(other)])
        self.assertIn(field["label"], [item["question"] for item in bank.entries_for({**june, "id": "again"})])

    def test_agreement_questions_stay_with_the_employer_and_start_date(self):
        for label in ("Do you agree to the terms and conditions?",
                      "I have read the privacy notice",
                      "Do you accept the candidate policy?",
                      "I acknowledge the statement above",
                      "I certify that my answers are true",
                      "Will you sign an arbitration agreement?"):
            with self.subTest(label=label):
                self.stays_with_the_employer({"label": label, "type": "select", "options": YES_NO}, "Yes")

    def test_a_required_checkbox_stays_with_the_employer(self):
        for label in ("Proceed", "Tick to continue", SHIFT):
            with self.subTest(label=label):
                self.stays_with_the_employer({"label": label, "type": "checkbox"}, True)

    def test_a_yes_no_question_stays_with_the_employer_whatever_its_wording(self):
        for options in (YES_NO, [{"value": "a", "label": "I agree"}, {"value": "d", "label": "I do not agree"}],
                        ["Accept", "Decline"], ["True", "False"]):
            with self.subTest(options=options):
                self.stays_with_the_employer({"label": "Please respond", "type": "radio", "options": options},
                                             options[0]["label"] if isinstance(options[0], dict) else options[0])

    def test_a_question_about_the_employer_or_the_role_stays_with_the_employer(self):
        for label in ("How did you hear about us?", "Why do you want to work here?",
                      "Have you worked for this company before?", "Who referred you?",
                      "What interests you about our team?", HEAR):
            with self.subTest(label=label):
                self.stays_with_the_employer({"label": label, "type": "text"}, "A friend")

    def test_a_question_about_the_candidate_alone_is_reused_by_the_next_employer(self):
        bank = self.module("answer_bank").AnswerBank(self.root)
        other = {**job("next"), "company": "Another Co", "start_date": "2031-01-01"}
        text = {"label": CLUB, "type": "text"}
        bank.confirm(text, job(), "Robotics Society", source_id="s")
        self.assertEqual(bank.lookup(text, other)["value"], "Robotics Society")
        choice = {"label": "Which language do you know best?", "type": "select", "options": ["Python", "Go", "Rust"]}
        bank.confirm(choice, job(), "Go", source_id="s")
        self.assertEqual(bank.lookup(choice, other)["value"], "Go")
        self.assertEqual(sorted(item["question"] for item in bank.entries_for(other)), [choice["label"], CLUB])
        self.assertEqual(OPTIONS[2]["label"], "Friend")


class CollidingOptionTests(RefillProofTests):
    """One option's value reads like another option's label."""

    OPTIONS = [{"value": "North", "label": "South"}, {"value": "south-id", "label": "North"}]

    def held(self):
        runner = super().held()
        runner.store.update("synthetic-1", unanswered=[unknown("question_1", HEAR, options=self.OPTIONS)])
        self.browser.refill.return_value = {"filled": ["question_1"], "blocked": []}
        return runner

    def refilled(self, row):
        runner = self.held()
        self.browser.snapshot.return_value = snapshot(fields=[row])
        return runner.refill("synthetic-1", {"question_1": "North"})["status"]  # the user chose "South"

    def test_a_review_row_is_read_as_a_label_never_as_an_option_value(self):
        self.assertEqual(self.refilled({"id": "review-1", "type": "summary", "label": HEAR, "value": "North"}), "needs_manual")
        self.assertEqual(self.refilled({"id": "question_1", "type": "summary", "label": HEAR, "value": "North"}), "needs_manual")
        self.assertEqual(self.refilled({"id": "review-1", "type": "summary", "label": HEAR, "value": "South"}), "filled")

    def test_a_control_value_that_could_name_two_options_proves_nothing(self):
        self.assertEqual(self.refilled({"id": "question_1", "type": "select", "label": HEAR, "value": "North"}), "needs_manual")
        self.assertEqual(self.refilled({"id": "question_1", "type": "select", "label": HEAR, "value": "south-id"}), "needs_manual")


for _name in [name for name in dir(RefillProofTests) if name.startswith("test_")]:
    setattr(CollidingOptionTests, _name, None)  # only the collision cases run here


class CandidateOnlyScopeTests(EmployerScopeTests):
    test_a_question_about_the_candidate_alone_is_reused_by_the_next_employer = None
    test_agreement_questions_stay_with_the_employer_and_start_date = None
    test_a_required_checkbox_stays_with_the_employer = None
    test_a_yes_no_question_stays_with_the_employer_whatever_its_wording = None
    test_a_question_about_the_employer_or_the_role_stays_with_the_employer = None

    def test_contact_permission_stays_with_the_employer_in_both_lookup_paths(self):
        for label in ("May recruiters contact you about future opportunities?", "Stay in touch?",
                      "Join the talent community", "Receive job alerts by"):
            with self.subTest(label=label):
                self.stays_with_the_employer(
                    {"label": label, "type": "select", "options": ["Email only", "Do not contact"]}, "Email only")

    def test_a_question_not_known_to_concern_the_candidate_alone_stays_with_the_employer(self):
        for label in ("Choose one", "Preferred office", "Which shift suits you?", "Anything else to add?"):
            with self.subTest(label=label):
                self.stays_with_the_employer({"label": label, "type": "select", "options": ["Blue", "Green"]}, "Blue")
                self.stays_with_the_employer({"label": label, "type": "text"}, "Blue")

    def test_candidate_wording_never_frees_an_employer_bound_question(self):
        for label in ("May we keep your degree and skills on file?", "Share your university with recruiters by"):
            with self.subTest(label=label):
                self.stays_with_the_employer({"label": label, "type": "select", "options": ["Email", "Phone"]}, "Email")
