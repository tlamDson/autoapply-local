"""Reopening a completed native record editor starts a fresh container."""

import copy
import unittest

import test_guided_lifecycle as lifecycle
import test_guided_recovery as recovery


class GuidedReeditTests(lifecycle.LifecycleHarness):
    education_seed = recovery.GuidedRecoveryTests.education_seed
    choose_existing = recovery.GuidedRecoveryTests.choose_existing
    finish_existing_editor = recovery.GuidedRecoveryTests.finish_existing_editor

    def completed_seeded_edit(self, confirm=False):
        original = self.education_seed(envelope=True)
        self.prepared(document=original)
        packet = self.choose_existing("education.records", index=0)
        self.finish_existing_editor(packet, "education.records", {"graduation.value": "2032-06"})
        if confirm:
            self.finish()
            self.assertTrue(self.cli("--confirm")["applied"])
        return copy.deepcopy(self.progress()["document"]["groups"]["education"]["records"])

    def assert_fresh_container(self):
        packet = self.edit("education.records", ui="codex-async")
        question = next(q for q in packet["questions"] if q["field"] == "education.records")
        controls = {choice["value"] for choice in question["choices"]}
        self.assertTrue({"add", "edit_existing", "done"}.issubset(controls), controls)
        self.assertNotIn("cancel", controls, "A fresh container has no active record to cancel")
        return packet, question

    def test_reopen_after_done_reselects_second_record_and_cancel_restores_it(self):
        records = self.completed_seeded_edit()
        baseline = self.state_bytes()
        self.assert_fresh_container()
        packet = self.choose_existing("education.records", index=1)
        self.assertEqual(packet["questions"][0]["record_id"], "edu:second")
        self.finish_existing_editor(packet, "education.records", {"graduation.value": "2028"}, cancel=True)
        self.assertEqual(self.progress()["document"]["groups"]["education"]["records"], records)
        self.assertEqual(self.state_bytes(), baseline, "Cancel changes no confirmed facts")
        self.assert_fresh_container()

    def test_reopen_after_confirm_can_edit_same_record_again_without_duplicate(self):
        records = self.completed_seeded_edit(confirm=True)
        baseline = self.state_bytes()
        self.assert_fresh_container()
        packet = self.choose_existing("education.records", index=0)
        self.assertEqual(packet["questions"][0]["record_id"], "edu:first")
        self.finish_existing_editor(packet, "education.records", {"graduation.value": "2033-05"})
        drafted = self.progress()["document"]["groups"]["education"]["records"]
        self.assertEqual([r["id"] for r in drafted], ["edu:first", "edu:second"])
        self.assertEqual(drafted[0]["graduation"], {"value": "2033-05", "precision": "month", "kind": "expected"})
        self.assertEqual(drafted[1], records[1])
        self.assertEqual(self.state_bytes(), baseline, "Re-edit remains a draft until confirmation")
        self.finish()
        self.assertTrue(self.cli("--confirm")["applied"])
        normalized = self.read("profile.json")["education"]
        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["graduation"]["value"]["value"], "2033-05")
        self.assert_fresh_container()


if __name__ == "__main__":
    unittest.main()
