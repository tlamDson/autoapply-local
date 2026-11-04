"""Offline contracts for the four autoapply-local-review-regressions findings.

Giả định của người thực thi: dùng API hiện có, id câu trả lời tổng hợp và
timestamp ISO có timezone; chỉ sửa test nên ghi giả định tại đây thay vì plan.
Không ghi receipt RED ngoài phạm vi; output lệnh chạy là bằng chứng RED.
"""

import copy
import hashlib
import json
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from test_support import SCOPE, ReleaseCase, candidate, fact, job, snapshot


EARLY = "2001-01-01T00:00:00Z"


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AnswerReconfirmationTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.intake = self.module("intake")
        self.resolver = self.module("profile")
        self.prepare()

    def prepare(self, name="user"):
        self.user = self.root / name
        self.intake.setup(self.user)
        self.path = self.user / "user/profile-intake.md"
        self.document = self.intake.read_intake(self.path)
        self.document["groups"]["education"]["records"] = [
            {
                "id": "education:1",
                "school": "Example University",
                "graduation": {"value": "2031-05", "precision": "month", "kind": "expected"},
            }
        ]
        self.document["groups"]["authorization"]["current_status"] = "student"
        self.document["groups"]["international_student"]["cpt"] = {"status": "planned"}
        self.document["answers"] = [
            {
                "id": "answer:authorization",
                "question": SCOPE["question"],
                **fact(True, scope=SCOPE, source_id="user:authorization"),
                "confirmed_at": EARLY,
            },
            {
                "id": "answer:sponsorship",
                "question": "Require sponsorship?",
                **fact(False, scope={**SCOPE, "question": "Require sponsorship?"}, source_id="user:sponsorship"),
                "confirmed_at": EARLY,
            },
        ]
        self.original_answers = copy.deepcopy(self.document["answers"])
        self.import_document(self.document)
        self.seed_review()

    def read(self, name):
        return json.loads((self.user / name).read_text(encoding="utf-8"))

    def seed_review(self):
        self.write_json(
            str(self.user.relative_to(self.root) / "reviews.json"),
            [{"job_id": "synthetic-1", "profile_revision": self.read("profile.json")["revision"], "valid": True}],
        )

    def preview(self, document):
        self.path.write_text("```json\n" + json.dumps(document, ensure_ascii=False) + "\n```\n", encoding="utf-8")
        return self.intake.preview_import(self.user, self.path)

    def import_document(self, document):
        return self.intake.apply_import(self.user, self.preview(document), confirm=True)

    def graduation_change(self):
        changed = copy.deepcopy(self.document)
        changed["groups"]["education"]["records"][0]["graduation"]["value"] = "2032-05"
        return changed

    def assert_answer_states(self, expected):
        profile, ledger = self.read("profile.json"), self.read("answers.json")
        self.resolver.validate_profile(profile)
        for label, data in (
            ("profile", profile),
            ("answers", {**profile, "answers": ledger["answers"]}),
        ):
            self.assertEqual({answer["id"] for answer in data["answers"]}, set(expected))
            for answer in data["answers"]:
                valid = expected[answer["id"]]
                with self.subTest(path=label, answer=answer["id"]):
                    self.assertIs(answer.get("valid", True), valid)
                    direct = self.resolver.resolve_answer(data, answer["question"], answer["scope"])
                    field = self.resolver.resolve_field(
                        data,
                        {"id": answer["id"], "label": answer["question"]},
                        job(start_date=SCOPE["effective_at"]),
                    )
                    for resolved in (direct, field):
                        self.assertEqual(resolved["status"], "confirmed" if valid else "unknown")
                        if valid:
                            self.assertEqual(resolved["value"], answer["value"])
                        else:
                            self.assertIsNone(resolved["value"])
        # The plan permits either a document validity flag or per-answer
        # resolution. Both resolver paths above must respect each answer.

    def all_states(self, valid):
        return {answer["id"]: valid for answer in self.original_answers}

    def assert_reviews_invalid(self):
        reviews = self.read("reviews.json")
        self.assertTrue(reviews)
        self.assertTrue(all(review["valid"] is False for review in reviews))

    def assert_reconfirmation(self, result, answers):
        self.assertIn("needs_reconfirmation", result)
        self.assertEqual(
            {(item["id"], item["question"]) for item in result["needs_reconfirmation"]},
            {(item["id"], item["question"]) for item in answers},
        )

    def test_graduation_import_invalidates_each_persisted_answer_and_both_resolver_paths(self):
        result = self.import_document(self.graduation_change())
        self.assert_answer_states(self.all_states(False))
        for original, saved in zip(self.original_answers, self.read("profile.json")["answers"]):
            for key, value in original.items():
                self.assertEqual(saved[key], value, "Invalidation must retain the answer and its confirmation history")
            self.assertEqual(saved.get("invalidated_revision"), result["revision"])
            self.assertIn("invalidated_at", saved)
            self.assertGreater(timestamp(saved["invalidated_at"]), timestamp(EARLY))
        self.assert_reviews_invalid()

    def test_old_form_cannot_resurrect_answers_even_when_facts_revert(self):
        self.import_document(self.graduation_change())
        self.import_document(self.document)
        self.import_document(self.document)
        self.assert_answer_states(self.all_states(False))
        for original, saved in zip(self.original_answers, self.read("profile.json")["answers"]):
            self.assertEqual(saved["confirmed_at"], original["confirmed_at"])
            self.assertEqual(saved["provenance"], original["provenance"])

    def test_explicit_fresh_confirmation_revalidates_only_the_selected_answer(self):
        changed = self.graduation_change()
        self.import_document(changed)
        saved = self.read("profile.json")["answers"][0]
        self.assertIn("invalidated_at", saved)
        fresh = max(timestamp(saved["invalidated_at"]), timestamp(saved["confirmed_at"])) + timedelta(seconds=1)
        changed["answers"][0]["confirmed_at"] = fresh.isoformat()
        result = self.import_document(changed)
        self.assert_answer_states({"answer:authorization": True, "answer:sponsorship": False})
        self.assert_reconfirmation(result, [self.original_answers[1]])

    def test_stale_or_equal_confirmation_never_revalidates(self):
        for index, comparison in enumerate(("old", "equal", "before_invalidation")):
            with self.subTest(timestamp=comparison):
                self.prepare("timestamp-" + str(index))
                changed = self.graduation_change()
                self.import_document(changed)
                saved = self.read("profile.json")["answers"][0]
                self.assertIn("invalidated_at", saved)
                invalidated = timestamp(saved["invalidated_at"])
                changed["answers"][0]["confirmed_at"] = (
                    EARLY
                    if comparison == "old"
                    else invalidated.isoformat()
                    if comparison == "equal"
                    else (invalidated - timedelta(seconds=1)).isoformat()
                )
                self.import_document(changed)
                self.assert_answer_states(self.all_states(False))

    def test_reconfirmation_must_also_be_newer_than_the_stored_confirmation(self):
        # A supplied confirmation may be in the future: comparing only against
        # invalidated_at must not let an older confirmation supersede it.
        self.document["answers"][0]["confirmed_at"] = "2099-01-01T00:00:00Z"
        self.import_document(self.document)
        changed = self.graduation_change()
        self.import_document(changed)
        saved = self.read("profile.json")["answers"][0]
        self.assertIn("invalidated_at", saved)
        changed["answers"][0]["confirmed_at"] = (timestamp(saved["invalidated_at"]) + timedelta(seconds=1)).isoformat()
        self.import_document(changed)
        self.assert_answer_states(self.all_states(False))

    def test_authorization_details_alone_invalidates_without_changing_records(self):
        before = self.read("profile.json")["authorization"]
        changed = copy.deepcopy(self.document)
        changed["groups"]["authorization"]["current_status"] = "CPT pending school approval"
        self.import_document(changed)
        self.assertEqual(self.read("profile.json")["authorization"], before)
        self.assert_answer_states(self.all_states(False))

    def test_each_dependent_fact_group_invalidates_answers(self):
        edits = (
            ("education", "current_program", "education:1"),
            ("authorization", "records", [{"id": "auth:1", "mechanism": "CPT"}]),
            ("international_student", "cpt", {"status": "pending"}),
            ("targets", "roles", ["backend"]),
            ("logistics", "hours_per_week", 20),
        )
        for index, (group, field, value) in enumerate(edits):
            with self.subTest(group=group, field=field):
                self.prepare("dependency-" + str(index))
                changed = copy.deepcopy(self.document)
                changed["groups"][group][field] = value
                self.import_document(changed)
                self.assert_answer_states(self.all_states(False))

    def test_filters_keep_factual_answers_but_invalidate_reviews(self):
        before = self.read("profile.json")["answers"]
        changed = copy.deepcopy(self.document)
        changed["groups"]["filters"]["include_big_tech"] = True
        self.import_document(changed)
        self.assertEqual(self.read("profile.json")["answers"], before)
        self.assert_answer_states(self.all_states(True))
        self.assert_reviews_invalid()

    def test_identical_raw_intake_does_not_invalidate_due_to_generated_timestamps(self):
        before = self.read("profile.json")
        self.import_document(self.document)
        self.assert_answer_states(self.all_states(True))
        self.assertEqual(self.read("profile.json")["answers"], before["answers"])
        self.assert_reviews_invalid()

    def test_fact_confirmation_timestamp_alone_does_not_invalidate(self):
        envelope = fact("Example University")
        envelope["confirmed_at"] = EARLY
        self.document["groups"]["education"]["records"][0]["school"] = envelope
        self.import_document(self.document)
        # Confirm answers after this genuine provenance change, then change
        # only a fact's confirmation timestamp on the next import.
        for answer in self.document["answers"]:
            answer["confirmed_at"] = "2099-01-01T00:00:00Z"
        self.import_document(self.document)
        self.document["groups"]["education"]["records"][0]["school"]["confirmed_at"] = "2002-01-01T00:00:00Z"
        self.import_document(self.document)
        self.assert_answer_states(self.all_states(True))

    def test_update_profile_education_invalidates_inline_and_external_answers(self):
        education = self.read("profile.json")["education"]
        education[0]["graduation"]["value"]["value"] = "2032-05"
        self.intake.update_profile(self.user, {"education": education}, confirm=True)
        self.assert_answer_states(self.all_states(False))
        self.assert_reviews_invalid()

    def test_update_profile_preferences_preserves_answer_validity(self):
        self.intake.update_profile(self.user, {"preferences": {"include_big_tech": True}}, confirm=True)
        self.assert_answer_states(self.all_states(True))
        self.assert_reviews_invalid()

    def test_first_import_accepts_explicit_scoped_answers(self):
        self.assert_answer_states(self.all_states(True))

    def test_preview_and_apply_report_exact_questions_needing_reconfirmation(self):
        before = {name: (self.user / name).read_bytes() for name in ("profile.json", "answers.json", "reviews.json")}
        preview = self.preview(self.graduation_change())
        self.assert_reconfirmation(preview, self.original_answers)
        self.assertEqual({name: (self.user / name).read_bytes() for name in before}, before)
        result = self.intake.apply_import(self.user, preview, confirm=True)
        self.assert_reconfirmation(result, self.original_answers)

    def test_unchanged_answers_are_not_listed_for_reconfirmation(self):
        preview = self.preview(self.document)
        self.assert_reconfirmation(preview, [])
        self.assert_reconfirmation(self.intake.apply_import(self.user, preview, confirm=True), [])

    def test_new_and_changed_unconfirmed_answers_remain_unusable(self):
        document = copy.deepcopy(self.document)
        document["answers"][0].update(value=False, status="unknown", confirmed_at=None)
        new = {
            "id": "answer:consent",
            "question": "Consent to processing?",
            **fact(None, "unknown", scope={**SCOPE, "question": "Consent to processing?"}),
        }
        document["answers"].append(new)
        result = self.import_document(document)
        self.assert_answer_states({"answer:authorization": False, "answer:sponsorship": True, "answer:consent": False})
        self.assert_reconfirmation(result, [document["answers"][0], new])

    def test_changed_value_with_old_confirmation_is_not_explicit_reconfirmation(self):
        document = copy.deepcopy(self.document)
        document["answers"][0]["value"] = False
        self.import_document(document)
        self.assert_answer_states({"answer:authorization": False, "answer:sponsorship": True})

    def test_fresh_timestamp_with_changed_provenance_cannot_reconfirm_invalid_answer(self):
        document = self.graduation_change()
        self.import_document(document)
        saved = self.read("profile.json")["answers"][0]
        self.assertIn("invalidated_at", saved)
        document["answers"][0]["confirmed_at"] = (timestamp(saved["invalidated_at"]) + timedelta(seconds=1)).isoformat()
        document["answers"][0]["provenance"]["source_id"] = "different:confirmation"
        self.import_document(document)
        self.assert_answer_states(self.all_states(False))


