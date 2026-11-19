"""Engine setup part 3.3 steps 1-3: resume groups, missing facts, derived filters."""

import json
import unittest

from test_resume_facts import RESUME
from test_support import ReleaseCase

META = ("schema_version", "session_id", "revision", "batch_id")
ALL_JD = ["no_sponsorship", "us_citizen", "clearance", "itar"]
MISSING = ["authorization.authorized_now", "authorization.sponsorship_now", "authorization.sponsorship_future",
           "contact.street", "contact.city", "contact.state", "contact.postcode", "contact.country",
           "compensation.eeo", "logistics.season", "logistics.year", "targets.job_types", "targets.roles"]


class GuidedQuickTests(ReleaseCase):
    def prepared(self, resume=RESUME, terminal=True):
        self.module("runtime").setup(root=self.root)
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        if terminal:
            path = self.root / "resumes/alex.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            if resume is not None:
                path.write_text(resume, encoding="utf-8")
            config["runtime"].update(language="en", apply_engine="claude", jobs_per_run=10, quota_stop_percent=90)
            config["resumes"] = {"main_pdf": str(path), "main_tags": ["SWE"], "alternatives": []}
        self.write_json("config.json", config)

    def run_guided(self, **kwargs):
        return self.module("guided").run(self.root, ui="codex-async", **kwargs)

    def progress(self):
        return json.loads((self.root / "user/setup-progress.json").read_text(encoding="utf-8"))

    def document(self):
        return self.progress()["document"]["groups"]

    def entry(self, question, value=None, status="answered"):
        result = {"question_id": question["id"], "status": status}
        if status != "answered":
            return result
        values = value if question["kind"] == "multi_choice" else [value]
        ids = [next((c["id"] for c in question["choices"] if not c.get("control") and type(c["value"]) is type(v)
                     and c["value"] == v), None) for v in values]
        if all(ids):
            result["option_ids"] = ids
        else:
            result["value"] = value
        return result

    def reply(self, packet, entries):
        path = self.root.parent / (self.root.name + "-quick-response.json")
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text(json.dumps({**{key: packet[key] for key in META}, "answers": entries}), encoding="utf-8")
        return self.run_guided(answer=path)

    def drive(self, packet, answers, until):
        """Answer listed fields, skip the rest, until a question with ``until`` ID is issued."""
        seen = []
        for _ in range(200):
            ids = [q["id"] for q in packet["questions"]]
            seen.extend(ids)
            if until in ids or packet["status"] != "questions":
                return packet, seen
            packet = self.reply(packet, [self.entry(q, answers[q["id"]]) if q["id"] in answers
                                         else self.entry(q, status="notasked") for q in packet["questions"]])
        self.fail("setup did not reach " + until)

    def test_resume_groups_come_first_with_confirm_and_edit(self):
        self.prepared()
        packet = self.run_guided()
        ids = [q["id"] for q in packet["questions"]]
        self.assertEqual(ids, ["setup.quick.resume.contact", "setup.quick.resume.education",
                               "setup.quick.resume.experience"])
        contact = packet["questions"][0]
        self.assertIn("Alex Rivera", contact["prompt"]["en"])
        self.assertIn("alex.rivera@example.invalid", contact["prompt"]["en"])
        values = [c["value"] for c in contact["choices"] if not c.get("control")]
        self.assertEqual(values, ["confirm", "edit"])
        self.assertIn("Northfield State University", packet["questions"][1]["prompt"]["en"])
        self.assertIn("Example Labs", packet["questions"][2]["prompt"]["en"])
        self.assertEqual(packet["ui"]["tool"], "request_user_input_async")

    def test_confirm_writes_group_and_edit_asks_its_fields_with_the_resume_value(self):
        self.prepared()
        packet = self.run_guided()
        contact, education, experience = packet["questions"]
        packet = self.reply(packet, [self.entry(contact, "confirm"), self.entry(education, "edit"),
                                     self.entry(experience, "confirm")])
        groups = self.document()
        self.assertEqual(groups["contact"]["legal_name"], "Alex Rivera")
        self.assertEqual(groups["contact"]["github"], "https://github.com/alexrivera-example")
        self.assertEqual([r["employer"] for r in groups["experience"]["records"]], ["Example Labs", "Campus Tools Office"])
        # Edit keeps the resume value as the draft and asks each of its fields.
        self.assertEqual(groups["education"]["records"][0]["school"], "Northfield State University")
        ids = [q["id"] for q in packet["questions"]]
        self.assertEqual(ids, ["education.records", "education.gpa", "education.gpa_scale"])
        self.assertIn("Resume shows", packet["questions"][1]["prompt"]["en"])
        self.assertIn("3.8", packet["questions"][1]["prompt"]["en"])
        packet = self.reply(packet, [self.entry(packet["questions"][0], status="notasked"),
                                     self.entry(packet["questions"][1], 3.9),
                                     self.entry(packet["questions"][2], status="notasked")])
        groups = self.document()
        self.assertEqual(groups["education"]["gpa"], 3.9)
        self.assertEqual(groups["education"]["gpa_scale"], 4.0)
        self.assertEqual(len(groups["education"]["records"]), 1)
        self.assertEqual(packet["questions"][0]["id"], "setup.quick.resume.skills")

    def test_skipping_a_group_writes_nothing(self):
        self.prepared()
        packet = self.run_guided()
        self.reply(packet, [self.entry(q, status="notasked") for q in packet["questions"]])
        groups = self.document()
        self.assertIsNone(groups["contact"]["legal_name"])
        self.assertEqual(groups["experience"]["records"], [])

    def test_missing_facts_then_filters_derived_from_answers(self):
        self.prepared()
        answers = {f"setup.quick.resume.{name}": "confirm" for name in ("contact", "education", "experience", "skills")}
        answers.update({"authorization.sponsorship_now": True, "logistics.season": "summer", "logistics.year": 2027,
                        "targets.job_types": ["internship"], "targets.roles": ["SWE"]})
        packet, seen = self.drive(self.run_guided(), answers, "setup.quick.filters")
        catalog = [i for i in seen if not i.startswith("setup.")]
        self.assertEqual(catalog, MISSING, "Missing facts follow the resume groups; resume fields are not re-asked")
        self.assertEqual([q["id"] for q in packet["questions"]], ["setup.quick.filters"], "Filters are shown alone")
        prompt = packet["questions"][0]["prompt"]["en"]
        for needle in ("7 days", "ITAR", "intern", "senior", "Summer 2027", "SWE"):
            self.assertIn(needle, prompt)
        packet = self.reply(packet, [self.entry(packet["questions"][0], "confirm")])
        filters = self.document()["filters"]
        self.assertEqual(filters["jd_exclude"], ALL_JD)
        self.assertEqual(filters["title_include"], ["intern"])
        self.assertIn("senior", filters["title_exclude"])
        self.assertEqual(filters["posted_within_days"], 7)
        # The optional advanced intake continues and never re-asks quick fields.
        later, rest = self.drive(packet, {}, "never")
        self.assertEqual(later["status"], "draft_complete")
        asked = set(MISSING) | {"contact.legal_name", "contact.application_email", "education.records",
                                "experience.records", "experience.skills", "filters.jd_exclude"}
        self.assertFalse(asked & set(rest), sorted(asked & set(rest)))
        self.assertIn("runtime.mode", rest)
        preview = self.run_guided(finish=True)
        self.assertEqual(preview["status"], "preview")
        changed = {change["field"] for change in preview["preview"]["changes"]}
        self.assertIn("filters.jd_exclude", changed)

    def test_filters_follow_the_latest_answers(self):
        self.prepared()
        answers = {f"setup.quick.resume.{name}": "confirm" for name in ("contact", "education", "experience", "skills")}
        answers.update({"authorization.sponsorship_now": False, "authorization.sponsorship_future": False})
        packet, _ = self.drive(self.run_guided(), answers, "setup.quick.filters")
        self.assertNotIn("ITAR", packet["questions"][0]["prompt"]["en"])
        self.reply(packet, [self.entry(packet["questions"][0], "confirm")])
        self.assertEqual(self.document()["filters"]["jd_exclude"], [])

    def test_edit_filters_asks_each_filter_with_the_suggestion(self):
        self.prepared()
        answers = {f"setup.quick.resume.{name}": "confirm" for name in ("contact", "education", "experience", "skills")}
        answers.update({"authorization.sponsorship_future": True, "targets.job_types": ["internship"]})
        packet, _ = self.drive(self.run_guided(), answers, "setup.quick.filters")
        packet = self.reply(packet, [self.entry(packet["questions"][0], "edit")])
        self.assertEqual([q["id"] for q in packet["questions"]],
                         ["filters.posted_within_days", "filters.jd_exclude", "filters.title_include"])
        self.assertIn("Suggested", packet["questions"][0]["prompt"]["en"])
        self.assertEqual(self.document()["filters"]["jd_exclude"], ALL_JD)
        packet = self.reply(packet, [self.entry(packet["questions"][0], 3),
                                     self.entry(packet["questions"][1], ["itar"]),
                                     self.entry(packet["questions"][2], status="notasked")])
        filters = self.document()["filters"]
        self.assertEqual((filters["posted_within_days"], filters["jd_exclude"]), (3, ["itar"]))
        self.assertEqual(filters["title_include"], ["intern"])
        self.assertEqual([q["id"] for q in packet["questions"]][:2], ["filters.title_exclude", "filters.excluded_employers"])

    def test_unreadable_resume_starts_with_missing_facts(self):
        self.prepared(resume=None)
        packet = self.run_guided()
        self.assertEqual([q["id"] for q in packet["questions"]], MISSING[:3])

    def test_edit_of_an_answered_field_keeps_the_pending_queue(self):
        self.prepared(resume=None)
        packet = self.run_guided()
        self.assertEqual([q["id"] for q in packet["questions"]], MISSING[:3])
        packet = self.reply(packet, [self.entry(q, status="notasked") for q in packet["questions"]])
        self.assertEqual([q["id"] for q in packet["questions"]], MISSING[3:6])
        pending = [q["id"] for q in self.progress()["queue"]]
        self.assertEqual(pending[:3], MISSING[3:6])
        packet = self.run_guided(edit=MISSING[0])
        self.assertEqual([q["id"] for q in packet["questions"]], [MISSING[0], *MISSING[3:5]])
        self.assertEqual([q["id"] for q in self.progress()["queue"]], [MISSING[0], *pending])

    def test_edit_of_a_queued_field_moves_it_first_without_duplicate(self):
        self.prepared(resume=None)
        self.run_guided()
        pending = [q["id"] for q in self.progress()["queue"]]
        self.assertEqual(pending[:3], MISSING[:3])
        self.run_guided(edit=MISSING[2])
        queue = [q["id"] for q in self.progress()["queue"]]
        self.assertEqual(queue, [MISSING[2], *[q for q in pending if q != MISSING[2]]])

    def test_without_terminal_setup_the_original_order_is_kept(self):
        self.prepared(terminal=False)
        packet = self.run_guided()
        self.assertEqual({q["id"] for q in packet["questions"]}, {"runtime.mode", "runtime.language", "runtime.apply_engine"})

    def test_confirm_keeps_terminal_setup_config(self):
        self.prepared()
        packet = self.run_guided()
        self.reply(packet, [self.entry(q, "confirm") for q in packet["questions"]])
        self.run_guided(finish=True)
        result = self.run_guided(confirm=True)
        self.assertTrue(result["applied"])
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["runtime"]["jobs_per_run"], 10)
        self.assertEqual(config["runtime"]["quota_stop_percent"], 90)
        self.assertEqual(config["resumes"]["main_tags"], ["SWE"])
        self.assertTrue(config["resumes"]["main_pdf"].endswith("alex.txt"))
        self.assertEqual(config["runtime"]["apply_engine"], "claude")
        profile = json.loads((self.root / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["contact"]["legal_name"]["value"], "Alex Rivera")


class GuidedQuickReviewTests(ReleaseCase):
    prepared, run_guided, progress, document = (GuidedQuickTests.prepared, GuidedQuickTests.run_guided,
                                                GuidedQuickTests.progress, GuidedQuickTests.document)
    entry, reply, drive = GuidedQuickTests.entry, GuidedQuickTests.reply, GuidedQuickTests.drive

    def confirmed_filters(self, answers):
        self.prepared()
        base = {f"setup.quick.resume.{name}": "confirm" for name in ("contact", "education", "experience", "skills")}
        packet, _ = self.drive(self.run_guided(), {**base, **answers}, "setup.quick.filters")
        return self.reply(packet, [self.entry(packet["questions"][0], "confirm")])

    def edit_answer(self, field, value=None, status="answered"):
        packet = self.run_guided(edit=field)
        question = next(q for q in packet["questions"] if q["id"] == field)
        return self.reply(packet, [self.entry(question, value, status)])

    def test_confirm_shows_every_resume_value_it_writes(self):
        self.prepared()
        packet = self.run_guided()
        education, experience = packet["questions"][1]["prompt"]["en"], packet["questions"][2]["prompt"]["en"]
        self.assertIn("2027-05", education)
        self.assertIn("expected", education)
        self.assertIn("Computer Science", education)
        self.assertIn("Built a caching layer for an internal API", experience)
        self.assertIn("Maintained a ticket dashboard", experience)
        self.assertIn("Springfield, IL", experience)
        self.assertIn("Built a caching layer", packet["questions"][2]["prompt"]["vi"])

    def test_unknown_sponsorship_after_confirm_clears_derived_exclusions(self):
        self.confirmed_filters({"authorization.sponsorship_now": True})
        self.assertEqual(self.document()["filters"]["jd_exclude"], ALL_JD)
        packet = self.edit_answer("authorization.sponsorship_now", status="unknown")
        self.assertEqual(self.document()["filters"]["jd_exclude"], [])
        self.assertIn("setup.quick.filters", [q["id"] for q in packet["questions"]],
                      "Changed answers show the filter review again")
        self.assertNotIn("ITAR", packet["questions"][0]["prompt"]["en"])

    def test_resync_keeps_filters_the_user_edited(self):
        self.confirmed_filters({"authorization.sponsorship_now": True})
        self.edit_answer("filters.jd_exclude", ["itar"])
        self.edit_answer("authorization.sponsorship_now", False)
        self.assertEqual(self.document()["filters"]["jd_exclude"], ["itar"])

    def test_reconfirming_the_reopened_review_keeps_edited_filters(self):
        self.confirmed_filters({"authorization.sponsorship_now": True, "targets.job_types": ["internship"]})
        self.edit_answer("filters.jd_exclude", ["itar"])
        packet = self.edit_answer("authorization.sponsorship_now", False)
        review = packet["questions"][0]
        self.assertEqual(review["id"], "setup.quick.filters")
        self.assertIn("ITAR", review["prompt"]["en"], "The review shows the filters Confirm will write")
        self.assertNotIn("security clearance", review["prompt"]["en"])
        self.reply(packet, [self.entry(review, "confirm")])
        filters = self.document()["filters"]
        self.assertEqual(filters["jd_exclude"], ["itar"])
        self.assertEqual(filters["title_include"], ["intern"])
        # The edit stays an edit: a later answer change still does not replace it.
        packet = self.edit_answer("authorization.sponsorship_now", True)
        self.assertEqual(self.document()["filters"]["jd_exclude"], ["itar"])
        self.reply(packet, [self.entry(packet["questions"][0], "confirm")])
        self.assertEqual(self.document()["filters"]["jd_exclude"], ["itar"])

    def test_reopened_review_still_follows_answers_for_unedited_filters(self):
        self.confirmed_filters({"authorization.sponsorship_now": True, "targets.job_types": ["internship"]})
        self.edit_answer("filters.jd_exclude", ["itar"])
        packet = self.edit_answer("targets.job_types", ["newgrad"])
        self.assertNotIn("title must include: intern", packet["questions"][0]["prompt"]["en"])
        self.reply(packet, [self.entry(packet["questions"][0], "confirm")])
        filters = self.document()["filters"]
        self.assertEqual((filters["jd_exclude"], filters["title_include"]), (["itar"], []))

    def test_editing_the_reopened_review_starts_from_the_edited_filters(self):
        self.confirmed_filters({"authorization.sponsorship_now": True})
        self.edit_answer("filters.jd_exclude", ["itar"])
        packet = self.edit_answer("authorization.sponsorship_now", False)
        packet = self.reply(packet, [self.entry(packet["questions"][0], "edit")])
        self.assertEqual(self.document()["filters"]["jd_exclude"], ["itar"])
        question = next(q for q in packet["questions"] if q["id"] == "filters.jd_exclude")
        self.assertIn("Suggested: itar;", question["prompt"]["en"])

    def test_new_session_review_keeps_filters_edited_in_the_loaded_profile(self):
        quick = self.module("guided_quick")
        groups = self.module("intake").blank_intake()["groups"]
        groups["authorization"]["sponsorship_now"] = True
        groups["targets"]["job_types"] = ["internship"]
        groups["filters"].update(jd_exclude=["itar"], title_include=["intern"])
        state = {"document": {"groups": groups}, "quick_baseline": quick.baseline(groups)}
        groups["targets"]["job_types"] = ["newgrad"]
        review = {"quick_step": "filters"}
        quick.refresh(state, review)
        self.assertEqual(review["_draft"]["filters.jd_exclude"], ["itar"], "An edited filter is kept")
        self.assertEqual(review["_draft"]["filters.title_include"], [], "An unedited filter follows the answers")
        self.assertNotIn("security clearance", review["prompt"]["en"])

    def test_legacy_intake_without_filter_fields_gets_the_derived_filters(self):
        self.prepared()
        document = self.module("intake").blank_intake()
        for name in ("posted_within_days", "title_include", "title_exclude", "jd_exclude"):
            del document["groups"]["filters"][name]
        document["groups"]["authorization"]["sponsorship_now"] = True
        document["groups"]["targets"]["job_types"] = ["internship"]
        self.write_json("intake-import.json", document)
        base = {f"setup.quick.resume.{name}": "confirm" for name in ("contact", "education", "experience", "skills")}
        packet, _ = self.drive(self.run_guided(), base, "setup.quick.filters")
        prompt = packet["questions"][0]["prompt"]["en"]
        self.assertIn("ITAR", prompt)
        self.assertIn("title must include: intern", prompt)
        self.reply(packet, [self.entry(packet["questions"][0], "confirm")])
        filters = self.document()["filters"]
        self.assertEqual((filters["jd_exclude"], filters["title_include"]), (ALL_JD, ["intern"]))
        self.assertIn("senior", filters["title_exclude"])

    def test_never_derived_empty_filters_are_not_treated_as_edits(self):
        quick = self.module("guided_quick")
        groups = self.module("intake").blank_intake()["groups"]
        groups["authorization"]["sponsorship_future"] = True
        groups["targets"]["job_types"] = ["internship"]
        groups["filters"]["title_exclude"] = ["contract"]
        state = {"document": {"groups": groups}, "quick_baseline": quick.baseline(groups)}
        review = {"quick_step": "filters"}
        quick.refresh(state, review)
        self.assertEqual(review["_draft"]["filters.jd_exclude"], ALL_JD)
        self.assertEqual(review["_draft"]["filters.title_include"], ["intern"])
        self.assertEqual(review["_draft"]["filters.title_exclude"], ["contract"], "A saved edit is still kept")

    def confirmed_and_restarted(self, answers, edits=()):
        self.confirmed_filters(answers)
        for field, value, status in edits:
            packet = self.run_guided(edit=field)
            # A typed empty list or the native Unknown option clears a multi-choice.
            entry = {"question_id": field, "status": status}
            if status == "answered":
                entry["value"] = value
            self.reply(packet, [entry])
            self.assertEqual(self.document()[field.split(".")[0]][field.split(".")[1]], value)
        self.run_guided(finish=True)
        self.assertTrue(self.run_guided(confirm=True)["applied"])
        return self.run_guided(restart=True)

    def test_confirmed_intake_records_what_the_answers_derived(self):
        self.confirmed_and_restarted({"authorization.sponsorship_now": True}, [("filters.jd_exclude", [], "answered")])
        saved = json.loads((self.root / "intake-import.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["derived_filters"]["filters.jd_exclude"], ALL_JD)
        self.assertEqual(saved["groups"]["filters"]["jd_exclude"], [])

    def test_saved_filter_clear_survives_a_restarted_session(self):
        self.saved_clear_survives_a_restart("answered")

    def test_filter_cleared_with_unknown_survives_a_restarted_session(self):
        self.saved_clear_survives_a_restart("unknown")

    def saved_clear_survives_a_restart(self, status):
        packet = self.confirmed_and_restarted({"authorization.sponsorship_now": True},
                                              [("filters.jd_exclude", [], status)])
        self.assertEqual(self.document()["filters"]["jd_exclude"], [])
        base = {f"setup.quick.resume.{name}": "confirm" for name in ("contact", "education", "experience", "skills")}
        packet, _ = self.drive(packet, base, "setup.quick.filters")
        review = next(q for q in packet["questions"] if q["id"] == "setup.quick.filters")
        self.assertNotIn("ITAR", review["prompt"]["en"], "A saved clear is an edit, not a legacy blank")
        self.reply(packet, [self.entry(q, "confirm") if q is review else self.entry(q, status="notasked")
                            for q in packet["questions"]])
        self.assertEqual(self.document()["filters"]["jd_exclude"], [])

    def test_restarted_session_still_syncs_unedited_filters(self):
        self.confirmed_and_restarted({"authorization.sponsorship_now": True})
        self.assertEqual(self.document()["filters"]["jd_exclude"], ALL_JD)
        self.edit_answer("authorization.sponsorship_now", status="unknown")
        self.assertEqual(self.document()["filters"]["jd_exclude"], [],
                         "The derivation saved with the intake lets a new session resync")

    def change_sponsorship(self, *answers, confirm=False):
        """Change the sponsorship answer in turn; optionally confirm each reopened filter review."""
        for value in answers:
            packet = (self.edit_answer("authorization.sponsorship_now", status="unknown") if value is None
                      else self.edit_answer("authorization.sponsorship_now", value))
            review = packet["questions"][0]
            self.assertEqual(review["id"], "setup.quick.filters", "Changed answers show the filter review again")
            if confirm:
                self.reply(packet, [self.entry(review, "confirm")])
        return review

    def test_saved_clear_survives_sponsorship_unknown_and_back_after_restart(self):
        self.confirmed_and_restarted({"authorization.sponsorship_now": True}, [("filters.jd_exclude", [], "unknown")])
        review = self.change_sponsorship(None, True)
        self.assertNotIn("ITAR", review["prompt"]["en"])
        self.assertEqual(self.document()["filters"]["jd_exclude"], [],
                         "A clear that once matched the derivation is still the user's edit")

    def test_saved_clear_survives_confirmed_reviews_while_sponsorship_changes(self):
        self.clear_survives_confirmed_reviews("answered")

    def test_unknown_clear_survives_confirmed_reviews_while_sponsorship_changes(self):
        self.clear_survives_confirmed_reviews("unknown")

    def clear_survives_confirmed_reviews(self, status):
        self.confirmed_and_restarted({"authorization.sponsorship_now": True}, [("filters.jd_exclude", [], status)])
        review = self.change_sponsorship(None, True, confirm=True)
        self.assertNotIn("ITAR", review["prompt"]["en"])
        self.assertEqual(self.document()["filters"]["jd_exclude"], [])

    def test_clear_in_the_same_session_survives_sponsorship_unknown_and_back(self):
        self.confirmed_filters({"authorization.sponsorship_now": True})
        packet = self.run_guided(edit="filters.jd_exclude")
        self.reply(packet, [{"question_id": "filters.jd_exclude", "status": "answered", "value": []}])
        self.change_sponsorship(None, True, confirm=True)
        self.assertEqual(self.document()["filters"]["jd_exclude"], [])

    def test_edit_mark_is_saved_and_survives_a_second_restart(self):
        self.confirmed_and_restarted({"authorization.sponsorship_now": True}, [("filters.jd_exclude", [], "answered")])
        self.change_sponsorship(None)
        self.run_guided(finish=True)
        self.assertTrue(self.run_guided(confirm=True)["applied"])
        saved = json.loads((self.root / "intake-import.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["edited_filters"], ["filters.jd_exclude"])
        self.assertEqual(saved["derived_filters"]["filters.jd_exclude"], [])
        self.run_guided(restart=True)
        self.change_sponsorship(True, confirm=True)
        self.assertEqual(self.document()["filters"]["jd_exclude"], [])

    def test_unedited_filters_follow_sponsorship_unknown_and_back_after_restart(self):
        self.confirmed_and_restarted({"authorization.sponsorship_now": True})
        self.change_sponsorship(None, True, confirm=True)
        self.assertEqual(self.document()["filters"]["jd_exclude"], ALL_JD)
        self.assertNotIn("edited_filters", {k for k, v in self.progress()["document"].items() if v})

    def test_malformed_edit_marks_are_ignored(self):
        quick = self.module("guided_quick")
        groups = self.module("intake").blank_intake()["groups"]
        groups["authorization"]["sponsorship_now"] = True
        groups["filters"]["jd_exclude"] = list(ALL_JD)
        for marks in ("filters.jd_exclude", ["filters.posted_within_days"], [3], {"filters.jd_exclude": True}):
            state = {"document": {"groups": groups, "derived_filters": quick.baseline(groups), "edited_filters": marks}}
            self.assertEqual(quick.edited(state), set(), marks)

    def test_new_sessions_record_the_loaded_filter_baseline(self):
        self.prepared()
        self.run_guided()
        self.assertEqual(self.progress()["quick_baseline"],
                         {"filters.jd_exclude": [], "filters.title_include": [], "filters.title_exclude": []})

    def test_unrelated_edit_does_not_requeue_filters(self):
        self.confirmed_filters({"authorization.sponsorship_now": True})
        packet = self.edit_answer("contact.city", "Springfield")
        self.assertNotIn("setup.quick.filters", [q["id"] for q in packet["questions"]])

    def test_terminal_resumes_and_engine_seed_the_draft(self):
        self.prepared()
        self.run_guided()
        runtime, resumes = self.document()["runtime"], self.document()["resumes"]
        self.assertEqual(runtime["apply_engine"], "claude")
        self.assertTrue(resumes["main_pdf"].endswith("alex.txt"))

    def test_cleared_alternatives_reach_config_and_kept_ones_keep_tags(self):
        self.prepared()
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        other = self.root / "resumes/other.txt"
        other.write_text("Alex Rivera", encoding="utf-8")
        config["resumes"]["alternatives"] = [{"id": "other", "path": str(other), "tags": ["Data"]}]
        self.write_json("config.json", config)
        self.assertEqual(self.document_after_seed()["resumes"]["alternatives"], [str(other)])
        self.run_guided(finish=True)
        self.assertTrue(self.run_guided(confirm=True)["applied"])
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["resumes"]["alternatives"], [{"id": "other", "path": str(other), "tags": ["Data"]}])
        packet = self.run_guided(edit="resumes.alternatives")
        question = next(q for q in packet["questions"] if q["id"] == "resumes.alternatives")
        self.reply(packet, [self.entry(question, status="unknown")])
        self.assertEqual(self.document()["resumes"]["alternatives"], [])
        self.run_guided(finish=True)
        self.assertTrue(self.run_guided(confirm=True)["applied"])
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["resumes"]["alternatives"], [])
        self.assertEqual(config["runtime"]["jobs_per_run"], 10)

    def document_after_seed(self):
        self.run_guided()
        return self.document()


class IntakeCompatibilityTests(ReleaseCase):
    def test_documents_without_the_new_filter_fields_still_validate(self):
        intake = self.module("intake")
        document = intake.blank_intake()
        for name in ("posted_within_days", "title_include", "title_exclude", "jd_exclude"):
            del document["groups"]["filters"][name]
        validated = intake._validate_document(document)
        self.assertEqual(validated["groups"]["filters"]["jd_exclude"], [])
        self.assertIsNone(validated["groups"]["filters"]["posted_within_days"])

    def test_new_filter_fields_are_typed(self):
        intake = self.module("intake")
        document = intake.blank_intake()
        document["groups"]["filters"]["posted_within_days"] = "seven"
        with self.assertRaises(ValueError):
            intake._validate_document(document)
        document["groups"]["filters"].update(posted_within_days=7, jd_exclude=["telepathy"])
        with self.assertRaises(ValueError):
            intake._validate_document(document)

    def test_catalog_offers_the_jd_rules_as_choices(self):
        question = self.module("guided_catalog").index()["filters.jd_exclude"]
        self.assertEqual(question["kind"], "multi_choice")
        self.assertEqual([c["value"] for c in question["choices"]], ALL_JD)


if __name__ == "__main__":
    unittest.main()
