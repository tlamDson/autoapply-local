"""Offline lifecycle regressions, driven by issued native question packets."""

import copy
import hashlib
import unittest
from unittest.mock import patch

import test_guided_onboarding as onboarding
from test_support import ReleaseCase, SCOPE, fact


class LifecycleHarness(ReleaseCase):
    # Reuse helpers without inheriting/discovering the frozen test cases twice.
    prepared = onboarding.GuidedOnboardingTests.prepared
    read = onboarding.GuidedOnboardingTests.read
    progress = onboarding.GuidedOnboardingTests.progress
    write_intake = onboarding.GuidedOnboardingTests.write_intake
    state_bytes = onboarding.GuidedOnboardingTests.state_bytes
    tree_bytes = onboarding.GuidedOnboardingTests.tree_bytes
    cli = onboarding.GuidedOnboardingTests.cli
    start = onboarding.GuidedOnboardingTests.start
    catalog_id = onboarding.GuidedOnboardingTests.catalog_id
    edit = onboarding.GuidedOnboardingTests.edit
    entry = onboarding.GuidedOnboardingTests.entry
    payload = onboarding.GuidedOnboardingTests.payload
    answer = onboarding.GuidedOnboardingTests.answer
    set_field = onboarding.GuidedOnboardingTests.set_field
    finish = onboarding.GuidedOnboardingTests.finish
    document = onboarding.GuidedOnboardingTests.document
    add_record = onboarding.GuidedOnboardingTests.add_record

    def popup(self, packet, question_id, value, ui="codex"):
        """Find a real paginated control/choice, then submit its actual binding."""
        for _ in range(100):
            question = next(q for q in packet["questions"] if q["id"] == question_id)
            binding = next(b for b in packet["ui"]["bindings"] if b["question_id"] == question_id)
            if ui == "codex":
                self.assertTrue(2 <= len(binding["option_ids"]) <= 3)
            choices = {c["value"]: c for c in question["choices"]}
            if value in choices:
                self.assertIn(choices[value]["id"], binding["option_ids"])
                self.answer(packet, [self.entry(question, value)])
                return self.start(ui=ui)
            self.assertIn("more", choices, f"No reachable {value!r} native control")
            self.answer(packet, [self.entry(question, "more")])
            packet = self.start(ui=ui)
        self.fail(f"Native control {value!r} was not reachable")

    def confirm_document(self, document):
        path = self.write_intake(document)
        intake = self.module("intake")
        intake.apply_import(self.root, intake.preview_import(self.root, path), confirm=True)


