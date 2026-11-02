"""Editable intake, preview/confirm import and safe, repeatable onboarding.

Giả định của người thực thi: intake uses a fenced JSON object with `groups`;
setup(root), read_intake(path), preview_import(root,path),
apply_import(root,preview,confirm=False) use an explicit user root.
"""

import copy
import json

from test_support import SCOPE, ReleaseCase, candidate, fact


GROUP_FIELDS = {
    "runtime": {"available_engines", "apply_engine", "tailor_engine", "mode", "language"},
    "contact": {
        "legal_name",
        "preferred_name",
        "application_email",
        "gmail_email",
        "phone",
        "phone_country_code",
        "city",
        "state",
        "country",
        "postcode",
        "street",
        "linkedin",
        "github",
        "portfolio",
    },
    "resumes": {
        "main_pdf",
        "alternatives",
        "role_mapping",
        "newest",
        "master_source",
        "withdrawn_claims",
        "superseded_facts",
    },
    "education": {
        "records",
        "current_program",
        "return_after_internship",
        "class_standing_now",
        "class_standing_at_internship",
        "gpa",
        "gpa_scale",
        "major_gpa",
        "display_gpa",
        "transcript_available",
    },
    "targets": {
        "job_types",
        "roles",
        "include_roles",
        "exclude_roles",
        "include_stacks",
        "exclude_stacks",
        "adjacent_roles",
        "seniority",
    },
    "logistics": {
        "season",
        "year",
        "window",
        "earliest_start",
        "latest_end",
        "hours_per_week",
        "weeks",
        "term_time_availability",
        "preferred_locations",
        "excluded_locations",
        "work_modes",
        "relocation",
        "travel",
        "housing",
        "relocation_support",
    },
    "authorization": {
        "records",
        "current_status",
        "authorized_now",
        "authorized_at_start",
        "restrictions",
        "sponsorship_now",
        "sponsorship_future",
    },
    "international_student": {
        "status",
        "intended_mechanism",
        "cpt",
        "opt",
        "stem_opt",
        "visa_expiry",
        "program_end",
        "work_auth_expiry",
        "school_support",
        "dso_support",
        "employer_support",
        "unknown_conditions",
    },
    "filters": {"eligibility", "no_sponsor_conflict", "priority_employers", "excluded_employers", "include_big_tech"},
    "experience": {
        "records",
        "current",
        "most_recent",
        "unlisted_projects",
        "skills",
        "metrics",
        "certifications",
        "languages",
    },
    "compensation": {
        "minimum",
        "range",
        "currency",
        "period",
        "unpaid_consent",
        "pronouns",
        "eeo",
        "decline_preferences",
    },
    "history": {"import_files", "alternate_emails", "avoid_employers"},
    "tailor": {
        "enabled",
        "scope",
        "employers",
        "fact_sources",
        "pages",
        "section_order",
        "summary",
        "gpa",
        "honors",
        "required_entries",
        "excluded_entries",
        "required_bullets",
        "excluded_bullets",
        "allow_rewrite",
        "allow_reselection",
        "missing_facts",
    },
    "resources": {"gmail_opt_in", "gmail_account", "gmail_lookback", "gmail_permissions", "lanes", "chrome_override"},
}


