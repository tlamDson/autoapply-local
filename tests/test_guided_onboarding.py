"""Offline RED contracts for native-popup onboarding. No implementation edits.

Giả định của người thực thi (test-only): CLI success/invalid input exits 0/2.
Guided packets expose schema_version/session_id/revision/batch_id/questions;
each question has id, field, choices and optional record_id. Native packets
have ui={tool,arguments,bindings}, bindings in native question order with
question_id and option_ids. --ui none returns status=ui_required and no tool.
Answer files repeat packet metadata and contain answers with question_id,
status=answered|unknown|not_applicable|declined|notasked and either value or
option_ids. Navigation choices use values add/done/more; record editors issue
one record at a time. --edit takes catalog IDs or previously issued record
question IDs. Finish returns intake/preview, storing exactly preview in
user/setup-progress.json. Statuses are in progress.field_status keyed by stable
question ID; seeded documents are in progress.document. Dependency hashes are
in progress.references keyed by root-relative filenames (missing => null).
These minimal API choices live here because only new test files may be edited.
They do not certify native host rendering; actual tools stay host-owned.
"""

import copy
import hashlib
import io
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from test_support import RELEASE, SCOPE, ReleaseCase, candidate, fact


META = ("schema_version", "session_id", "revision", "batch_id")
NORMALIZED = ("profile.json", "config.json", "answers.json", "reviews.json", "intake-import.json")
DEPENDENCIES = ("user/profile-intake.md", "profile.json", "config.json", "answers.json", "intake-import.json")


def raw(value):
    if isinstance(value, dict) and "value" in value and "status" in value:
        return value["value"]
    return value