class ClaimQualifierTests(ReleaseCase):
    def validate(self, evidence, text, supporting=None):
        source = {"id": "experience:1", "type": "experience", "status": "confirmed", "text": evidence}
        sources = [source, *(supporting or [])]
        claim = {"type": "experience", "source_ids": [item["id"] for item in sources], "text": text}
        return self.module("tailor").validate_claims(sources, [claim])

    def test_dropping_contribution_words_cannot_promote_ownership(self):
        for qualifier in ("assisted", "helped", "supported", "contributed", "participated", "collaborated"):
            with self.subTest(qualifier=qualifier), self.assertRaises(ValueError):
                self.validate(qualifier + " in building the API", "Built the API")

    def test_dropping_negation_is_rejected_without_numbers(self):
        for evidence, claim in (
            ("did not lead the API", "lead the API"),
            ("No API ownership", "API ownership"),
            ("Never led the API", "Led the API"),
            ("Built the API without ownership", "Built the API"),
            ("không dẫn dắt", "dẫn dắt"),
            ("chưa dẫn dắt", "dẫn dắt"),
            ("chẳng dẫn dắt", "dẫn dắt"),
        ):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                self.validate(evidence, claim)

    def test_dropping_limitations_is_rejected_without_numbers(self):
        for qualifier in ("partial", "partially", "limited", "only", "prototype", "experimental"):
            with self.subTest(qualifier=qualifier), self.assertRaises(ValueError):
                self.validate("Built the " + qualifier + " API", "Built the API")

    def test_protected_words_from_supporting_evidence_also_must_survive(self):
        support = {
            "id": "experience:1:scope",
            "entry_id": "experience:1",
            "type": "experience",
            "status": "confirmed",
            "text": "Assisted with the prototype API",
        }
        # Both sources describe the same entry; rejecting unrelated entries
        # would not exercise preservation across all cited evidence.
        with self.assertRaises(ValueError):
            self.validate("Built the API", "Built the API", supporting=[support])

    def test_protected_word_in_claim_requires_evidence(self):
        for qualifier in ("Assisted", "not", "prototype", "không"):
            with self.subTest(qualifier=qualifier), self.assertRaises(ValueError):
                self.validate("Built the API", qualifier + " built the API")

    def test_rewrites_preserving_qualifiers_and_word_forms_are_accepted(self):
        pairs = [
            ("Assisted in building the API", "Assisted building the API"),
            ("did not lead the API", "not lead the API"),
            ("Built the prototype API", "Built prototype API"),
            ("Partially built the API", "Partially built API"),
            ("Không dẫn dắt API", "KHÔNG dẫn dắt API"),
            (unicodedata.normalize("NFD", "chưa dẫn dắt API"), "chưa dẫn dắt API"),
        ]
        for form in (
            "assist",
            "assisting",
            "helps",
            "helping",
            "supporting",
            "contributes",
            "participating",
            "collaborating",
        ):
            pairs.append((form + " in building the API", form + " building the API"))
        for evidence, claim in pairs:
            with self.subTest(evidence=evidence):
                self.assertEqual(self.validate(evidence, claim)[0]["text"], claim)

    def test_dropping_contribution_word_variants_is_rejected(self):
        for form in (
            "assist",
            "assisting",
            "helps",
            "helping",
            "supporting",
            "contributes",
            "participating",
            "collaborating",
        ):
            with self.subTest(form=form), self.assertRaises(ValueError):
                self.validate(form + " in building the API", "Built the API")

    def test_unicode_normalization_cannot_hide_dropped_vietnamese_negation(self):
        for word in ("không", "chưa", "chẳng"):
            with self.subTest(word=word), self.assertRaises(ValueError):
                self.validate(unicodedata.normalize("NFD", word.upper() + " dẫn dắt API"), "dẫn dắt API")

    def test_existing_technical_rewrites_metrics_and_ownership_guards_survive(self):
        for evidence, claim in (
            ("Developed Python services using HTTP APIs", "Built Python service with HTTP API"),
            ("Reduced Python service latency by approximately 12%", "Reduced Python latency by approximately 12%"),
        ):
            with self.subTest(claim=claim):
                self.assertEqual(self.validate(evidence, claim)[0]["text"], claim)
        for evidence, claim in (
            ("Reduced latency by approximately 12%", "Reduced latency by 12%"),
            ("Reduced latency by 12%", "Reduced latency by 90%"),
            ("Used Python tooling and led HTTP review", "Led Python tooling and used HTTP review"),
        ):
            with self.subTest(claim=claim), self.assertRaises(ValueError):
                self.validate(evidence, claim)


class AnalysisFailureTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.write_json("profile.json", candidate("authorized"))
        self.pdf = self.root / "resume.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n% synthetic resume\n%%EOF")
        self.browser = Mock(spec=["open", "fill", "upload_file", "snapshot", "submit", "close"])
        self.browser.open.return_value = "tab-1"
        self.browser.fill.return_value = {"status": "filled"}
        self.browser.snapshot.return_value = snapshot(resume_sha256=hashlib.sha256(self.pdf.read_bytes()).hexdigest())
        self.engines = {"codex": self.fake_engine(), "alternate": self.fake_engine()}
        self.runner = self.module("workflow").Workflow(self.root, self.browser, self.engines, lane="2")
        self.runner.store.enqueue(job())

    def apply(self):
        try:
            return self.runner.apply(["synthetic-1"], self.pdf, engine="codex")
        except (RuntimeError, OSError) as error:
            self.fail("Analysis failure must return retryable and preserve safe state: " + str(error))

    def test_analysis_quota_error_releases_new_claim_then_same_engine_can_fill(self):
        error = self.module("engines").EngineError("quota_exhausted")
        self.engines["codex"].run.side_effect = [error, {"status": "completed"}]
        result = self.apply()
        self.assertEqual(result["jobs"][0]["status"], "retryable")
        record = self.runner.store.get("synthetic-1")
        self.assertEqual(record["state"], "pending")
        self.assertIs(record["retryable"], True)
        self.assertIn("quota_exhausted", record["problem"])
        for key in ("lane", "claim", "claimed_at", "tab_id"):
            self.assertIsNone(record.get(key), "A released job must not retain claim/tab ownership")
        self.assertEqual(self.browser.mock_calls, [])
        self.engines["alternate"].run.assert_not_called()
        result = self.apply()
        self.assertEqual(result["jobs"][0]["status"], "filled")
        self.assertEqual(self.runner.store.get("synthetic-1")["state"], "filled")
        self.assertEqual(
            [call.args[0]["kind"] for call in self.engines["codex"].run.call_args_list],
            ["apply_analysis", "apply_analysis"],
        )
        self.browser.open.assert_called_once_with(job()["url"])
        self.browser.fill.assert_called_once()
        self.browser.upload_file.assert_called_once_with("tab-1", str(self.pdf.resolve()))
        self.browser.submit.assert_not_called()
        self.browser.close.assert_not_called()
        self.engines["alternate"].run.assert_not_called()

    def test_login_and_network_errors_are_retryable_without_browser_actions(self):
        errors = (self.module("engines").EngineError("login_required"), OSError("synthetic network unavailable"))
        for index, error in enumerate(errors):
            identity = "error-" + str(index)
            self.runner.store.enqueue(job(identity))
            self.engines["codex"].run.side_effect = error
            try:
                result = self.runner.apply([identity], self.pdf)
            except (RuntimeError, OSError) as caught:
                self.fail("Analysis error escaped instead of releasing its claim: " + str(caught))
            self.assertEqual(result["jobs"][0]["status"], "retryable")
            record = self.runner.store.get(identity)
            self.assertEqual(record["state"], "pending")
            self.assertIs(record["retryable"], True)
            self.assertTrue(record["problem"])
            self.assertIsNone(record.get("lane"))
        self.assertEqual(self.browser.mock_calls, [])

    def test_continuing_manual_job_keeps_tab_and_state_on_analysis_failure(self):
        self.runner.store.update(
            "synthetic-1",
            state="needs_manual",
            tab_id="held-tab",
            lane="2",
            pdf=str(self.pdf),
            fill_result={"status": "needs_manual"},
        )
        before = self.runner.store.get("synthetic-1")
        self.engines["codex"].run.side_effect = self.module("engines").EngineError("quota_exhausted")
        result = self.apply()
        self.assertEqual(result["jobs"][0]["status"], "retryable")
        after = self.runner.store.get("synthetic-1")
        self.assertEqual({key: value for key, value in after.items() if key != "problem"}, before)
        self.assertIn("quota_exhausted", after["problem"])
        self.assertEqual(self.browser.mock_calls, [])
        self.engines["alternate"].run.assert_not_called()

    def test_reconciliation_intent_and_submitted_jobs_are_never_reset(self):
        for state in ("needs_reconciliation", "submit_intent", "submitted"):
            with self.subTest(state=state):
                self.runner.store.enqueue(job(state))
                self.runner.store.update(state, state=state, tab_id="held-" + state, lane="2")
                before = self.runner.store.get(state)
                with self.assertRaises(ValueError):
                    self.runner.apply([state], self.pdf)
                self.assertEqual(self.runner.store.get(state), before)
        self.engines["codex"].run.assert_not_called()
        self.engines["alternate"].run.assert_not_called()
        self.assertEqual(self.browser.mock_calls, [])

    def test_failure_does_not_release_other_claims_from_a_previous_process(self):
        self.runner.store.enqueue(job("previous-process"))
        self.runner.store.claim("previous-process", lane="3")
        before = self.runner.store.get("previous-process")
        self.engines["codex"].run.side_effect = self.module("engines").EngineError("quota_exhausted")
        result = self.apply()
        self.assertEqual(result["jobs"][0]["status"], "retryable")
        self.assertEqual(self.runner.store.get("previous-process"), before)
        self.assertEqual(self.browser.mock_calls, [])


