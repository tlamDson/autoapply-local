"""RED contracts for the guided onboarding catalog; synthetic/offline only.

Giả định của người thực thi (test-only): guided_catalog.questions() returns
JSON-safe descriptors with stable id, backend field (group.path), kind,
value_type, bilingual prompt, mandatory, allow_unknown/allow_skip/allow_custom,
choices and nested fields for structured editors. Choice labels are bilingual;
IDs are opaque, values retain their native JSON types. Nested descriptors use
full dotted fields, e.g. education.records.graduation.precision. Editor kinds
are record/list/object, with repeatable=True for records and arbitrary lists.
These routine API choices are documented here because this turn permits only
the two new test files; the plan and production files remain untouched.
"""

import json
import unittest

from test_support import ReleaseCase


ROLE_VALUES = {
    "SWE", "backend", "frontend", "fullstack", "mobile", "platform", "SRE",
    "DevOps", "data", "AI", "ML", "research", "QA", "SDET", "security", "custom",
}
KINDS = {"choice", "multi_choice", "text", "number", "date", "path", "record", "list", "object"}
TYPES = {"string", "boolean", "integer", "number", "array", "object"}


def walk(questions):
    for question in questions:
        yield question
        yield from walk(question.get("fields", []))


class GuidedCatalogTests(ReleaseCase):
    def catalog(self):
        return self.module("guided_catalog").questions()

    def indexed(self):
        return {question["field"]: question for question in walk(self.catalog())}

    def values(self, field):
        return [choice["value"] for choice in self.indexed()[field]["choices"]]

    def test_catalog_is_stable_json_safe_and_covers_every_intake_field_in_all_14_groups(self):
        questions = self.catalog()
        self.assertEqual(questions, self.catalog())
        self.assertEqual(json.loads(json.dumps(questions, allow_nan=False)), questions)
        fields = self.module("intake").FIELDS
        self.assertEqual(len(fields), 14)
        required = {f"{group}.{name}" for group, names in fields.items() for name in names.split()}
        actual = self.indexed()
        self.assertFalse(required - actual.keys(), sorted(required - actual.keys()))
        self.assertEqual({q["field"].split(".")[0] for q in questions}, set(fields))
        ids = [q["id"] for q in walk(questions)]
        self.assertEqual(len(ids), len(set(ids)), "Nested fields also need unambiguous stable IDs")

    def test_first_questions_choose_runtime_mode_language_and_engine(self):
        first = self.catalog()[:3]
        self.assertEqual({q["field"] for q in first}, {"runtime.mode", "runtime.language", "runtime.apply_engine"})
        for question in first:
            self.assertEqual(question["kind"], "choice")
            self.assertGreaterEqual(len(question["choices"]), 2)
        self.assertTrue({"en", "vi"}.issubset(set(self.values("runtime.language"))))
        self.assertTrue({"codex", "claude"}.issubset(set(self.values("runtime.apply_engine"))))

    def test_descriptors_have_human_prompts_typed_choices_and_explicit_skip_controls(self):
        for question in walk(self.catalog()):
            with self.subTest(field=question["field"]):
                self.assertIn(question["kind"], KINDS)
                self.assertIn(question["value_type"], TYPES)
                for name in ("mandatory", "allow_unknown", "allow_skip", "allow_custom"):
                    self.assertIs(type(question[name]), bool)
                self.assertIs(question["allow_unknown"], True)
                self.assertIs(question["allow_skip"], True)
                for language in ("en", "vi"):
                    prompt = question["prompt"][language]
                    self.assertGreater(len(prompt.strip()), 8)
                    self.assertNotEqual(prompt.strip(), question["field"])
                    self.assertNotIn(question["field"], prompt, "Backend paths must stay out of the native UI")
                choices = question.get("choices", [])
                self.assertEqual(len({c["id"] for c in choices}), len(choices))
                if question["kind"] in ("choice", "multi_choice"):
                    self.assertGreaterEqual(len(choices), 2, "A heading is not a usable choice question")
                for choice in choices:
                    self.assertIsInstance(choice["id"], str)
                    for language in ("en", "vi"):
                        self.assertTrue(choice["label"][language].strip())
                    if question["value_type"] == "boolean":
                        self.assertIs(type(choice["value"]), bool)
                        self.assertNotIn("recommended", json.dumps(choice).casefold())

    def test_roles_are_multiple_real_choices_and_legacy_adjacent_roles_stays_a_list(self):
        index = self.indexed()
        self.assertEqual(index["targets.roles"]["kind"], "multi_choice")
        self.assertTrue(ROLE_VALUES.issubset(set(self.values("targets.roles"))))
        self.assertIs(index["targets.roles"]["allow_custom"], True)
        self.assertEqual(index["targets.roles"]["value_type"], "array")
        self.assertEqual(index["targets.adjacent_roles"]["value_type"], "array")
        self.assertTrue({"internship", "co-op", "newgrad", "experienced"}.issubset(set(self.values("targets.job_types"))))
        self.assertNotEqual(index["logistics.relocation"]["value_type"], "boolean")
        for field in ("targets.include_roles", "targets.exclude_roles", "targets.include_stacks",
                      "targets.exclude_stacks", "targets.countries"):
            self.assertEqual(index[field]["value_type"], "array")

    def test_closed_sponsorship_and_filters_have_real_independent_choices(self):
        for prefix in ("authorization", "authorization.records"):
            for name in ("authorized_now", "authorized_at_start", "sponsorship_now", "sponsorship_future"):
                field = f"{prefix}.{name}"
                with self.subTest(field=field):
                    question = self.indexed()[field]
                    self.assertEqual(question["kind"], "choice")
                    self.assertEqual(question["value_type"], "boolean")
                    self.assertEqual({type(v) for v in self.values(field)}, {bool})
                    self.assertEqual(set(self.values(field)), {True, False})
                    self.assertIs(question["allow_custom"], False)
        self.assertEqual(set(self.values("filters.eligibility")), {"broad_unknown", "explicit_only"})
        self.assertEqual(set(self.values("filters.no_sponsor_conflict")), {"review", "exclude"})
        self.assertEqual(set(self.values("tailor.scope")), {"all", "priority", "bigtech"})
        self.assertEqual(set(self.values("tailor.missing_facts")), {"ask", "omit"})

    def test_repeatable_records_expose_typed_nested_fields_not_raw_json(self):
        required = {
            "education.records": {
                "school", "degree", "major", "status", "start", "end",
                "graduation", "graduation.value", "graduation.precision", "graduation.kind",
            },
            "authorization.records": {
                "country", "job_type", "employer", "effective_at", "current_status",
                "authorized_now", "authorized_at_start", "sponsorship_now", "sponsorship_future",
                "restrictions", "mechanism",
            },
            "experience.records": {"employer", "title", "start", "end", "location", "description", "metrics"},
            "experience.unlisted_projects": {"name", "description", "contribution"},
            "experience.skills": {"name", "evidence", "source_id"},
        }
        index = self.indexed()
        for field, children in required.items():
            with self.subTest(field=field):
                self.assertIn(index[field]["kind"], {"record", "list"})
                self.assertIs(index[field]["repeatable"], True)
                self.assertTrue({f"{field}.{child}" for child in children}.issubset(index))
                for child in children:
                    self.assertIn(index[f"{field}.{child}"]["kind"], KINDS)
        self.assertEqual(set(self.values("education.records.status")), {"current", "completed", "leave"})
        self.assertEqual(set(self.values("education.records.graduation.precision")), {"year", "month", "day"})
        self.assertEqual(set(self.values("education.records.graduation.kind")), {"expected", "actual"})
        self.assertEqual(set(self.values("experience.skills.evidence")), {"work", "project", "course", "learning"})
        self.assertTrue({"precise", "estimated"}.issubset(set(self.values("experience.metrics.kind"))))

    def test_cpt_opt_and_stem_opt_statuses_and_support_are_independent(self):
        index = self.indexed()
        for mechanism in ("cpt", "opt", "stem_opt"):
            prefix = f"international_student.{mechanism}"
            self.assertEqual(index[prefix]["kind"], "object")
            self.assertEqual(set(self.values(prefix + ".status")), {"not_applied", "planned", "pending", "granted"})
            for field in ("valid_from", "valid_to", "school_support", "dso_support", "employer_support"):
                self.assertIn(prefix + "." + field, index)
        for field in ("visa_expiry", "program_end", "work_auth_expiry"):
            self.assertIn("international_student." + field, index)

    def test_mapping_eeo_arbitrary_lists_and_compensation_have_structured_editors(self):
        index = self.indexed()
        for field in ("resumes.role_mapping", "compensation.eeo"):
            self.assertEqual(index[field]["kind"], "object")
            self.assertTrue(index[field]["fields"], "Object data must have usable entry questions")
        self.assertIs(index["compensation.eeo"]["allow_decline"], True)
        for field in ("resumes.alternatives", "history.import_files", "history.alternate_emails",
                      "tailor.required_entries", "tailor.excluded_entries", "resources.boards"):
            self.assertIn(index[field]["kind"], {"list", "record", "object", "multi_choice"})
            self.assertTrue(index[field].get("fields") or index[field].get("choices"))
        for field in ("contact.first_name", "contact.last_name", "resumes.main_pdf", "resumes.master_source"):
            self.assertIn(index[field]["kind"], {"text", "path"})
        for field in ("logistics.hours_per_week", "logistics.weeks", "compensation.minimum", "resources.gmail_lookback"):
            self.assertEqual(index[field]["kind"], "number")
        self.assertEqual(set(self.values("resources.lanes")), {1, 2, 3})
        self.assertEqual(set(self.values("resources.gmail_opt_in")), {True, False})
        self.assertEqual(set(self.values("resources.gmail_permissions")), {"search", "read", "current_job_verification"})


if __name__ == "__main__":
    unittest.main()
