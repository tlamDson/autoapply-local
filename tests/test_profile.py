"""Facts must retain scope, uncertainty, provenance and user-supplied precision.

Giả định của người thực thi: profile exposes validate_profile(data) and
resolve_answer(data, question, scope); resolution returns a fact envelope.
"""

from test_support import ReleaseCase, SCOPE, candidate, fact


class ProfileTests(ReleaseCase):
    def test_three_candidate_profiles_roundtrip_without_inventing_facts(self):
        profile = self.module("profile")
        for kind in ("student", "authorized", "unknown"):
            with self.subTest(kind=kind):
                original = candidate(kind)
                self.assertEqual(profile.validate_profile(original), original)

    def test_student_status_does_not_establish_work_authorization(self):
        answer = self.module("profile").resolve_answer(candidate(), SCOPE["question"], SCOPE)
        self.assertEqual(answer["status"], "unknown")
        self.assertIsNone(answer["value"])

    def test_blank_is_not_false(self):
        data = candidate("unknown")
        data["answers"] = [{"question": SCOPE["question"], **fact(None, "unknown", SCOPE)}]
        result = self.module("profile").resolve_answer(data, SCOPE["question"], SCOPE)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["value"])

    def test_exact_scoped_answer_preserves_provenance(self):
        data = candidate()
        expected = fact(True, scope=SCOPE)
        data["answers"] = [{"question": SCOPE["question"], **expected}]
        result = self.module("profile").resolve_answer(data, SCOPE["question"], SCOPE)
        for key, value in expected.items():
            self.assertEqual(result[key], value)

    def test_scoped_answer_cannot_leak_across_country_role_employer_time_or_question(self):
        profile = self.module("profile")
        data = candidate()
        data["answers"] = [{"question": SCOPE["question"], **fact(True, scope=SCOPE)}]
        for key, value in {
            "country": "CA",
            "job_type": "fulltime",
            "employer": "Other Example",
            "effective_at": "2031-06-01",
            "question": "Unrestricted authorization?",
        }.items():
            with self.subTest(scope_key=key):
                scope = {**SCOPE, key: value}
                result = profile.resolve_answer(data, scope["question"], scope)
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["value"])

    def test_question_specific_sensitive_answers_never_use_generic_no(self):
        profile = self.module("profile")
        data = candidate("authorized")
        for question in (
            "Will sponsorship include CPT support?",
            "Unrestricted authorization?",
            "Are you a US citizen?",
            "Export control eligibility?",
            "Security clearance?",
            "Consent to background check?",
            "Previously employed here?",
            "Why this company?",
        ):
            with self.subTest(question=question):
                result = profile.resolve_answer(data, question, {**SCOPE, "question": question})
                self.assertEqual(result["status"], "unknown")

    def test_now_start_and_internship_fulltime_sponsorship_are_separate(self):
        profile = self.module("profile")
        data = candidate()
        data["answers"] = [
            {"question": "Sponsorship now?", **fact(False, scope={**SCOPE, "question": "Sponsorship now?"})}
        ]
        for question, job_type in (
            ("Sponsorship future?", "internship"),
            ("Sponsorship now?", "fulltime"),
            ("Authorized now?", "internship"),
            ("Authorized at job start?", "internship"),
        ):
            with self.subTest(question=question, job_type=job_type):
                result = profile.resolve_answer(data, question, {**SCOPE, "question": question, "job_type": job_type})
                self.assertEqual(result["status"], "unknown")

    def test_repeated_records_and_distinct_expiry_dates_survive_validation(self):
        data = candidate()
        data["education"].append(
            {
                "id": "education:2",
                "school": fact("Second Example School"),
                "status": fact("completed"),
                "graduation": fact({"value": "2028", "precision": "year", "kind": "actual"}),
            }
        )
        data["authorization"][0].update(
            {"visa_expiry": fact("2032-01"), "program_end": fact("2031-05"), "work_auth_expiry": fact(None, "unknown")}
        )
        data["authorization"].append(
            {"id": "auth:2", "mechanism": fact("OPT"), "mechanism_status": fact("not_applied")}
        )
        self.assertEqual(self.module("profile").validate_profile(data), data)

    def test_invalid_fact_status_and_boolean_type_rejected(self):
        profile = self.module("profile")
        for invalid in (fact(True, "guessed"), fact("yes")):
            with self.subTest(invalid=invalid):
                data = candidate()
                data["authorization"][0]["authorized_now"] = invalid
                with self.assertRaises(ValueError):
                    profile.validate_profile(data)

    def test_generated_resume_is_not_a_confirmed_fact_source(self):
        data = candidate()
        data["experience"][0]["title"]["provenance"]["kind"] = "generated_resume"
        with self.assertRaises(ValueError):
            self.module("profile").validate_profile(data)

    def test_unknown_not_applicable_and_declined_remain_distinct(self):
        data = candidate()
        data["contact"].update(
            {
                "street": fact(None, "unknown"),
                "pronouns": fact(None, "declined"),
                "portfolio": fact(None, "not_applicable"),
            }
        )
        self.assertEqual(self.module("profile").validate_profile(data), data)