class RestorePdfIsolationTests(ReleaseCase):
    def records(self):
        records = []
        for identity in ("missing", "healthy"):
            path = self.root / (identity + ".pdf")
            path.write_bytes(b"%PDF-1.4\n% synthetic " + identity.encode() + b"\n%%EOF")
            receipt = {
                "name": path.name,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            records.append(
                {
                    **job(identity),
                    "state": "filled",
                    "lane": "1",
                    "tab_id": "tab-" + identity,
                    "pdf": str(path),
                    "upload_receipt": receipt,
                    "resume_sha256": receipt["sha256"],
                }
            )
        client = Mock(spec=["new_page", "list_pages", "evaluate_script", "upload_file", "submit", "close_page"])
        client.list_pages.return_value = [{"id": record["tab_id"], "url": record["url"]} for record in records]

        def observe(tab_id, function, *, operation="audit"):
            record = next(record for record in records if record["tab_id"] == tab_id)
            return {
                "fields": [
                    {
                        "id": "resume",
                        "label": "Resume",
                        "type": "file",
                        "files": [copy.deepcopy(record["upload_receipt"])],
                    }
                ],
                "errors": [],
            }

        client.evaluate_script.side_effect = observe
        return self.module("browser").BrowserBridge(client), client, records

    def test_missing_pdf_does_not_prevent_either_tab_binding_or_healthy_snapshot(self):
        bridge, client, records = self.records()
        missing, healthy = records
        Path(missing["pdf"]).unlink()
        bridge.uploads[missing["tab_id"]] = copy.deepcopy(missing["upload_receipt"])
        for record in records:
            try:
                result = bridge.restore(record)
            except OSError as error:
                self.fail("Restore must retain this tab and continue restoring the lane: " + str(error))
            if record is missing:
                self.assertEqual(result, {"restored": True, "pdf_ok": False})
        self.assertEqual(set(bridge.tabs), {record["tab_id"] for record in records})
        for record in records:
            self.assertEqual(bridge.tabs[record["tab_id"]]["job"]["id"], record["id"])
            self.assertEqual(bridge.observed_uploads[record["tab_id"]], record["upload_receipt"])
        self.assertNotIn(missing["tab_id"], bridge.uploads)
        self.assertIn(missing["tab_id"], bridge.restore_errors)
        self.assertEqual(bridge.snapshot(healthy["tab_id"], healthy["id"])["resume_sha256"], healthy["resume_sha256"])
        for operation in (bridge.snapshot, bridge.submit):
            with (
                self.subTest(operation=operation.__name__),
                self.assertRaisesRegex(ValueError, "Selected PDF missing/unreadable"),
            ):
                operation(missing["tab_id"], missing["id"])
        client.new_page.assert_not_called()
        client.upload_file.assert_not_called()
        client.submit.assert_not_called()
        client.close_page.assert_not_called()

    def test_unreadable_pdf_is_isolated_like_missing_pdf(self):
        bridge, client, records = self.records()
        missing, healthy = records
        original_read = Path.read_bytes

        def read(path):
            if path == Path(missing["pdf"]):
                raise PermissionError("synthetic PDF permission denied")
            return original_read(path)

        with patch.object(Path, "read_bytes", read):
            try:
                result = bridge.restore(missing)
            except OSError as error:
                self.fail("Unreadable PDF must produce an isolated restore error: " + str(error))
            self.assertEqual(result, {"restored": True, "pdf_ok": False})
            bridge.restore(healthy)
        self.assertEqual(set(bridge.tabs), {record["tab_id"] for record in records})
        self.assertNotIn(missing["tab_id"], bridge.uploads)
        for operation in (bridge.snapshot, bridge.submit):
            with (
                self.subTest(operation=operation.__name__),
                self.assertRaisesRegex(ValueError, "Selected PDF missing/unreadable"),
            ):
                operation(missing["tab_id"], missing["id"])
        self.assertEqual(bridge.snapshot(healthy["tab_id"], healthy["id"])["resume_sha256"], healthy["resume_sha256"])
        client.new_page.assert_not_called()
        client.upload_file.assert_not_called()
        client.submit.assert_not_called()
        client.close_page.assert_not_called()

    def test_restore_does_not_swallow_non_os_errors(self):
        bridge, client, records = self.records()
        with patch.object(Path, "read_bytes", side_effect=RuntimeError("synthetic programming error")):
            with self.assertRaisesRegex(RuntimeError, "synthetic programming error"):
                bridge.restore(records[0])
        client.new_page.assert_not_called()
        client.close_page.assert_not_called()

    def test_workflow_live_still_rejects_a_disappeared_pdf(self):
        self.write_json("profile.json", candidate("authorized"))
        pdf = self.root / "selected.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% synthetic resume\n%%EOF")
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        record = {**job(), "tab_id": "tab-1", "pdf": str(pdf), "resume_sha256": digest}
        browser = Mock(spec=["snapshot", "submit", "close"])
        live = snapshot(resume_sha256=digest)
        live["fields"][0]["value"] = candidate("authorized")["contact"]["legal_name"]["value"]
        browser.snapshot.return_value = live
        runner = self.module("workflow").Workflow(self.root, browser, {"codex": self.fake_engine()})
        pdf.unlink()
        with self.assertRaisesRegex(ValueError, "Selected PDF changed or disappeared"):
            runner._live(record)
        browser.submit.assert_not_called()
        browser.close.assert_not_called()
