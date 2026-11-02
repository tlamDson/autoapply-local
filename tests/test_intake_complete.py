"""Offline regressions for the complete intake, validated facts and runtime use.

Giả định của người thực thi (test-only): preserve the existing intake/profile
JSON projections; runtime.select_resume(profile, config, job) returns a Path.
Confirmed experience records must feed Tailor directly without hand-authored
fact_sources. Explicit confirmation replaces extraction provenance with a user
confirmation, while extracted/generated text cannot confirm itself. These
assumptions live here because this task permits changes to test files only.
"""

import json
import zipfile
from pathlib import Path
from unittest.mock import Mock

import test_tailor
from test_support import NOW, SCOPE, ReleaseCase, candidate, fact, job


class IntakeCompleteTests(ReleaseCase):
    prepared = test_tailor.TailorTests.prepared
    config = test_tailor.TailorTests.config

    def intake_document(self):
        intake = self.module("intake")
        intake.setup(self.root)
        path = self.root / "user/profile-intake.md"
        return intake, path, intake.read_intake(path)

    def import_document(self, intake, path, document):
        path.write_text("```json\n" + json.dumps(document) + "\n```", encoding="utf-8")
        preview = intake.preview_import(self.root, path)
        result = intake.apply_import(self.root, preview, confirm=True)
        self.assertIs(result["applied"], True)
        return (
            json.loads((self.root / "profile.json").read_text(encoding="utf-8")),
            json.loads((self.root / "config.json").read_text(encoding="utf-8")),
        )

    def full_document(self):
        intake, path, document = self.intake_document()
        main = self.root / "main.pdf"
        alternate = self.root / "backend.pdf"
        for pdf in (main, alternate):
            pdf.write_bytes(b"%PDF-1.4\n% synthetic source\n%%EOF")
        groups = document["groups"]
        groups["runtime"].update(
            available_engines=["codex"], apply_engine="codex", tailor_engine="codex", mode="both", language="vi"
        )
        groups["contact"].update(
            legal_name="Alex Example",
            preferred_name="Alex",
            application_email="alex@example.invalid",
            gmail_email="mail@example.invalid",
            phone_country_code="+1",
            city="Example City",
            state="EX",
            country="US",
            postcode="00000",
            street=None,
            portfolio="https://portfolio.example.invalid",
        )
        groups["resumes"].update(
            main_pdf=str(main),
            alternatives=[str(alternate)],
            role_mapping={"backend": str(alternate)},
            newest=str(main),
            master_source="synthetic-source.tex",
            withdrawn_claims=["experience:old"],
            superseded_facts=["education:obsolete"],
        )
        education_scope = {"education_id": "education:1", "effective_at": "2030-01-10"}
        groups["education"].update(
            records=[
                {
                    "id": "education:1",
                    "school": fact("Example University", scope=education_scope),
                    "degree": "BS",
                    "major": "Computer Science",
                    "status": "current",
                    "start": "2027-09",
                    "graduation": fact(
                        {"value": "2031-05", "precision": "month", "kind": "expected"}, scope=education_scope
                    ),
                },
                {
                    "id": "education:2",
                    "school": "Second Example School",
                    "degree": "Certificate",
                    "major": "Computing",
                    "status": "completed",
                    "start": "2025",
                    "graduation": {"value": "2026", "precision": "year", "kind": "actual"},
                },
            ],
            current_program="education:1",
            return_after_internship=True,
            class_standing_now="junior",
            class_standing_at_internship="senior",
            gpa="3.5",
            gpa_scale="4.0",
            major_gpa=None,
            display_gpa=True,
            transcript_available=False,
        )
        groups["targets"].update(
            job_types=["internship", "newgrad"],
            roles=["backend"],
            countries=["US"],
            exclude_roles=["QA"],
            include_stacks=["Python"],
            exclude_stacks=["UnwantedStack"],
            adjacent_roles=["platform"],
            seniority="entry",
        )
        groups["logistics"].update(
            season="summer",
            year=2030,
            window="2030-06/2030-08",
            earliest_start="2030-06-01",
            latest_end="2030-08-31",
            hours_per_week=40,
            weeks=12,
            term_time_availability=False,
            preferred_locations=["Remote, US"],
            excluded_locations=["Excluded Example City"],
            work_modes=["remote", "hybrid"],
            relocation=None,
            travel=False,
            housing=None,
            relocation_support=True,
        )
        groups["authorization"].update(
            current_status="student",
            authorized_now=None,
            authorized_at_start=None,
            sponsorship_now=None,
            sponsorship_future=None,
            restrictions=["school approval required"],
            records=[
                {
                    "id": "auth:cpt",
                    "mechanism": "CPT",
                    "mechanism_status": "planned",
                    "authorized_now": fact(None, "unknown", scope=SCOPE),
                    "authorized_at_start": fact(None, "unknown", scope=SCOPE),
                    "sponsorship_now": fact(None, "unknown", scope=SCOPE),
                    "sponsorship_future": fact(None, "unknown", scope=SCOPE),
                },
                {
                    "id": "auth:opt",
                    "mechanism": "OPT",
                    "mechanism_status": "not_applied",
                    "authorized_at_start": fact(None, "unknown", scope={**SCOPE, "job_type": "fulltime"}),
                },
            ],
        )
        groups["international_student"].update(
            status="student",
            intended_mechanism="CPT",
            cpt={"status": "planned", "valid_from": None},
            opt={"status": "not_applied"},
            stem_opt={"status": "not_applied"},
            visa_expiry="2032-01",
            program_end="2031-05",
            work_auth_expiry=None,
            school_support=None,
            dso_support=None,
            employer_support=None,
            unknown_conditions=["DSO approval"],
        )
        groups["filters"].update(
            eligibility="broad_unknown",
            no_sponsor_conflict="review",
            priority_employers=["Example Labs"],
            excluded_employers=["Excluded Example Employer"],
            include_big_tech=True,
        )
        groups["experience"].update(
            records=[
                {
                    "id": "experience:1",
                    "employer": "Example Systems",
                    "title": "Engineering Intern",
                    "start": "2029-06",
                    "end": "2029-08",
                    "location": "Remote, US",
                    "description": "Reduced Python service latency by 12%.",
                    "metrics": ["Reduced Python service latency by 12%."],
                }
            ],
            current=None,
            most_recent="experience:1",
            unlisted_projects=[{"id": "project:1", "name": "Example API"}],
            skills=[{"name": "Python", "evidence": "work", "source_id": "experience:1"}],
            metrics=[{"value": "12%", "precision": "precise", "source_id": "experience:1"}],
            certifications=["Example Certificate"],
            languages=["English"],
        )
        groups["compensation"].update(
            minimum=20,
            range="20-30",
            currency="USD",
            period="hourly",
            unpaid_consent=False,
            pronouns=fact(None, "declined"),
            eeo={"disability": "declined"},
            decline_preferences=["veteran"],
        )
        groups["history"].update(
            import_files=[], alternate_emails=["old@example.invalid"], avoid_employers=["Prior Example Employer"]
        )
        groups["tailor"].update(
            enabled=True,
            scope="all",
            employers=["Example Labs"],
            fact_sources=[],
            pages=1,
            section_order=["education", "experience", "skills"],
            summary=False,
            gpa=True,
            honors=False,
            required_entries=["experience:1"],
            allow_rewrite=True,
            allow_reselection=True,
            missing_facts="omit",
        )
        groups["resources"].update(
            gmail_opt_in=True,
            gmail_account="mail@example.invalid",
            gmail_lookback=5,
            gmail_permissions=["search", "read", "current_job_verification"],
            lanes=1,
            chrome_override=None,
        )
        return intake, path, document

    def test_full_fourteen_groups_persist_as_runtime_facts_and_configuration(self):
        intake, path, document = self.full_document()
        profile, config = self.import_document(intake, path, document)
        # Each original group has a concrete consumer-visible value, not just
        # a heading in the template or a copy in intake-import.json.
        self.assertEqual(config["runtime"]["language"], "vi")
        self.assertEqual(profile["contact"]["application_email"]["value"], "alex@example.invalid")
        self.assertEqual(config["resumes"]["role_mapping"]["backend"], str(self.root / "backend.pdf"))
        self.assertEqual(profile["education"][1]["graduation"]["value"]["precision"], "year")
        self.assertEqual(profile["targets"]["exclude_roles"], ["QA"])
        self.assertIs(profile["logistics"]["term_time_availability"], False)
        self.assertEqual(profile["authorization"][1]["mechanism"]["value"], "OPT")
        self.assertEqual(profile["international_student"]["visa_expiry"], "2032-01")
        self.assertEqual(profile["preferences"]["excluded_employers"], ["Excluded Example Employer"])
        self.assertEqual(profile["experience"][0]["description"]["value"], "Reduced Python service latency by 12%.")
        self.assertIs(profile["compensation"]["unpaid_consent"], False)
        self.assertEqual(profile["history"]["alternate_emails"], ["old@example.invalid"])
        self.assertEqual(config["tailor"]["required_entries"], ["experience:1"])
        self.assertEqual(config["resources"]["gmail_account"], "mail@example.invalid")
        self.assertNotIn("contact", config)
        self.assertNotIn("runtime", profile)

    def test_full_import_drives_fetch_filter_resume_role_mapping_and_tailor(self):
        runner = self.prepared()
        intake, path, document = self.full_document()
        profile, config = self.import_document(intake, path, document)
        transport = Mock(spec=["get_json", "post_json"])
        transport.get_json.return_value = {
            "jobs": [
                {
                    "id": "synthetic-1",
                    "title": "Backend Software Engineering Intern Summer 2030",
                    "absolute_url": job()["url"],
                    "location": {"name": "Remote, US"},
                    "content": "Build Python services.",
                }
            ]
        }
        fetch = self.module("fetch")
        fetched = fetch.Fetcher(transport=transport).fetch(
            "greenhouse", {"board": "synthetic", "company": "Example Labs"}
        )
        self.assertEqual(len(fetched), 1)
        self.assertEqual(fetch.evaluate_job(fetched[0], profile)["decision"], "keep")
        self.assertEqual(
            fetch.evaluate_job({**fetched[0], "company": "Excluded Example Employer"}, profile)["decision"],
            "exclude",
        )
        selected = self.module("runtime").select_resume(profile, config, fetched[0])
        self.assertEqual(Path(selected), self.root / "backend.pdf")
        self.assertNotIn("fact_sources", profile)
        result = runner.run(self.jd, profile, engine=config["runtime"]["tailor_engine"], config=config["tailor"])
        tex = Path(result["tex"]).read_text(encoding="utf-8")
        self.assertIn("Example Systems", tex)
        self.assertIn("Reduced Python service latency by 12", tex)
        self.assertIn("Example University", tex)
        self.assertIn("Python", tex)
        self.engines["codex"].run.assert_called_once()
        self.engines["claude"].run.assert_not_called()

    def test_confirmed_profile_experience_reaches_tailor_without_fact_sources_backdoor(self):
        runner = self.prepared()
        intake, path, document = self.full_document()
        profile, config = self.import_document(intake, path, document)
        self.assertNotIn("fact_sources", profile)
        result = runner.run(self.jd, profile, engine="codex", config=config["tailor"])
        self.assertIn("Example Systems", Path(result["tex"]).read_text(encoding="utf-8"))
        self.compiler.compile.assert_called_once()

    def test_education_fact_scope_and_date_precision_survive_import(self):
        intake, path, document = self.full_document()
        profile, _ = self.import_document(intake, path, document)
        education = profile["education"]
        self.assertEqual(education[0]["school"]["scope"]["education_id"], "education:1")
        self.assertEqual(
            education[0]["graduation"]["value"], {"value": "2031-05", "precision": "month", "kind": "expected"}
        )
        self.assertEqual(education[1]["graduation"]["value"], {"value": "2026", "precision": "year", "kind": "actual"})

    def test_multiple_auth_mechanisms_do_not_infer_authorization_or_sponsorship(self):
        intake, path, document = self.full_document()
        profile, _ = self.import_document(intake, path, document)
        self.assertEqual([item["mechanism"]["value"] for item in profile["authorization"]], ["CPT", "OPT"])
        for field in ("authorized_now", "authorized_at_start", "sponsorship_now", "sponsorship_future"):
            with self.subTest(field=field):
                value = profile["authorization"][0][field]
                self.assertEqual(value["status"], "unknown")
                self.assertIsNone(value["value"])
                self.assertEqual(value["scope"], SCOPE)
        self.assertEqual(profile["authorization"][1]["authorized_at_start"]["scope"]["job_type"], "fulltime")
        self.assertIsNone(profile["international_student"]["work_auth_expiry"])

    def test_blank_import_keeps_optional_facts_unknown_and_opt_in_disabled(self):
        intake, path, document = self.intake_document()
        profile, config = self.import_document(intake, path, document)
        self.assertEqual(profile["contact"]["legal_name"]["status"], "unknown")
        self.assertIsNone(profile["contact"]["legal_name"]["value"])
        self.assertEqual(profile["authorization"], [])
        for field in ("authorized_now", "authorized_at_start", "sponsorship_now", "sponsorship_future"):
            self.assertEqual(profile["authorization_details"][field]["status"], "unknown")
            self.assertIsNone(profile["authorization_details"][field]["value"])
        self.assertEqual(config["resources"]["lanes"], 1)
        self.assertIs(config["resources"]["gmail_opt_in"], False)

    def test_general_and_employer_specific_answers_require_exact_scope_after_update(self):
        intake, path, document = self.full_document()
        profile, _ = self.import_document(intake, path, document)
        question = "Have you previously worked for this employer?"
        answers = [
            {"question": "Preferred work mode?", **fact("remote", scope={"question": "Preferred work mode?"})},
            {"question": question, **fact(False, scope={**SCOPE, "question": question})},
        ]
        intake.update_profile(self.root, {"answers": answers}, confirm=True)
        updated = json.loads((self.root / "profile.json").read_text(encoding="utf-8"))
        resolve = self.module("profile").resolve_answer
        exact = resolve(updated, question, {**SCOPE, "question": question})
        self.assertIs(exact["value"], False)
        self.assertEqual(
            resolve(updated, question, {**SCOPE, "question": question, "employer": "Other Example"})["status"],
            "unknown",
        )
        self.assertEqual(
            resolve(updated, "Preferred work mode?", {"question": "Preferred work mode?"})["value"], "remote"
        )
        self.assertGreater(updated["revision"], profile["revision"])

    def test_profile_rejects_malformed_dates_and_date_precision(self):
        profile_api = self.module("profile")
        for graduation in (
            {"value": "2031-13", "precision": "month", "kind": "expected"},
            {"value": "2031-02-30", "precision": "day", "kind": "actual"},
            {"value": "2031-05-01", "precision": "month", "kind": "expected"},
            {"value": "2031", "precision": "month", "kind": "expected"},
            {"value": "2031-05", "precision": "month", "kind": "invented"},
        ):
            with self.subTest(graduation=graduation):
                data = candidate()
                data["education"][0]["graduation"] = fact(graduation)
                with self.assertRaises(ValueError):
                    profile_api.validate_profile(data)

    def test_profile_rejects_invalid_record_states_and_boolean_values(self):
        profile_api = self.module("profile")
        for group, key, value in (
            ("education", "status", "probably_current"),
            ("authorization", "mechanism_status", "automatically_approved"),
            ("authorization", "authorized_now", 1),
            ("authorization", "sponsorship_future", "false"),
        ):
            with self.subTest(group=group, key=key):
                data = candidate()
                data[group][0][key] = fact(value)
                with self.assertRaises(ValueError):
                    profile_api.validate_profile(data)

    def test_fact_confirmation_rejects_invalid_timestamp_and_missing_provenance_kind(self):
        for change in (
            {"confirmed_at": "not-a-timestamp"},
            {"confirmed_at": "2030-02-30T12:00:00Z"},
            {"provenance": {"source_id": "intake:1"}},
            {"status": "confirmed", "value": None},
        ):
            with self.subTest(change=change):
                data = candidate()
                data["contact"]["legal_name"].update(change)
                with self.assertRaises(ValueError):
                    self.module("profile").validate_profile(data)

    def test_derived_or_generated_provenance_cannot_mark_itself_confirmed(self):
        for kind in ("derived", "engine_inference", "document_extraction", "pdf_extraction", "generated_resume"):
            with self.subTest(kind=kind):
                data = candidate()
                data["experience"][0]["title"]["provenance"] = {"kind": kind, "source_id": "synthetic-source"}
                with self.assertRaises(ValueError):
                    self.module("profile").validate_profile(data)

    def test_import_rejects_confirmed_extraction_before_mutating_profile_or_config(self):
        intake, path, document = self.full_document()
        document["groups"]["experience"]["records"][0]["description"] = {
            **fact("Reduced Python service latency by 12%."),
            "provenance": {"kind": "document_extraction", "source_id": "extraction:0"},
        }
        before = {name: (self.root / name).read_bytes() for name in ("profile.json", "config.json")}
        path.write_text("```json\n" + json.dumps(document) + "\n```", encoding="utf-8")
        with self.assertRaises(ValueError):
            preview = intake.preview_import(self.root, path)
            intake.apply_import(self.root, preview, confirm=True)
        for name, original in before.items():
            self.assertEqual((self.root / name).read_bytes(), original)

    def test_pdf_tex_and_docx_extraction_stays_draft_until_user_confirmation(self):
        tailor = self.module("tailor")
        for suffix in (".pdf", ".tex", ".docx"):
            with self.subTest(suffix=suffix):
                source = self.root / ("source" + suffix)
                if suffix == ".docx":
                    with zipfile.ZipFile(source, "w") as archive:
                        archive.writestr(
                            "word/document.xml",
                            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                            "<w:body><w:p><w:r><w:t>Built a Python API.</w:t></w:r></w:p></w:body></w:document>",
                        )
                    draft = tailor.extract_source(source)
                elif suffix == ".pdf":
                    source.write_bytes(b"%PDF-1.4\n% synthetic extraction\n%%EOF")
                    draft = tailor.extract_source(source, extractor=Mock(return_value="Built a Python API."))
                else:
                    source.write_text("Built a Python API.", encoding="utf-8")
                    draft = tailor.extract_source(source)
                self.assertEqual(draft["status"], "draft")
                self.assertIs(draft["requires_confirmation"], True)
                self.assertTrue(draft["facts"])
                self.assertTrue(all(item["status"] != "confirmed" for item in draft["facts"]))
                # The confirmation action records that a person checked the
                # extracted text; extraction output itself is never evidence.
                intake, path, document = self.intake_document()
                confirmed = fact(draft["facts"][0]["text"], source_id="user-confirmed:source:" + suffix[1:])
                document["groups"]["experience"]["records"] = [{"id": "experience:1", "description": confirmed}]
                profile, _ = self.import_document(intake, path, document)
                stored = profile["experience"][0]["description"]
                self.assertEqual(stored["status"], "confirmed")
                self.assertEqual(stored["provenance"]["kind"], "user_confirmation")
                self.assertEqual(stored["confirmed_at"], NOW)
