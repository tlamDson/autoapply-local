"""Offline native-packet contracts for explicit recovery and seeded record edits."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_guided_lifecycle as lifecycle
from test_support import RELEASE, fact


class GuidedRecoveryTests(lifecycle.LifecycleHarness):
    # LifecycleHarness contains helpers only, never inherited test methods.
    def restart(self, ui="codex"):
        return self.cli("--restart", "--ui", ui)

    def choose_existing(self, field, index=0, ui="codex"):
        packet = self.edit(field, ui=ui)
        container = next(q for q in packet["questions"] if q["field"] == field)
        packet = self.popup(packet, container["id"], "edit_existing", ui=ui)
        picker = next(q for q in packet["questions"] if q.get("record_picker"))
        labels = [c["label"]["en"] for c in picker["choices"]]
        self.assertEqual(len(labels), len(set(labels)), "Similar records need distinct native labels")
        choices = [c for c in picker["choices"] if not c.get("control")]
        self.assertGreater(len(choices), index)
        chosen = choices[index]
        binding = next(b for b in packet["ui"]["bindings"] if b["question_id"] == picker["id"])
        self.assertIn(chosen["id"], binding["option_ids"])
        return self.popup(packet, picker["id"], chosen["value"], ui=ui)

    def finish_existing_editor(self, packet, field, changes, *, cancel=False):
        supplied = set()
        for _ in range(60):
            # Every child is issued before its ID is used; no internal path jumps.
            packet = self.start()
            entries = []
            for question in packet["questions"]:
                if question["field"] == field:
                    self.assertEqual(supplied, set(changes))
                    return self.popup(packet, question["id"], "cancel" if cancel else "done", ui="codex-async")
                self.assertTrue(question["field"].startswith(field + "."))
                suffix = question["field"][len(field) + 1:]
                if suffix in changes:
                    supplied.add(suffix)
                    entries.append(self.entry(question, changes[suffix]))
                else:
                    entries.append(self.entry(question, status="notasked"))
            packet = self.answer(packet, entries)
        self.fail("Existing record editor did not terminate")

    def education_seed(self, envelope=False):
        document = self.module("intake").blank_intake()
        graduation = {"value": "2031-05", "precision": "month", "kind": "expected"}
        if envelope:
            graduation = fact(graduation, source_id="synthetic:graduation")
        document["groups"]["education"]["records"] = [
            {"id": "edu:first", "school": fact("Example University"), "degree": "BS", "graduation": graduation},
            {"id": "edu:second", "school": fact("Example University"), "degree": "BS",
             "graduation": fact({"value": "2027", "precision": "year", "kind": "actual"}, source_id="synthetic:prior")},
        ]
        return document

    def test_restart_cli_dispatch_and_exclusive_actions(self):
        service = Mock(return_value={"status": "questions"})
        self.cli("--restart", "--ui", "codex", services={"setup": service})
        self.assertIs(service.call_args.kwargs["restart"], True)
        for arguments in (("--restart", "--confirm"), ("--restart", "--guided"),
                          ("--restart", "--edit", "contact.first_name")):
            before = self.tree_bytes()
            self.cli(*arguments, expected=2)
            self.assertEqual(self.tree_bytes(), before)

    def test_all_five_stale_dependencies_can_explicitly_restart_without_fact_writes(self):
        for dependency in lifecycle.onboarding.DEPENDENCIES:
            with self.subTest(dependency=dependency), tempfile.TemporaryDirectory(prefix="guided-recovery-") as directory:
                self.root = Path(directory)
                self.prepared()
                confirmed = self.module("intake").blank_intake()
                confirmed["groups"]["contact"]["first_name"] = "Confirmed"
                self.confirm_document(confirmed)
                old_packet = self.edit("contact.first_name")
                self.answer(old_packet, [self.entry(old_packet["questions"][0], "Unsaved draft")])
                self.finish()
                old_progress = (self.root / "user/setup-progress.json").read_bytes()
                old_export = (self.root / "user/setup-intake.md").read_bytes()
                if dependency == "user/profile-intake.md":
                    updated = copy.deepcopy(confirmed)
                    updated["groups"]["contact"]["city"] = "Current editable city"
                    self.write_intake(updated)
                else:
                    updated = self.read(dependency)
                    if dependency == "profile.json":
                        updated["contact"]["first_name"] = fact("Current profile")
                    elif dependency == "config.json":
                        updated["resources"]["gmail_lookback"] = 31
                    elif dependency == "intake-import.json":
                        updated["recovery_marker"] = "Current confirmed intake"
                    else:
                        updated["valid"] = False
                    self.write_json(dependency, updated)
                before = self.tree_bytes()
                error = self.cli("--guided", "--ui", "codex", expected=2)
                self.assertIn("setup --restart --ui", error)
                self.answer(old_packet, [self.entry(old_packet["questions"][0], "Old reply")], expected=2)
                self.cli("--finish", "--ui", "codex", expected=2)
                self.cli("--confirm", expected=2)
                self.assertEqual(self.tree_bytes(), before)
                packet = self.restart(ui="none")
                self.assertEqual(packet["status"], "ui_required")
                self.assertEqual(self.tree_bytes(), before, "No native host means no recovery mutation")
                normalized = self.state_bytes()
                packet = self.restart()
                state = self.progress()
                self.assertNotEqual(state["session_id"], old_packet["session_id"])
                self.assertNotEqual(state["batch_id"], old_packet["batch_id"])
                self.assertIsNone(state["preview"])
                self.assertEqual(self.state_bytes(), normalized)
                backups = packet["recovery"]["backups"]
                self.assertEqual(Path(backups["progress"]).read_bytes(), old_progress)
                self.assertEqual(Path(backups["intake"]).read_bytes(), old_export)
                for name in backups.values():
                    self.assertTrue(Path(name).resolve().is_relative_to(self.root.resolve()))
                self.assertEqual(Path(packet["recovery"]["root"]), self.root.resolve())
                for name, expected in state["references"].items():
                    path = self.root / name
                    self.assertEqual(expected, hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None)
                groups = state["document"]["groups"]
                self.assertNotEqual(lifecycle.onboarding.raw(groups["contact"]["first_name"]), "Unsaved draft")
                if dependency == "user/profile-intake.md":
                    self.assertEqual(groups["contact"]["city"], "Current editable city")
                elif dependency == "profile.json":
                    self.assertEqual(lifecycle.onboarding.raw(groups["contact"]["first_name"]), "Current profile")
                elif dependency == "config.json":
                    self.assertEqual(groups["resources"]["gmail_lookback"], 31)
                elif dependency == "intake-import.json":
                    self.assertEqual(state["document"]["recovery_marker"], "Current confirmed intake")
                self.answer(old_packet, [self.entry(old_packet["questions"][0], "Replay")], expected=2)
                self.cli("--confirm", expected=2)
                self.set_field("contact.city", "Recovery answer")
                self.assertEqual(self.state_bytes(), normalized)

    def test_restart_backups_are_unique_and_preserve_malformed_untrusted_progress(self):
        self.prepared()
        self.start()
        outside = self.root.parent / (self.root.name + "-do-not-read.txt")
        outside.write_text("Outside sentinel", encoding="utf-8")
        self.addCleanup(outside.unlink, missing_ok=True)
        malformed = json.dumps({"integrity": "invalid", "preview": {"path": str(outside)}}).encode()
        (self.root / "user/setup-progress.json").write_bytes(malformed)
        before = self.state_bytes()
        first = self.restart()
        backup = Path(first["recovery"]["backups"]["progress"])
        self.assertEqual(backup.read_bytes(), malformed)
        second = self.restart()
        self.assertNotEqual(second["recovery"]["backups"]["progress"], str(backup))
        self.assertEqual(backup.read_bytes(), malformed)
        self.assertEqual(outside.read_text(encoding="utf-8"), "Outside sentinel")
        self.assertEqual(self.state_bytes(), before)

    def test_restart_rejects_symlink_backup_surface_without_touching_data(self):
        self.prepared()
        self.start()
        before = self.tree_bytes()
        target = self.root / "user/setup-backups"
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == target or original(path)):
            error = self.cli("--restart", "--ui", "codex", expected=2)
        self.assertIn("symlink", error.lower())
        self.assertEqual(self.tree_bytes(), before)

    def test_restart_recovers_missing_progress_with_current_seed(self):
        self.prepared()
        (self.root / "user/setup-progress.json").unlink(missing_ok=True)
        before = self.state_bytes()
        packet = self.restart()
        self.assertEqual(packet["status"], "questions")
        self.assertEqual(packet["recovery"]["backups"], {})
        self.assertEqual(self.state_bytes(), before)

    def edit_seeded_graduation(self, envelope):
        original = self.education_seed(envelope)
        self.prepared(document=original)
        baseline = self.state_bytes()
        packet = self.choose_existing("education.records")
        self.assertEqual(packet["questions"][0]["record_id"], "edu:first")
        self.finish_existing_editor(packet, "education.records", {"graduation.value": "2032-06"})
        drafted = self.progress()["document"]["groups"]["education"]["records"]
        self.assertEqual(len(drafted), 2, "Editing must never Add a duplicate program")
        self.assertEqual(drafted[0]["id"], "edu:first")
        self.assertEqual(drafted[0]["school"], original["groups"]["education"]["records"][0]["school"])
        self.assertEqual(drafted[0]["graduation"], {"value": "2032-06", "precision": "month", "kind": "expected"})
        self.assertEqual(drafted[1], original["groups"]["education"]["records"][1])
        finished = self.finish()
        self.assertEqual(self.state_bytes(), baseline, "Preview does not confirm changed facts")
        self.assertEqual(self.document(finished)["groups"]["education"]["records"], drafted)
        self.cli("--confirm")
        records = self.read("profile.json")["education"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["graduation"]["value"], drafted[0]["graduation"])
        self.assertEqual(records[0]["graduation"]["status"], "confirmed")
        self.assertEqual(records[0]["school"], original["groups"]["education"]["records"][0]["school"])
        self.assertEqual(records[1]["graduation"], original["groups"]["education"]["records"][1]["graduation"])

    def test_native_existing_raw_graduation_edit_preserves_other_record(self):
        self.edit_seeded_graduation(envelope=False)

    def test_native_existing_enveloped_graduation_edit_preserves_metadata_and_precision(self):
        self.edit_seeded_graduation(envelope=True)

    def test_existing_experience_cancel_restores_record_and_other_queue_questions(self):
        document = self.module("intake").blank_intake()
        records = [{"id": "exp:first", "employer": fact("Example Labs"), "title": fact("Engineer"), "description": fact("Confirmed work")},
                   {"id": "exp:second", "employer": fact("Example Labs"), "title": fact("Engineer"), "description": fact("Other work")}]
        document["groups"]["experience"]["records"] = copy.deepcopy(records)
        self.prepared(document=document)
        packet = self.choose_existing("experience.records", index=1)
        self.assertEqual(packet["questions"][0]["record_id"], "exp:second")
        self.finish_existing_editor(packet, "experience.records", {"description": "Discard this draft"}, cancel=True)
        self.assertEqual(self.progress()["document"]["groups"]["experience"]["records"], records)
        self.assertEqual(self.state_bytes()["intake-import.json"], (self.root / "intake-import.json").read_bytes())

    def test_existing_authorization_editor_preserves_exact_typed_answers(self):
        document = self.module("intake").blank_intake()
        document["groups"]["authorization"]["records"] = [
            {"id": "auth:first", "country": "US", "authorized_now": fact(False), "sponsorship_future": fact(True)},
            {"id": "auth:second", "country": "CA", "authorized_now": fact(True)},
        ]
        self.prepared(document=document)
        packet = self.choose_existing("authorization.records")
        self.finish_existing_editor(packet, "authorization.records", {"authorized_now": True})
        self.finish()
        self.cli("--confirm")
        records = self.read("profile.json")["authorization"]
        self.assertIs(records[0]["authorized_now"]["value"], True)
        self.assertEqual(records[0]["sponsorship_future"], document["groups"]["authorization"]["records"][0]["sponsorship_future"])
        self.assertEqual(records[1]["authorized_now"], document["groups"]["authorization"]["records"][1]["authorized_now"])

    def test_picker_rejects_forged_bindings_and_cancel_keeps_all_records(self):
        original = self.education_seed(envelope=True)
        self.prepared(document=original)
        packet = self.edit("education.records", ui="codex")
        packet = self.popup(packet, packet["questions"][0]["id"], "edit_existing")
        picker = next(q for q in packet["questions"] if q.get("record_picker"))
        before = self.tree_bytes()
        self.answer(packet, [{"question_id": picker["id"], "status": "answered", "option_ids": ["unissued:edu:second"]}], expected=2)
        self.answer(packet, [{"question_id": picker["id"], "status": "answered", "value": "edu:second", "record_id": "edu:second"}], expected=2)
        self.assertEqual(self.tree_bytes(), before)
        self.popup(packet, picker["id"], "cancel")
        self.assertEqual(self.progress()["document"], original)

    def test_existing_record_editor_keeps_natural_pending_queue_and_unissued_guard(self):
        original = self.education_seed()
        self.prepared(document=original)
        packet = self.start()
        for _ in range(60):
            if any(q["field"] == "education.records" for q in packet["questions"]):
                break
            packet = self.answer(packet, [self.entry(q, status="notasked") for q in packet["questions"]])
        else:
            self.fail("Natural walk never reached education")
        q = next(q for q in packet["questions"] if q["field"] == "education.records")
        pending = [x["id"] for x in packet["questions"] if x["id"] != q["id"]]
        packet = self.answer(packet, [self.entry(q, "edit_existing")])
        picker = next(q for q in packet["questions"] if q.get("record_picker"))
        selected = next(c for c in picker["choices"] if not c.get("control"))
        self.answer(packet, [self.entry(picker, selected["value"])])
        queue = self.progress()["queue"]
        self.assertTrue(set(pending).issubset({q["id"] for q in queue}))
        unissued = next(q for q in queue if q["field"] == "education.records.graduation.kind")
        self.cli("--guided", "--edit", unissued["id"], "--ui", "codex", expected=2)
        self.assertEqual(self.progress()["document"], original)

    def test_native_instructions_require_reload_choice_and_explain_existing_editor(self):
        pairs = ((".claude/commands/setup.md", "autoapply_local/data/bootstrap/command-setup.md"),
                 (".codex/skills/autoapply-local/SKILL.md", "autoapply_local/data/bootstrap/skill.md"))
        for source, packaged in pairs:
            text = (RELEASE / source).read_text(encoding="utf-8")
            self.assertEqual(text, (RELEASE / packaged).read_text(encoding="utf-8"))
            for phrase in ("setup --restart --ui UI", "Reload latest confirmed profile", "Keep current draft", "only after", "timeout", "existing record", "backup"):
                self.assertIn(phrase, text)
        for name in ("AGENTS.md", "CLAUDE.md", "README.md", "README.vi.md", "autoapply_local/data/bootstrap/AGENTS.md", "autoapply_local/data/bootstrap/README.md"):
            self.assertIn("--restart", (RELEASE / name).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
