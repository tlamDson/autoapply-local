"""Source ownership regressions for complete-plan item 3.

Giả định của người thực thi: source.id is the entry identity unless entry_id
names its parent. primary_source_id selects one entry; supporting source_ids
may only refer to that entry. Existing validate_claims returns hydrated claims.
Configuration keys retain the existing Tailor API; honors=True includes confirmed
education honors. All compilation, rendering and engines here are fakes.
"""

import copy
from pathlib import Path

import test_tailor
from test_support import ReleaseCase, fact


def unrelated_sources():
    return [
        {
            "id": "experience:1",
            "type": "experience",
            "status": "confirmed",
            "employer": "Example Systems",
            "title": "Engineering Intern",
            "start": "2028-06",
            "end": "2028-08",
            "metric": "12%",
            "text": "Reduced Python service latency by approximately 12%.",
        },
        {
            "id": "experience:2",
            "type": "experience",
            "status": "confirmed",
            "employer": "Other Example Systems",
            "title": "Platform Intern",
            "start": "2029-06",
            "end": "2029-08",
            "metric": "30%",
            "text": "Reduced Rust build time by 30%.",
        },
    ]


class TailorCompleteTests(ReleaseCase):
    prepared = test_tailor.TailorTests.prepared
    config = test_tailor.TailorTests.config

    def validate(self, claim, evidence=None):
        return self.module("tailor").validate_claims(evidence or unrelated_sources(), [claim])

    def test_metric_moved_to_another_entry_is_rejected(self):
        a, b = unrelated_sources()
        claim = {**b, "primary_source_id": b["id"], "source_ids": [b["id"]], "metric": a["metric"]}
        with self.assertRaises(ValueError):
            self.validate(claim)

    def test_citing_both_entries_cannot_launder_transferred_metric(self):
        a, b = unrelated_sources()
        claim = {
            **b,
            "primary_source_id": b["id"],
            "source_ids": [a["id"], b["id"]],
            "metric": a["metric"],
            "text": "Reduced Rust build time by approximately 12%.",
        }
        with self.assertRaises(ValueError):
            self.validate(claim)

    def test_citing_both_entries_cannot_launder_transferred_title_or_dates(self):
        a, b = unrelated_sources()
        for key in ("employer", "title", "start", "end"):
            with self.subTest(field=key):
                claim = {
                    **b,
                    "primary_source_id": b["id"],
                    "source_ids": [a["id"], b["id"]],
                    key: a[key],
                }
                with self.assertRaises(ValueError):
                    self.validate(claim)

    def test_correct_metadata_does_not_excuse_wrong_free_text_attribution(self):
        a, b = unrelated_sources()
        claim = {**b, "primary_source_id": b["id"], "source_ids": [a["id"], b["id"]], "text": a["text"]}
        with self.assertRaises(ValueError):
            self.validate(claim)

    def test_omitting_estimate_qualifier_cannot_turn_estimate_into_exact_metric(self):
        source = unrelated_sources()[0]
        claim = {
            **source,
            "source_ids": [source["id"]],
            "text": "Reduced Python service latency by 12%.",
        }
        with self.assertRaises(ValueError):
            self.validate(claim)

    def test_word_bag_match_cannot_change_used_to_led_ownership(self):
        source = unrelated_sources()[0]
        source.update(metric=None, text="Used Python service tooling and led an HTTP documentation review.")
        claim = {
            **source,
            "source_ids": [source["id"]],
            "text": "Led Python service tooling and used an HTTP documentation review.",
        }
        with self.assertRaises(ValueError):
            self.validate(claim, [source])

    def test_omitted_immutable_fields_are_restored_to_rendered_claim(self):
        source = unrelated_sources()[0]
        result = self.validate(
            {
                "type": "experience",
                "primary_source_id": source["id"],
                "source_ids": [source["id"]],
                "text": source["text"],
            }
        )
        for key in ("employer", "title", "start", "end", "metric"):
            with self.subTest(field=key):
                self.assertEqual(result[0].get(key), source[key])

    def test_same_entry_supporting_claim_is_allowed_and_inputs_preserved(self):
        source = unrelated_sources()[0]
        support = {
            "id": "experience:1:metric:1",
            "entry_id": source["id"],
            "type": "experience",
            "status": "confirmed",
            "text": source["text"],
        }
        claim = {
            **source,
            "primary_source_id": source["id"],
            "source_ids": [source["id"], support["id"]],
        }
        evidence = [source, support]
        before = copy.deepcopy((evidence, claim))
        result = self.validate(claim, evidence)
        self.assertEqual(result[0]["text"], source["text"])
        self.assertEqual(result[0]["employer"], source["employer"])
        self.assertEqual((evidence, claim), before)

    def test_required_entry_cannot_be_selected_then_hidden_by_section_order(self):
        runner = self.prepared()
        config = {
            **self.config(),
            "required_entries": ["experience:1"],
            "section_order": ["education", "skills"],
        }
        with self.assertRaises(ValueError):
            runner.run(self.jd, self.profile, engine="codex", config=config)
        self.compiler.compile.assert_not_called()

    def test_confirmed_contact_education_and_skills_survive_claim_validation(self):
        runner = self.prepared()
        result = runner.run(self.jd, self.profile, engine="codex", config=self.config())
        tex = Path(result["tex"]).read_text(encoding="utf-8")
        for value in ("Alex Example", "alex@example.invalid", "Example University", "Computer Science", "Python"):
            with self.subTest(value=value):
                self.assertIn(value, tex)

    def test_configured_gpa_and_honors_reach_final_document(self):
        runner = self.prepared()
        self.profile["education"][0].update(gpa=fact("3.70/4.00"), honors=fact("Example Merit Award"))
        result = runner.run(
            self.jd,
            self.profile,
            engine="codex",
            config={**self.config(), "gpa": True, "honors": True},
        )
        tex = Path(result["tex"]).read_text(encoding="utf-8")
        self.assertIn("3.70/4.00", tex)
        self.assertIn("Example Merit Award", tex)

    def test_disabling_gpa_and_honors_hides_those_optional_fields(self):
        runner = self.prepared()
        self.profile["education"][0].update(gpa=fact("3.70/4.00"), honors=fact("Example Merit Award"))
        result = runner.run(
            self.jd,
            self.profile,
            engine="codex",
            config={**self.config(), "gpa": False, "honors": False},
        )
        tex = Path(result["tex"]).read_text(encoding="utf-8")
        self.assertNotIn("3.70/4.00", tex)
        self.assertNotIn("Example Merit Award", tex)