class GuidedOnboardingTests(ReleaseCase):
    def prepared(self, document=None, profile=None):
        # All snapshots are AFTER bootstrap's documented safe defaults.
        self.module("runtime").setup(root=self.root)
        if document is not None:
            self.write_json("intake-import.json", document)
            self.write_intake(document)
        if profile is not None:
            self.write_json("profile.json", profile)
        self.write_json("reviews.json", [{"id": "synthetic-review", "valid": True}])
        # Missing production module is an intentional RED, never a skip.
        self.module("guided")
        return self.state_bytes()

    def read(self, relative):
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def progress(self):
        return self.read("user/setup-progress.json")

    def write_intake(self, document):
        path = self.root / "user/profile-intake.md"
        path.write_text("```json\n" + json.dumps(document) + "\n```\n", encoding="utf-8")
        return path

    def state_bytes(self):
        return {name: (self.root / name).read_bytes() if (self.root / name).exists() else None for name in NORMALIZED}

    def tree_bytes(self):
        # Lock files are coordination artifacts; all data/backup files count.
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file() and not p.name.endswith(".lock")}

    def cli(self, *arguments, expected=0, services=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(self.module("engines").Engine, "run", side_effect=AssertionError("No engine interviews")), \
                patch("subprocess.Popen", side_effect=AssertionError("No external process")), \
                patch("socket.create_connection", side_effect=AssertionError("No network")):
            code = self.module("cli").main(
                ["--user-dir", str(self.root), "setup", *map(str, arguments)],
                services=services, stdout=out, stderr=err,
            )
        self.assertEqual(code, expected, err.getvalue() + out.getvalue())
        return json.loads(out.getvalue()) if expected == 0 else err.getvalue()

    def start(self, *, ui="codex-async", edit=None):
        arguments = ["--guided", "--ui", ui]
        if edit is not None:
            arguments += ["--edit", edit]
        return self.cli(*arguments)

    def catalog_id(self, field):
        from test_guided_catalog import walk
        return next(q["id"] for q in walk(self.module("guided_catalog").questions()) if q["field"] == field)

    def edit(self, field, ui="codex-async"):
        return self.start(ui=ui, edit=self.catalog_id(field))

    def entry(self, question, value=None, status="answered"):
        result = {"question_id": question["id"], "status": status}
        if status != "answered":
            return result
        values = value if question.get("kind") == "multi_choice" and isinstance(value, list) else [value]
        choices = question.get("choices", [])
        matched = [[c for c in choices if type(c.get("value")) is type(v) and c.get("value") == v] for v in values]
        if values and all(matched):
            result["option_ids"] = [items[0]["id"] for items in matched]
        else:
            result["value"] = value
        return result

    def payload(self, packet, entries):
        return {**{key: packet[key] for key in META}, "answers": entries}

    def answer(self, packet, entries, expected=0, **extra):
        # Host input is outside runtime root: it must never choose output paths.
        path = self.root.parent / (self.root.name + "-host-response.json")
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text(json.dumps({**self.payload(packet, entries), **extra}), encoding="utf-8")
        return self.cli("--answer", path, "--ui", "codex-async", expected=expected)

    def set_field(self, field, value=None, status="answered"):
        packet = self.edit(field)
        question = next(q for q in packet["questions"] if q["field"] == field)
        result = self.answer(packet, [self.entry(question, value, status)])
        return question, result

    def finish(self):
        return self.cli("--finish", "--ui", "codex-async")

    def document(self, finished):
        intake_path = Path(finished["intake"])
        self.assertTrue(intake_path.resolve().is_relative_to((self.root / "user").resolve()))
        return self.module("intake").read_intake(intake_path)

    def test_existing_setup_keeps_service_dispatch_and_ready_bootstrap(self):
        service = Mock(return_value={"status": "ready"})
        self.assertEqual(self.cli(services={"setup": service}), {"status": "ready"})
        service.assert_called_once_with(root=self.root)
        self.assertEqual(self.cli()["status"], "ready")
        before = self.state_bytes()
        second = self.cli()
        self.assertEqual(second["status"], "ready")
        self.assertIn("--guided", json.dumps(second))
        self.assertEqual(self.state_bytes(), before)

    def test_guided_actions_are_mutually_exclusive_and_edit_requires_guided(self):
        for arguments in (("--guided", "--finish"), ("--guided", "--confirm"),
                          ("--answer", "answer.json", "--finish"), ("--finish", "--confirm"),
                          ("--edit", "contact.first_name")):
            with self.subTest(arguments=arguments):
                before = self.tree_bytes()
                self.cli(*arguments, expected=2)
                self.assertEqual(self.tree_bytes(), before)

    def test_start_is_progressive_resumable_and_never_treats_defaults_as_answers(self):
        baseline = self.prepared()
        first = self.start()
        for key in META:
            self.assertIn(key, first)
        self.assertEqual(first["schema_version"], 1)
        self.assertTrue(first["session_id"])
        self.assertEqual({q["field"] for q in first["questions"]},
                         {"runtime.mode", "runtime.language", "runtime.apply_engine"})
        self.assertLessEqual(len(first["questions"]), 3)
        progress = self.progress()
        statuses = progress["field_status"]
        for field in ("resources.gmail_opt_in", "resources.lanes"):
            self.assertEqual(statuses[self.catalog_id(field)], "notasked")
        restarted = self.start()
        self.assertEqual({key: restarted[key] for key in META}, {key: first[key] for key in META})
        self.assertEqual(restarted["questions"], first["questions"])
        self.assertEqual(self.state_bytes(), baseline)

    def test_start_hashes_all_dependencies_including_expected_missing_import(self):
        self.prepared()
        self.start()
        references = self.progress()["references"]
        for relative in DEPENDENCIES:
            path = self.root / relative
            expected = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            self.assertEqual(references[relative], expected)
        self.assertIsNone(references["intake-import.json"])

    def test_cli_roundtrip_answer_restart_edit_partial_finish_preview_and_explicit_confirm(self):
        baseline = self.prepared()
        first = self.start()
        self.answer(first, [self.entry(q, status="unknown") for q in first["questions"]])
        _, after_name = self.set_field("contact.first_name", "Alex")
        self.assertGreater(after_name["revision"], first["revision"])
        self.assertEqual(self.start()["session_id"], first["session_id"])
        self.set_field("contact.first_name", "Avery")
        self.set_field("contact.last_name", "Example")
        self.set_field("targets.roles", ["backend", "frontend"])
        self.set_field("resources.gmail_opt_in", False)
        self.set_field("resources.lanes", 1)
        finished = self.finish()
        document = self.document(finished)
        self.module("intake")._validate_document(copy.deepcopy(document))
        self.assertEqual(len(document["groups"]), 14)
        self.assertEqual(raw(document["groups"]["contact"]["first_name"]), "Avery")
        self.assertEqual(raw(document["groups"]["contact"]["last_name"]), "Example")
        self.assertEqual(document["groups"]["targets"]["roles"], ["backend", "frontend"])
        self.assertEqual(self.progress()["preview"], finished["preview"])
        self.assertEqual(self.module("intake").preview_import(self.root, finished["intake"]), finished["preview"])
        self.assertEqual(self.state_bytes(), baseline)
        preview = copy.deepcopy(finished["preview"])
        intake = self.module("intake")
        original = intake.apply_import
        with patch.object(intake, "apply_import", wraps=original) as apply, \
                patch.object(intake, "preview_import", side_effect=AssertionError("Do not replace viewed preview")):
            confirmed = self.cli("--confirm")
        self.assertIs(confirmed["applied"], True)
        self.assertEqual(apply.call_count, 1)
        arguments, keywords = apply.call_args
        self.assertEqual(Path(arguments[0]), self.root)
        self.assertEqual(arguments[1], preview)
        self.assertIs(keywords["confirm"], True)
        self.assertTrue(callable(keywords["precondition"]))
        self.assertEqual(self.read("profile.json")["revision"], 2)
        self.assertEqual(raw(self.read("profile.json")["contact"]["first_name"]), "Avery")
        self.assertEqual(self.read("profile.json")["targets"]["roles"], ["backend", "frontend"])
        self.assertEqual(self.read("config.json")["resources"]["lanes"], 1)

    def test_unknown_skip_decline_not_applicable_and_unasked_remain_distinct(self):
        baseline = self.prepared()
        self.start()
        supplied = {}
        for field, status in (("authorization.authorized_now", "unknown"),
                              ("authorization.sponsorship_now", "notasked"),
                              ("compensation.eeo", "declined"),
                              ("contact.portfolio", "not_applicable")):
            question, _ = self.set_field(field, status=status)
            supplied[question["id"]] = status
        finished = self.finish()
        for question_id, status in supplied.items():
            self.assertEqual(self.progress()["field_status"][question_id], status)
        document = self.document(finished)
        for name in ("authorized_now", "sponsorship_now", "authorized_at_start", "sponsorship_future"):
            self.assertIsNone(raw(document["groups"]["authorization"][name]))
        self.assertEqual(self.state_bytes(), baseline)

    def test_blank_answer_and_free_text_yes_cannot_become_false_or_a_factual_enum(self):
        self.prepared()
        for value in ("", "yes, I think so", "false", 0, None):
            with self.subTest(value=value):
                packet = self.edit("authorization.authorized_now")
                question = next(q for q in packet["questions"] if q["field"] == "authorization.authorized_now")
                before = self.tree_bytes()
                self.answer(packet, [{"question_id": question["id"], "status": "answered", "value": value}], expected=2)
                self.assertEqual(self.tree_bytes(), before)

    def test_resume_paths_are_explicit_absolute_and_draft_only(self):
        baseline = self.prepared()
        main = self.root / "chosen resume.pdf"
        newest = self.root / "newer resume.pdf"
        main.write_bytes(b"%PDF-1.4\n% synthetic selected resume\n%%EOF")
        newest.write_bytes(b"%PDF-1.4\n% synthetic newer resume\n%%EOF")
        self.set_field("resumes.main_pdf", str(main))
        finished = self.finish()
        self.assertEqual(self.document(finished)["groups"]["resumes"]["main_pdf"], str(main.resolve()))
        self.assertEqual(self.state_bytes(), baseline)
        self.assertEqual(self.read("profile.json")["authorization"], [])
        packet = self.edit("resumes.main_pdf")
        question = next(q for q in packet["questions"] if q["field"] == "resumes.main_pdf")
        before = self.tree_bytes()
        self.answer(packet, [self.entry(question, str(self.root / "missing.pdf"))], expected=2)
        self.assertEqual(self.tree_bytes(), before)

    def test_seed_preserves_all_groups_existing_fields_answers_and_confirmed_profile(self):
        intake = self.module("intake")
        document = intake.blank_intake()
        document["groups"]["contact"]["first_name"] = "Existing"
        document["groups"]["targets"]["adjacent_roles"] = ["platform", "SRE"]
        document["groups"]["logistics"]["relocation"] = "case_by_case"
        document["groups"]["history"]["alternate_emails"] = ["prior@example.invalid"]
        profile = candidate()
        question = "Authorized at job start?"
        profile["answers"] = [{"question": question, **fact(True, scope={**SCOPE, "question": question})}]
        document["answers"] = copy.deepcopy(profile["answers"])
        baseline = self.prepared(document, profile)
        self.start()
        seeded = self.progress()["document"]
        self.assertEqual(seeded, document)
        self.set_field("contact.first_name", "Edited")
        final = self.document(self.finish())
        expected = copy.deepcopy(document)
        expected["groups"]["contact"]["first_name"] = final["groups"]["contact"]["first_name"]
        self.assertEqual(final, expected)
        self.assertEqual(raw(final["groups"]["contact"]["first_name"]), "Edited")
        self.assertEqual(self.state_bytes(), baseline)

    def test_native_configuration_values_and_explicit_resource_opt_in_survive_confirm(self):
        self.prepared()
        self.start()
        self.set_field("runtime.apply_engine", "codex")
        self.set_field("runtime.tailor_engine", "codex")
        self.set_field("runtime.language", "vi")
        self.set_field("targets.roles", ["backend", "platform"])
        self.set_field("tailor.enabled", True)
        self.set_field("tailor.scope", "priority")
        self.set_field("tailor.pages", 2)
        self.set_field("tailor.allow_rewrite", False)
        self.set_field("resources.gmail_opt_in", True)
        self.set_field("resources.gmail_account", "selected@example.invalid")
        self.set_field("resources.gmail_lookback", 14)
        self.set_field("resources.lanes", 3)
        finished = self.finish()
        draft = self.document(finished)
        self.assertEqual(draft["answers"], [])
        self.cli("--confirm")
        config = self.read("config.json")
        self.assertEqual(config["runtime"]["apply_engine"], "codex")
        self.assertEqual(config["runtime"]["tailor_engine"], "codex")
        self.assertEqual(config["runtime"]["language"], "vi")
        self.assertIs(config["tailor"]["enabled"], True)
        self.assertIs(config["tailor"]["allow_rewrite"], False)
        self.assertEqual(config["tailor"]["scope"], "priority")
        self.assertEqual(config["tailor"]["pages"], 2)
        self.assertIs(config["resources"]["gmail_opt_in"], True)
        self.assertEqual(config["resources"]["gmail_account"], "selected@example.invalid")
        self.assertEqual(config["resources"]["gmail_lookback"], 14)
        self.assertEqual(config["resources"]["lanes"], 3)
        self.assertEqual(self.read("profile.json")["targets"]["roles"], ["backend", "platform"])
        self.assertEqual(self.read("answers.json")["answers"], [])

    def test_skipped_international_and_tailor_sections_can_be_revisited_without_inferred_facts(self):
        baseline = self.prepared()
        self.set_field("international_student.status", status="notasked")
        self.set_field("tailor.enabled", status="notasked")
        first = self.document(self.finish())
        self.assertIsNone(raw(first["groups"]["international_student"]["status"]))
        for mechanism in ("cpt", "opt", "stem_opt"):
            value = first["groups"]["international_student"][mechanism]
            self.assertNotEqual(raw(value.get("status")), "granted")
        self.set_field("international_student.cpt.status", "pending")
        self.set_field("tailor.enabled", True)
        self.set_field("tailor.missing_facts", "ask")
        second = self.document(self.finish())
        self.assertEqual(raw(second["groups"]["international_student"]["cpt"]["status"]), "pending")
        self.assertIsNone(raw(second["groups"]["authorization"]["authorized_at_start"]))
        self.assertIs(second["groups"]["tailor"]["enabled"], True)
        self.assertEqual(second["groups"]["tailor"]["missing_facts"], "ask")
        self.assertEqual(self.state_bytes(), baseline)

    def test_history_files_remain_selected_paths_without_importing_applications(self):
        self.prepared()
        history = self.root / "synthetic-history.csv"
        history.write_text("company,role,status\nExample Labs,Intern,submitted\n", encoding="utf-8")
        baseline = self.tree_bytes()
        # Arbitrary list entries go through the item popup, never a raw array.
        self.add_record("history.import_files", {"value": str(history)})
        finished = self.finish()
        self.assertEqual(self.document(finished)["groups"]["history"]["import_files"], [str(history)])
        after = self.tree_bytes()
        for path, data in baseline.items():
            if "setup-progress" not in path:
                self.assertEqual(after[path], data, path)

    def add_record(self, field, values):
        """Exercise issued Add -> individual fields -> Done, not JSON injection."""
        packet = self.edit(field)
        editor = next(q for q in packet["questions"] if q["field"] == field)
        packet = self.answer(packet, [self.entry(editor, "add")])
        record_id = None
        issued = {}
        answered = set()
        for _ in range(160):
            self.assertLessEqual(len(packet["questions"]), 3)
            entries = []
            for question in packet["questions"]:
                if question["field"] == field:
                    self.assertEqual(answered, set(values), "Every supplied nested field must really be asked")
                    self.answer(packet, [self.entry(question, "done")])
                    return record_id, issued
                self.assertTrue(question["field"].startswith(field + "."), question)
                current_id = question.get("record_id")
                if current_id is not None:
                    if record_id is None:
                        record_id = current_id
                    self.assertEqual(current_id, record_id)
                suffix = question["field"][len(field) + 1:]
                issued[suffix] = question["id"]
                if suffix in values:
                    answered.add(suffix)
                    entries.append(self.entry(question, values[suffix]))
                else:
                    entries.append(self.entry(question, status="unknown"))
            self.assertTrue(entries, "Record editor ended without a Done choice")
            packet = self.answer(packet, entries)
        self.fail("Record editor did not terminate within a bounded number of batches")

    def test_two_education_programs_survive_restart_record_edit_and_partial_finish(self):
        baseline = self.prepared()
        first_id, first_issued = self.add_record("education.records", {
            "school": "Example University", "degree": "BS", "major": "Computer Science",
            "status": "current", "graduation.value": "2031-05", "graduation.precision": "month",
            "graduation.kind": "expected",
        })
        self.start()
        second_id, _ = self.add_record("education.records", {
            "school": "Second Example School", "degree": "Certificate", "major": "Computing",
            "status": "completed", "graduation.value": "2026", "graduation.precision": "year",
            "graduation.kind": "actual",
        })
        self.assertIsNotNone(first_id)
        self.assertNotEqual(first_id, second_id)
        packet = self.start(edit=first_issued["school"])
        question = next(q for q in packet["questions"] if q["id"] == first_issued["school"])
        self.answer(packet, [self.entry(question, "Renamed Example University")])
        self.set_field("education.current_program", first_id)
        records = self.document(self.finish())["groups"]["education"]["records"]
        self.assertEqual(len(records), 2)
        self.assertEqual([raw(r["school"]) for r in records], ["Renamed Example University", "Second Example School"])
        self.assertEqual(raw(records[0]["graduation"]), {"value": "2031-05", "precision": "month", "kind": "expected"})
        self.assertEqual(raw(records[1]["graduation"]), {"value": "2026", "precision": "year", "kind": "actual"})
        self.assertEqual(self.state_bytes(), baseline)

    def test_two_authorization_scopes_do_not_inherit_now_start_or_future_answers(self):
        baseline = self.prepared()
        self.add_record("authorization.records", {
            "country": "US", "job_type": "internship", "employer": "Example Labs",
            "effective_at": "2030-06-01", "mechanism": "CPT", "authorized_now": False,
            "authorized_at_start": True, "sponsorship_now": False, "sponsorship_future": True,
        })
        self.start()
        self.add_record("authorization.records", {
            "country": "US", "job_type": "experienced", "mechanism": "OPT",
        })
        self.set_field("international_student.cpt.status", "planned")
        self.set_field("international_student.opt.status", "pending")
        self.set_field("international_student.stem_opt.status", "not_applied")
        self.set_field("filters.no_sponsor_conflict", "exclude")
        document = self.document(self.finish())
        records = document["groups"]["authorization"]["records"]
        self.assertEqual(len(records), 2)
        self.assertEqual([raw(r["job_type"]) for r in records], ["internship", "experienced"])
        for field, expected in (("authorized_now", False), ("authorized_at_start", True),
                                ("sponsorship_now", False), ("sponsorship_future", True)):
            self.assertIs(raw(records[0][field]), expected)
            self.assertIsNone(raw(records[1].get(field)))
        international = document["groups"]["international_student"]
        self.assertEqual([raw(international[k]["status"]) for k in ("cpt", "opt", "stem_opt")],
                         ["planned", "pending", "not_applied"])
        self.assertEqual(document["answers"], [], "Generic onboarding wording cannot create exact form answers")
        self.assertIsNone(raw(international["work_auth_expiry"]))
        self.assertEqual(self.state_bytes(), baseline)

    def test_two_experience_records_metrics_skills_and_projects_are_entered_field_by_field(self):
        baseline = self.prepared()
        first_id, first_issued = self.add_record("experience.records", {
            "employer": "Example Systems", "title": "Intern", "description": "Built a Python API.",
        })
        self.start()
        second_id, _ = self.add_record("experience.records", {
            "employer": "Example Research", "title": "Assistant", "description": "Wrote test fixtures.",
        })
        self.assertNotEqual(first_id, second_id)
        packet = self.start(edit=first_issued["description"])
        question = next(q for q in packet["questions"] if q["id"] == first_issued["description"])
        self.answer(packet, [self.entry(question, "Built and tested a Python API.")])
        self.add_record("experience.unlisted_projects", {
            "name": "Example Tool", "description": "A local parser.", "contribution": "Implemented parsing.",
        })
        self.add_record("experience.skills", {"name": "Python", "evidence": "project", "source_id": "project:example"})
        self.add_record("experience.metrics", {"value": "About 12% faster in a local benchmark", "kind": "estimated"})
        experience = self.document(self.finish())["groups"]["experience"]
        self.assertEqual(len(experience["records"]), 2)
        self.assertEqual(raw(experience["records"][0]["description"]), "Built and tested a Python API.")
        self.assertEqual(raw(experience["records"][1]["employer"]), "Example Research")
        self.assertEqual(raw(experience["unlisted_projects"][0]["contribution"]), "Implemented parsing.")
        self.assertEqual(experience["skills"], [{"name": "Python", "evidence": "project", "source_id": "project:example"}])
        self.assertEqual(raw(experience["metrics"][0]["kind"]), "estimated")
        self.assertEqual(self.state_bytes(), baseline)

    def test_invalid_calendar_date_and_precision_are_rejected_before_mutation(self):
        self.prepared()
        for field, value in (("logistics.earliest_start", "2030-02-30"),
                             ("international_student.visa_expiry", "2030-13-01"),
                             ("logistics.latest_end", "2030-04-31")):
            with self.subTest(field=field, value=value):
                packet = self.edit(field)
                question = next(q for q in packet["questions"] if q["field"] == field)
                before = self.tree_bytes()
                self.answer(packet, [self.entry(question, value)], expected=2)
                self.assertEqual(self.tree_bytes(), before)

    def test_ui_none_preserves_progress_and_reports_native_ui_required(self):
        baseline = self.prepared()
        started = self.start()
        before = self.progress()
        result = self.start(ui="none")
        self.assertEqual(result["status"], "ui_required")
        self.assertIsNone(result.get("ui", {}).get("tool"))
        self.assertTrue(result["next"].strip())
        self.assertEqual(self.progress(), before)
        self.assertEqual(self.start()["session_id"], started["session_id"])
        self.assertEqual(self.state_bytes(), baseline)

    def test_timeout_interruption_and_preselected_defaults_do_not_advance_progress(self):
        self.prepared()
        packet = self.start()
        before = self.tree_bytes()
        for extra in ({"timeout": True}, {"interrupted": True}, {"preselected": True}):
            with self.subTest(extra=extra):
                self.answer(packet, [], expected=2, **extra)
                self.assertEqual(self.tree_bytes(), before)
        self.assertEqual(self.start()["batch_id"], packet["batch_id"])

    def test_answer_rejects_stale_duplicate_unknown_and_unissued_questions_without_writes(self):
        self.prepared()
        packet = self.start()
        first = self.entry(packet["questions"][0], status="unknown")
        bad_cases = [
            {"answers": [first, first]},
            {"answers": [{"question_id": "not-a-question", "status": "unknown"}]},
            {"answers": [{"question_id": self.catalog_id("contact.first_name"), "status": "answered", "value": "Injected"}]},
            {"batch_id": "stale-batch", "answers": [first]},
            {"revision": packet["revision"] + 1, "answers": [first]},
            {"revision": True, "answers": [first]},
            {"session_id": "other-session", "answers": [first]},
            {"schema_version": 999, "answers": [first]},
        ]
        for changes in bad_cases:
            with self.subTest(changes=changes):
                before = self.tree_bytes()
                entries = changes.get("answers", [first])
                self.answer(packet, entries, expected=2, **{k: v for k, v in changes.items() if k != "answers"})
                self.assertEqual(self.tree_bytes(), before)

    def test_malformed_response_and_duplicate_json_keys_are_rejected_without_mutation(self):
        self.prepared()
        self.start()
        path = self.root / "invalid-response.json"
        for content in ('{"answers":', '{"answers": [], "answers": []}'):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                before = self.tree_bytes()
                self.cli("--answer", path, "--ui", "codex-async", expected=2)
                self.assertEqual(self.tree_bytes(), before)

    def test_invalid_option_type_status_and_decline_of_mandatory_enum_have_no_partial_writes(self):
        self.prepared()
        packet = self.start()
        question = packet["questions"][0]
        for changes in ({"option_ids": ["invented-option"]}, {"value": {"arbitrary": "object"}},
                        {"status": "confirmed", "value": "quick"}, {"status": "declined"},
                        {"option_ids": [question["choices"][0]["id"], question["choices"][0]["id"]]}):
            with self.subTest(changes=changes):
                invalid = {"question_id": question["id"], "status": "answered", **changes}
                entries = [invalid]
                if len(packet["questions"]) > 1:
                    entries.insert(0, self.entry(packet["questions"][1], status="unknown"))
                before = self.tree_bytes()
                self.answer(packet, entries, expected=2)
                self.assertEqual(self.tree_bytes(), before)

    def test_payload_paths_cannot_redirect_progress_intake_or_normalized_output(self):
        self.prepared()
        packet = self.start()
        for malicious in ("../escaped.json", str(self.root.parent / "escaped.json"), "profile.json", "contact.__dict__"):
            with self.subTest(path=malicious):
                before = self.tree_bytes()
                self.answer(packet, [{"question_id": malicious, "status": "answered", "value": "injected",
                                      "path": malicious}], expected=2, output=malicious, root=malicious)
                self.assertEqual(self.tree_bytes(), before)
        before = self.tree_bytes()
        self.start()
        for invalid_id in ("../profile.json", "contact.first_name.value", "invented-record:1.school"):
            self.cli("--guided", "--edit", invalid_id, "--ui", "codex-async", expected=2)
        self.assertEqual(self.tree_bytes(), before)

    def test_replayed_answer_and_manual_progress_change_are_rejected(self):
        self.prepared()
        packet = self.start()
        entries = [self.entry(q, status="unknown") for q in packet["questions"]]
        self.answer(packet, entries)
        before = self.tree_bytes()
        self.answer(packet, entries, expected=2)
        self.assertEqual(self.tree_bytes(), before)
        current = self.start()
        progress = self.progress()
        progress["document"]["groups"]["contact"]["first_name"] = "Manual tamper"
        self.write_json("user/setup-progress.json", progress)
        before = self.tree_bytes()
        self.answer(current, [self.entry(current["questions"][0], status="unknown")], expected=2)
        self.assertEqual(self.tree_bytes(), before)

    def test_concurrent_answers_to_one_batch_have_exactly_one_winner(self):
        self.prepared()
        packet = self.start()
        # Separate immutable input files; default services are real product code.
        paths = []
        for index in range(2):
            paths.append(self.write_json(f"response-{index}.json", self.payload(
                packet, [self.entry(q, status="unknown") for q in packet["questions"]])))
        barrier = threading.Barrier(2)

        def invoke(path):
            out, err = io.StringIO(), io.StringIO()
            barrier.wait(timeout=5)
            return self.module("cli").main(
                ["--user-dir", str(self.root), "setup", "--answer", str(path), "--ui", "codex-async"],
                stdout=out, stderr=err,
            )

        with patch.object(self.module("engines").Engine, "run", side_effect=AssertionError("No engine")), \
                patch("subprocess.Popen", side_effect=AssertionError("No external process")):
            with ThreadPoolExecutor(max_workers=2) as executor:
                codes = list(executor.map(invoke, paths))
        self.assertEqual(sorted(codes), [0, 2])
        self.assertEqual(self.progress()["revision"], packet["revision"] + 1)

    def test_dependency_changes_reject_resume_answer_and_finish_before_mutation(self):
        # Each (dependency, command) gets a separate synthetic session.
        for relative in DEPENDENCIES:
            for operation in ("resume", "answer", "finish"):
                with self.subTest(relative=relative, operation=operation):
                    nested = self.root / (relative.replace("/", "-") + "-" + operation)
                    nested.mkdir()
                    original_root = self.root
                    self.root = nested
                    try:
                        self.prepared()
                        packet = self.start()
                        path = self.root / relative
                        if path.exists():
                            path.write_bytes(path.read_bytes() + b"\n ")
                        else:
                            self.write_json(relative, self.module("intake").blank_intake())
                        before = self.tree_bytes()
                        if operation == "resume":
                            self.cli("--guided", "--ui", "codex-async", expected=2)
                        elif operation == "answer":
                            self.answer(packet, [self.entry(packet["questions"][0], status="unknown")], expected=2)
                        else:
                            self.cli("--finish", "--ui", "codex-async", expected=2)
                        self.assertEqual(self.tree_bytes(), before)
                    finally:
                        self.root = original_root

    def test_every_dependency_change_after_finish_rejects_confirm_without_writes_or_backups(self):
        for relative in DEPENDENCIES:
            with self.subTest(relative=relative):
                original_root = self.root
                self.root = original_root / relative.replace("/", "-")
                self.root.mkdir()
                try:
                    self.prepared()
                    self.start()
                    self.finish()
                    path = self.root / relative
                    if path.exists():
                        path.write_bytes(path.read_bytes() + b"\n ")
                    else:
                        self.write_json(relative, self.module("intake").blank_intake())
                    before = self.tree_bytes()
                    self.cli("--confirm", expected=2)
                    self.assertEqual(self.tree_bytes(), before)
                finally:
                    self.root = original_root

    def test_confirm_checks_answer_dependency_inside_apply_import_lock_before_any_write(self):
        self.prepared()
        self.start()
        self.finish()
        intake = self.module("intake")
        original_apply = intake.apply_import
        after_race = {}

        def race(root, preview, **kwargs):
            self.assertTrue(callable(kwargs.get("precondition")))
            self.write_json("answers.json", {"profile_revision": 1, "valid": False, "answers": [], "manual": True})
            after_race.update(self.tree_bytes())
            return original_apply(root, preview, **kwargs)

        with patch.object(intake, "apply_import", side_effect=race):
            self.cli("--confirm", expected=2)
        self.assertTrue(after_race)
        self.assertEqual(self.tree_bytes(), after_race)

    def test_confirmation_requires_saved_preview_and_edits_invalidate_previous_preview(self):
        self.prepared()
        self.start()
        before = self.tree_bytes()
        self.cli("--confirm", expected=2)
        self.assertEqual(self.tree_bytes(), before)
        self.finish()
        self.set_field("contact.first_name", "Changed after preview")
        before = self.tree_bytes()
        self.cli("--confirm", expected=2)
        self.assertEqual(self.tree_bytes(), before)
        finished = self.finish()
        self.assertEqual(raw(self.document(finished)["groups"]["contact"]["first_name"]), "Changed after preview")

    def test_finish_preview_is_produced_by_existing_intake_preview_import(self):
        self.prepared()
        self.start()
        intake = self.module("intake")
        with patch.object(intake, "preview_import", wraps=intake.preview_import) as preview:
            result = self.finish()
        self.assertEqual(preview.call_count, 1)
        self.assertEqual(Path(preview.call_args.args[0]), self.root)
        self.assertEqual(Path(preview.call_args.args[1]), Path(result["intake"]))
        self.assertEqual(self.progress()["preview"], result["preview"])

    def test_native_packet_shapes_limits_and_bindings_match_the_selected_host_tool(self):
        self.prepared()
        tools = {"codex-async": "request_user_input_async", "codex": "request_user_input", "claude": "AskUserQuestion"}
        for ui, tool in tools.items():
            with self.subTest(ui=ui):
                packet = self.start(ui=ui)
                native = packet["ui"]
                self.assertEqual(native["tool"], tool)
                questions = native["arguments"]["questions"]
                bindings = native["bindings"]
                self.assertGreater(len(questions), 0)
                self.assertLessEqual(len(questions), 3)
                self.assertEqual(len(questions), len(bindings))
                canonical = {q["id"]: q for q in packet["questions"]}
                for question, binding in zip(questions, bindings):
                    self.assertIn(binding["question_id"], canonical)
                    original = canonical[binding["question_id"]]
                    self.assertEqual(len(binding["option_ids"]), len(question.get("options", [])))
                    self.assertTrue(set(binding["option_ids"]).issubset({c["id"] for c in original.get("choices", [])}))
                    if ui == "codex-async":
                        self.assertEqual(set(question), {"title", "options"})
                        self.assertTrue(all(isinstance(option, str) for option in question["options"]))
                    else:
                        self.assertLessEqual(len(question["header"]), 12)
                        self.assertTrue(2 <= len(question["options"]) <= (3 if ui == "codex" else 4))
                        self.assertTrue(all(set(option) == {"label", "description"} for option in question["options"]))
                        if ui == "codex":
                            self.assertEqual(set(question), {"id", "header", "question", "options"})
                        else:
                            self.assertEqual(set(question), {"question", "header", "options", "multiSelect"})
                    display = json.dumps(question)
                    self.assertNotIn(original["field"], display)
                    self.assertNotIn('"Other"', display, "The host already supplies free text")

    def test_many_roles_paginate_and_repeated_single_choice_can_select_multiple_roles(self):
        baseline = self.prepared()
        packet = self.edit("targets.roles", ui="codex")
        chosen = {"backend", "frontend", "security"}
        remaining = set(chosen)
        seen = set()
        for _ in range(100):
            native = packet["ui"]
            self.assertEqual(native["tool"], "request_user_input")
            self.assertLessEqual(len(native["arguments"]["questions"]), 3)
            q = next(q for q in packet["questions"] if q["field"] == "targets.roles")
            bound = next(b for b in native["bindings"] if b["question_id"] == q["id"])
            options = [c for c in q["choices"] if c["id"] in bound["option_ids"]]
            self.assertTrue(2 <= len(options) <= 3, "Navigation also consumes native option slots")
            values = [c["value"] for c in options]
            seen.update(v for v in values if isinstance(v, str))
            matches = [v for v in values if v in remaining]
            if matches:
                selected = matches[0]
                remaining.remove(selected)
            elif not remaining and "done" in values:
                self.answer(packet, [self.entry(q, "done")])
                break
            else:
                self.assertIn("more", values)
                selected = "more"
            packet = self.answer(packet, [self.entry(q, selected)])
            # --ui explicitly selects this schema; it does not change engine mode.
            packet = self.start(ui="codex")
        else:
            self.fail("Paginated choices failed to reach all roles and Done")
        self.assertFalse(remaining)
        self.assertTrue(chosen.issubset(seen))
        self.assertEqual(set(self.document(self.finish())["groups"]["targets"]["roles"]), chosen)
        self.assertEqual(self.state_bytes(), baseline)

    def test_claude_roles_use_native_multiselect_without_raw_array_text(self):
        self.prepared()
        packet = self.edit("targets.roles", ui="claude")
        native = packet["ui"]
        q = next(q for q in packet["questions"] if q["field"] == "targets.roles")
        index = next(i for i, b in enumerate(native["bindings"]) if b["question_id"] == q["id"])
        self.assertIs(native["arguments"]["questions"][index]["multiSelect"], True)
        self.assertEqual(q["kind"], "multi_choice")

    def test_text_names_and_paths_are_popup_prompts_without_upload_instructions(self):
        self.prepared()
        for field in ("contact.first_name", "contact.last_name", "resumes.main_pdf"):
            with self.subTest(field=field):
                packet = self.edit(field)
                self.assertEqual(packet["ui"]["tool"], "request_user_input_async")
                rendered = json.dumps(packet["ui"]["arguments"], ensure_ascii=False).casefold()
                self.assertNotIn("upload", rendered)
                self.assertNotIn("raw json", rendered)
                self.assertNotIn(field, rendered)


class GuidedImportPreconditionTests(ReleaseCase):
    def preview(self):
        intake = self.module("intake")
        intake.setup(self.root)
        path = self.root / "user/profile-intake.md"
        return intake, intake.preview_import(self.root, path)

    def files(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file() and not p.name.endswith(".lock")}

    def test_existing_ordinary_import_without_hook_still_previews_and_confirms(self):
        intake, preview = self.preview()
        before = self.files()
        self.assertIs(intake.apply_import(self.root, preview)["applied"], False)
        self.assertEqual(self.files(), before)
        result = intake.apply_import(self.root, preview, confirm=True)
        self.assertIs(result["applied"], True)
        self.assertEqual(result["revision"], 2)

    def test_trusted_precondition_runs_under_profile_lock_before_any_write(self):
        intake, preview = self.preview()
        storage = self.module("storage")
        actual_lock = storage.lock
        held = []
        baseline = self.files()

        @contextmanager
        def observe_lock(path, *args, **kwargs):
            with actual_lock(path, *args, **kwargs):
                held.append(Path(path))
                try:
                    yield
                finally:
                    held.pop()

        def precondition():
            self.assertIn(self.root / "profile-update.lock", held)
            self.assertEqual(self.files(), baseline)
            raise ValueError("Synthetic dependency became stale")

        with patch.object(storage, "lock", side_effect=observe_lock), \
                patch.object(storage, "atomic_write_json", wraps=storage.atomic_write_json) as write:
            with self.assertRaisesRegex(ValueError, "Synthetic dependency became stale"):
                intake.apply_import(self.root, preview, confirm=True, precondition=precondition)
        write.assert_not_called()
        self.assertEqual(self.files(), baseline)

    def test_preview_does_not_run_confirmation_precondition(self):
        intake, preview = self.preview()
        hook = Mock(side_effect=AssertionError("Preview cannot execute confirmation hook"))
        result = intake.apply_import(self.root, preview, confirm=False, precondition=hook)
        self.assertIs(result["applied"], False)
        hook.assert_not_called()


class GuidedWrapperTests(ReleaseCase):
    def test_source_and_packaged_setup_and_skill_remain_identical(self):
        for source, asset in ((".claude/commands/setup.md", "command-setup.md"),
                             (".codex/skills/autoapply-local/SKILL.md", "skill.md")):
            with self.subTest(source=source):
                self.assertEqual((RELEASE / source).read_bytes(),
                                 (RELEASE / "autoapply_local/data/bootstrap" / asset).read_bytes())

    def test_wrappers_require_real_discovery_tool_call_wait_mapping_and_explicit_guided_confirm(self):
        for relative in ("autoapply_local/data/bootstrap/command-setup.md", "autoapply_local/data/bootstrap/skill.md"):
            with self.subTest(relative=relative):
                text = (RELEASE / relative).read_text(encoding="utf-8")
                lower = text.casefold()
                for token in ("--guided", "--answer", "--finish", "--confirm", "--ui", "ui_required",
                              "request_user_input_async", "request_user_input", "AskUserQuestion"):
                    self.assertIn(token.casefold(), lower)
                for concept in ("discover", "top-level", "wait", "stable", "private", "timeout", "preselect"):
                    self.assertIn(concept, lower)
                self.assertIn("setup --confirm", text)
                self.assertRegex(lower, r"(?:no|never|do not)[^\n.]{0,100}(?:chat|prose)")
                self.assertRegex(lower, r"(?:no|never|do not)[^\n.]{0,100}(?:engine\.run|subprocess)")

    def test_plain_setup_routes_from_public_and_packaged_agent_instructions(self):
        for relative in ("AGENTS.md", "CLAUDE.md", "autoapply_local/data/bootstrap/AGENTS.md"):
            with self.subTest(relative=relative):
                text = (RELEASE / relative).read_text(encoding="utf-8").casefold()
                self.assertIn("--guided", text)
                self.assertIn("native", text)
                self.assertIn("setup", text)
                self.assertIn("ui_required", text)

    def test_guided_assets_and_tests_are_part_of_public_export_inventory(self):
        allowed = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
        for relative in ("autoapply_local/guided.py", "autoapply_local/guided_catalog.py",
                         "tests/test_guided_onboarding.py", "tests/test_guided_catalog.py"):
            self.assertIn(relative, allowed)


if __name__ == "__main__":
    unittest.main()