class GuidedLifecycleTests(LifecycleHarness):
    def test_confirm_refreshes_baseline_consumes_preview_and_allows_next_edit(self):
        self.prepared()
        self.set_field("contact.first_name", "Alex")
        self.finish()
        self.assertTrue(self.cli("--confirm")["applied"])
        state = self.progress()
        self.assertIsNone(state["preview"], "Successful confirmation consumes its saved preview")
        for name, digest in state["references"].items():
            path = self.root / name
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None)
        before = self.tree_bytes()
        self.cli("--confirm", expected=2)
        self.assertEqual(self.tree_bytes(), before)
        self.start()
        self.set_field("contact.first_name", "Avery")
        self.cli("--confirm", expected=2)
        self.finish()
        self.cli("--confirm")
        self.assertEqual(onboarding.raw(self.read("profile.json")["contact"]["first_name"]), "Avery")
        self.assertFalse(self.read("reviews.json")[0]["valid"])

    def test_failed_apply_does_not_refresh_or_consume_progress(self):
        self.prepared()
        self.set_field("contact.first_name", "Alex")
        self.finish()
        before = self.tree_bytes()
        with patch.object(self.module("intake"), "apply_import", side_effect=ValueError("synthetic failure")):
            self.cli("--confirm", expected=2)
        self.assertEqual(self.tree_bytes(), before)
        self.assertIsNotNone(self.progress()["preview"])

    def test_blank_editable_seed_preserves_newer_confirmed_facts_and_answers(self):
        self.prepared()
        document = self.module("intake").blank_intake()
        document["groups"]["contact"]["first_name"] = fact("New confirmed name")
        document["groups"]["targets"]["roles"] = ["backend", "security"]
        document["groups"]["resources"].update(gmail_opt_in=True, gmail_account="alex@example.invalid", gmail_permissions=["search", "read"])
        document["answers"] = [{"question": SCOPE["question"], **fact(True, scope=SCOPE)}]
        self.confirm_document(document)
        blank = self.module("intake").blank_intake()
        self.write_intake(blank)
        editable = (self.root / "user/profile-intake.md").read_bytes()
        self.set_field("contact.city", "Example City")
        finished = self.finish()
        groups = self.document(finished)["groups"]
        self.assertEqual(groups["contact"]["first_name"], document["groups"]["contact"]["first_name"])
        self.assertEqual(groups["targets"]["roles"], ["backend", "security"])
        self.assertEqual(groups["resources"]["gmail_permissions"], ["search", "read"])
        self.assertEqual(self.document(finished)["answers"], document["answers"])
        self.cli("--confirm")
        self.assertEqual((self.root / "user/profile-intake.md").read_bytes(), editable)
        self.assertEqual(self.read("config.json")["resources"]["gmail_permissions"], ["search", "read"])

    def test_stale_nonblank_seed_exposes_native_conflict_and_keeps_draft_file(self):
        self.prepared()
        document = self.module("intake").blank_intake()
        document["groups"]["contact"]["first_name"] = "Confirmed"
        self.confirm_document(document)
        stale = copy.deepcopy(document)
        stale["groups"]["contact"].update(first_name="Draft", city="New draft city")
        self.write_intake(stale)
        editable = (self.root / "user/profile-intake.md").read_bytes()
        packet = self.start()
        conflicts = [q for q in packet["questions"] if q.get("seed_conflict")]
        self.assertTrue(conflicts, "Conflicting editable facts need a real native choice")
        self.assertEqual(self.progress()["document"]["groups"]["contact"]["first_name"], "Confirmed")
        q = next(q for q in conflicts if q["field"] == "contact.first_name")
        packet = self.popup(packet, q["id"], "use_draft", ui="codex-async")
        groups = self.document(self.finish())["groups"]
        self.assertEqual(groups["contact"]["first_name"], "Draft")
        self.assertEqual(groups["contact"]["city"], "New draft city")
        self.assertEqual((self.root / "user/profile-intake.md").read_bytes(), editable)

    def test_normalized_profile_and_config_newer_than_import_are_preserved(self):
        self.prepared()
        document = self.module("intake").blank_intake()
        document["groups"]["contact"]["first_name"] = "Old"
        self.confirm_document(document)
        profile = self.read("profile.json")
        profile["contact"]["first_name"] = fact("Latest")
        self.write_json("profile.json", profile)
        config = self.read("config.json")
        config["resources"]["gmail_lookback"] = 19
        self.write_json("config.json", config)
        self.set_field("contact.city", "Example City")
        groups = self.document(self.finish())["groups"]
        self.assertEqual(onboarding.raw(groups["contact"]["first_name"]), "Latest")
        self.assertEqual(groups["resources"]["gmail_lookback"], 19)

    def test_add_second_question_preserves_unanswered_first_question(self):
        self.prepared()
        packet = self.start()
        packet = self.answer(packet, [self.entry(q, status="unknown") for q in packet["questions"]])
        # The natural second batch contains two multi-choice fields and mode-independent fields.
        while not any(q["field"] == "resumes.alternatives" for q in packet["questions"]):
            packet = self.answer(packet, [self.entry(q, status="notasked") for q in packet["questions"]])
        selected = next(q for q in packet["questions"] if q["field"] == "resumes.alternatives")
        pending = [q["id"] for q in packet["questions"] if q["id"] != selected["id"]]
        self.assertGreater([q["id"] for q in packet["questions"]].index(selected["id"]), 0)
        self.answer(packet, [self.entry(selected, "add")])
        queue_ids = [q["id"] for q in self.progress()["queue"]]
        for question_id in pending:
            self.assertEqual(queue_ids.count(question_id), 1, "Adding a later editor must preserve every pending question")
        self.assertEqual(queue_ids.count(selected["id"]), 1)

    def test_two_reordered_adds_keep_both_children_and_stable_record_bindings(self):
        self.prepared()
        packet = self.start()
        while not all(any(q["field"] == f for q in packet["questions"]) for f in ("targets.include_roles", "targets.exclude_roles")):
            packet = self.answer(packet, [self.entry(q, status="notasked") for q in packet["questions"]])
        selected = [q for q in packet["questions"] if q["kind"] == "list"]
        self.assertGreaterEqual(len(selected), 2)
        self.answer(packet, [self.entry(q, "add") for q in reversed(selected)])
        queue = self.progress()["queue"]
        # Issue both pending child packets before revisiting either by --edit.
        # Reading a queued ID alone is not a native host interaction.
        pending_ids = {c["id"] for c in queue if any(c["field"] == q["field"] + ".value" for q in selected)}
        for _ in range(20):
            packet = self.start()
            if pending_ids.issubset(self.progress()["issued"]):
                break
            self.answer(packet, [self.entry(q, status="notasked") for q in packet["questions"]])
        else:
            self.fail("Pending child questions were never issued")
        for q in selected:
            children = [c for c in queue if c["field"] == q["field"] + ".value"]
            self.assertEqual(len(children), 1)
            child = children[0]
            packet = self.start(edit=child["id"])
            self.answer(packet, [self.entry(packet["questions"][0], "Synthetic " + q["field"])])
        groups = self.progress()["document"]["groups"]
        for q in selected:
            group, field = q["field"].split(".")
            self.assertEqual(groups[group][field], ["Synthetic " + q["field"]])

    def test_natural_walk_covers_all_groups_and_add_done_without_losing_questions(self):
        self.prepared()
        packet = self.start()
        seen, expanded = set(), set()
        for _ in range(400):
            if packet["status"] == "draft_complete":
                break
            entries = []
            for q in packet["questions"]:
                seen.add(q["field"])
                if q["field"] in ("history.alternate_emails", "history.avoid_employers"):
                    action = "done" if q["field"] in expanded else "add"
                    expanded.add(q["field"])
                    entries.append(self.entry(q, action))
                elif q["field"].endswith(".value") and q["field"].startswith("history."):
                    entries.append(self.entry(q, "alex@example.invalid" if "emails" in q["field"] else "Example Labs"))
                else:
                    entries.append(self.entry(q, status="notasked"))
            packet = self.answer(packet, list(reversed(entries)))
        else:
            self.fail("Natural setup walk did not terminate")
        top = {q["field"] for q in self.module("guided_catalog").questions()}
        self.assertFalse(top - seen, sorted(top - seen))
        self.assertEqual({f.split(".")[0] for f in seen}, set(self.module("intake").FIELDS))
        self.assertEqual(self.progress()["document"]["groups"]["history"]["alternate_emails"], ["alex@example.invalid"])
        self.finish()

    def test_boards_field_by_field_unknown_optional_and_inapplicable_can_confirm(self):
        self.prepared()
        expected = []
        for ats, fields in (("greenhouse", {"board": "example"}), ("rippling", {"board": "example"}),
                            ("workday", {"host": "example.wd5.myworkdayjobs.com", "tenant": "example", "site": "Careers"})):
            self.add_record("resources.boards", {"ats": ats, "company": "Example Labs", **fields})
            expected.append({"ats": ats, "company": "Example Labs", **fields})
        self.assertEqual(self.document(self.finish())["groups"]["resources"]["boards"], expected)
        self.cli("--confirm")
        self.assertEqual(self.read("config.json")["boards"], expected)

    def test_board_missing_required_blocks_finish_without_normalized_writes(self):
        before = self.prepared()
        self.add_record("resources.boards", {"ats": "greenhouse", "company": "Example Labs"})
        draft = self.progress()
        self.cli("--finish", "--ui", "codex-async", expected=2)
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.progress(), draft)

    def test_roles_and_permissions_can_clear_then_replace_via_capped_native_controls(self):
        self.prepared()
        self.set_field("targets.roles", ["backend", "frontend"])
        self.set_field("resources.gmail_permissions", ["search", "read"])
        for field, wanted in (("targets.roles", "security"), ("resources.gmail_permissions", "read")):
            packet = self.edit(field, ui="codex")
            question_id = packet["questions"][0]["id"]
            packet = self.popup(packet, question_id, "clear")
            self.assertEqual(self.progress()["document"]["groups"][field.split(".")[0]][field.split(".")[1]], [])
            packet = self.popup(packet, question_id, wanted)
            self.popup(packet, question_id, "done")
        groups = self.document(self.finish())["groups"]
        self.assertEqual(groups["targets"]["roles"], ["security"])
        self.assertEqual(groups["resources"]["gmail_permissions"], ["read"])
        self.cli("--confirm")
        self.assertEqual(self.read("profile.json")["targets"]["roles"], ["security"])
        self.assertEqual(self.read("config.json")["resources"]["gmail_permissions"], ["read"])

    def test_async_native_selection_replaces_existing_list_including_empty_selection(self):
        self.prepared()
        self.set_field("targets.roles", ["backend", "frontend"])
        self.set_field("targets.roles", ["security"])
        self.assertEqual(self.progress()["document"]["groups"]["targets"]["roles"], ["security"])
        self.set_field("resources.gmail_permissions", ["read", "search"])
        self.set_field("resources.gmail_permissions", [])
        self.assertEqual(self.progress()["document"]["groups"]["resources"]["gmail_permissions"], [])
        self.assertIs(self.progress()["document"]["groups"]["resources"]["gmail_opt_in"], False)

    def test_seeded_graduation_envelope_is_not_wrapped_or_changed_on_finish_confirm(self):
        document = self.module("intake").blank_intake()
        graduation = fact({"value": "2031-05", "precision": "month", "kind": "expected"}, source_id="synthetic:graduation")
        document["groups"]["education"]["records"] = [{"id": "edu:1", "school": "Example University", "graduation": graduation}]
        self.prepared(document=document)
        self.start()
        finished = self.finish()
        self.assertEqual(self.document(finished)["groups"]["education"]["records"][0]["graduation"], graduation)
        self.cli("--confirm")
        self.assertEqual(self.read("profile.json")["education"][0]["graduation"], graduation)

    def test_exact_boolean_integer_and_finite_number_guards_preserve_progress(self):
        self.prepared()
        for field, value in (("resources.lanes", True), ("authorization.authorized_now", 1),
                             ("compensation.minimum", float("inf")), ("compensation.minimum", float("nan"))):
            with self.subTest(field=field, value=value):
                packet = self.edit(field)
                before = self.tree_bytes()
                self.answer(packet, [{"question_id": packet["questions"][0]["id"], "status": "answered", "value": value}], expected=2)
                self.assertEqual(self.tree_bytes(), before)


if __name__ == "__main__":
    unittest.main()