class IntakeTests(ReleaseCase):
    def prepared(self):
        intake = self.module("intake")
        intake.setup(self.root)
        return intake, self.root / "user/profile-intake.md"

    def test_setup_creates_editable_complete_template_and_user_copy(self):
        intake, path = self.prepared()
        template = self.root / "templates/profile-intake.md"
        self.assertTrue(template.is_file())
        self.assertEqual(path.read_text(encoding="utf-8"), template.read_text(encoding="utf-8"))
        document = intake.read_intake(path)
        self.assertEqual(set(document["groups"]), set(GROUP_FIELDS))
        for group, fields in GROUP_FIELDS.items():
            with self.subTest(group=group):
                self.assertTrue(fields <= set(document["groups"][group]))

    def test_setup_never_overwrites_existing_user_or_template_files(self):
        intake, path = self.prepared()
        path.write_text("User-owned edited intake", encoding="utf-8")
        template = self.root / "templates/profile-intake.md"
        template.write_text("User-owned template", encoding="utf-8")
        intake.setup(self.root)
        self.assertEqual(path.read_text(encoding="utf-8"), "User-owned edited intake")
        self.assertEqual(template.read_text(encoding="utf-8"), "User-owned template")

    def test_import_requires_explicit_confirmation_and_previews_changed_values(self):
        intake, path = self.prepared()
        data = intake.read_intake(path)
        data["groups"]["contact"]["preferred_name"] = "Alex"
        path.write_text("```json\n" + json.dumps(data) + "\n```", encoding="utf-8")
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        preview = intake.preview_import(self.root, path)
        self.assertTrue(
            any("preferred_name" in change["field"] and change["new"] == "Alex" for change in preview["changes"])
        )
        intake.apply_import(self.root, preview, confirm=False)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        result = intake.apply_import(self.root, preview, confirm=True)
        self.assertTrue(result["applied"])

    def test_malformed_types_are_rejected_before_any_import(self):
        intake, path = self.prepared()
        original = intake.read_intake(path)
        for group, field, value in (
            ("resources", "lanes", "three"),
            ("filters", "include_big_tech", "false"),
            ("targets", "roles", "backend"),
            ("education", "records", {}),
        ):
            with self.subTest(field=field):
                data = copy.deepcopy(original)
                data["groups"][group][field] = value
                path.write_text("```json\n" + json.dumps(data) + "\n```", encoding="utf-8")
                with self.assertRaises(ValueError):
                    intake.preview_import(self.root, path)

    def test_duplicate_json_keys_cannot_silently_override_facts(self):
        intake = self.module("intake")
        path = self.root / "intake.md"
        path.write_text('```json\n{"schema_version":1,"groups":{},"groups":{"contact":{}}}\n```', encoding="utf-8")
        with self.assertRaises(ValueError):
            intake.read_intake(path)

    def test_changed_intake_after_preview_requires_a_new_preview(self):
        intake, path = self.prepared()
        preview = intake.preview_import(self.root, path)
        data = intake.read_intake(path)
        data["groups"]["contact"]["preferred_name"] = "Changed after preview"
        path.write_text("```json\n" + json.dumps(data) + "\n```", encoding="utf-8")
        with self.assertRaises(ValueError):
            intake.apply_import(self.root, preview, confirm=True)

    def test_profile_update_invalidates_answers_and_reviews_for_graduation_auth_targets(self):
        intake = self.module("intake")
        for section, replacement in (("education", []), ("authorization", []), ("targets", {"roles": ["frontend"]})):
            with self.subTest(section=section):
                root = self.root / section
                root.mkdir()
                self.write_json(f"{section}/profile.json", candidate())
                self.write_json(f"{section}/answers.json", {"profile_revision": 1, "valid": True})
                self.write_json(
                    f"{section}/reviews.json", [{"job_id": "synthetic-1", "profile_revision": 1, "valid": True}]
                )
                result = intake.update_profile(root, {section: replacement}, confirm=True)
                self.assertGreater(result["revision"], 1)
                self.assertIn("answers", result["invalidated"])
                self.assertIn("reviews", result["invalidated"])
                persisted = json.loads((root / "profile.json").read_text(encoding="utf-8"))
                self.assertEqual(persisted[section], replacement)
                self.assertEqual(persisted["revision"], result["revision"])
                answers = json.loads((root / "answers.json").read_text(encoding="utf-8"))
                reviews = json.loads((root / "reviews.json").read_text(encoding="utf-8"))
                self.assertIs(answers["valid"], False)
                self.assertTrue(reviews)
                self.assertTrue(all(review["valid"] is False for review in reviews))

    def test_profile_update_without_confirm_leaves_existing_facts_unchanged(self):
        intake = self.module("intake")
        original = candidate()
        path = self.write_json("profile.json", original)
        before = path.read_bytes()
        intake.update_profile(self.root, {"targets": {"roles": ["frontend"]}}, confirm=False)
        self.assertEqual(path.read_bytes(), before)

    def test_dependent_profile_edits_invalidate_inline_answers_used_by_resolver(self):
        intake, profile_module = self.module("intake"), self.module("profile")
        question = SCOPE["question"]
        for section, replacement in (
            ("education", []),
            ("authorization", []),
            ("targets", {"roles": ["frontend"]}),
        ):
            with self.subTest(section=section):
                root = self.root / section
                original = candidate()
                original["answers"] = [{"question": question, **fact("Yes", scope=SCOPE)}]
                self.write_json(f"{section}/profile.json", original)
                before = profile_module.resolve_answer(original, question, SCOPE)
                self.assertEqual((before["status"], before["value"]), ("confirmed", "Yes"))

                # No answers.json exists: the runtime reads profile['answers'].
                intake.update_profile(root, {section: replacement}, confirm=True)
                persisted = json.loads((root / "profile.json").read_text(encoding="utf-8"))
                resolved = profile_module.resolve_answer(persisted, question, SCOPE)
                self.assertEqual(resolved["status"], "unknown")
                self.assertIsNone(resolved["value"])

    def test_confirmed_import_persists_candidate_facts_separately_from_runtime_config(self):
        intake, path = self.prepared()
        document = intake.read_intake(path)
        document["groups"]["contact"]["preferred_name"] = "Alex"
        document["groups"]["runtime"]["apply_engine"] = "codex"
        path.write_text("```json\n" + json.dumps(document) + "\n```", encoding="utf-8")
        preview = intake.preview_import(self.root, path)
        intake.apply_import(self.root, preview, confirm=True)
        profile = json.loads((self.root / "profile.json").read_text(encoding="utf-8"))
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["contact"]["preferred_name"]["value"], "Alex")
        self.assertEqual(profile["contact"]["preferred_name"]["status"], "confirmed")
        self.assertEqual(config["runtime"]["apply_engine"], "codex")
        self.assertNotIn("contact", config)
        self.assertNotIn("runtime", profile)
