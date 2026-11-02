"""Offline contracts for exact-question/full-scope answer identity.

Giả định của người thực thi: dùng API hiện có và fixture trong tempfile;
ghi giả định ở test vì lượt này chỉ cho sửa test, không sửa plan/receipt.
Một preview tổng hợp có hash đúng kiểm tra import độc lập với preview validation.
"""

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from test_support import SCOPE, ReleaseCase, fact, job


EARLY = "2001-01-01T00:00:00Z"
APPLIED_FILES = ("profile.json", "answers.json", "reviews.json", "config.json", "intake-import.json")


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def identity(answer):
    return answer["question"], json.dumps(answer["scope"], sort_keys=True)


class AnswerIdentityTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.intake = self.module("intake")
        self.resolver = self.module("profile")

    def answer(self, *, scope=None, answer_id="answer:display", value="synthetic-private-answer-A"):
        scope = copy.deepcopy(SCOPE if scope is None else scope)
        return {
            "id": answer_id,
            "question": scope["question"],
            **fact(value, scope=scope, source_id="user:exact-answer"),
            "confirmed_at": EARLY,
        }

    def prepare(self, name="user", answers=None, *, imported=True):
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
        self.document["answers"] = copy.deepcopy([self.answer()] if answers is None else answers)
        if imported:
            self.import_document(self.document)
        self.write_json(
            str(self.user.relative_to(self.root) / "reviews.json"),
            [{"job_id": "synthetic-1", "valid": True, "profile_revision": self.read("profile.json")["revision"]}],
        )

    def read(self, name):
        return json.loads((self.user / name).read_text(encoding="utf-8"))

    def write_document(self, document):
        self.path.write_text("```json\n" + json.dumps(document, ensure_ascii=False) + "\n```\n", encoding="utf-8")

    def preview(self, document):
        self.write_document(document)
        return self.intake.preview_import(self.user, self.path)

    def import_document(self, document):
        return self.intake.apply_import(self.user, self.preview(document), confirm=True)

    def invalidate(self):
        self.document["groups"]["education"]["records"][0]["graduation"]["value"] = "2032-05"
        self.import_document(self.document)
        for answer in self.read("profile.json")["answers"]:
            self.assertIs(answer["valid"], False)
            self.assertIn("invalidated_at", answer)

    def applied_bytes(self):
        # The editable intake is input, not an already applied data file.
        return {
            name: (self.user / name).read_bytes() if (self.user / name).exists() else None for name in APPLIED_FILES
        }

    def rejection_action(self, route, document):
        if route == "preview":
            self.write_document(document)
            return lambda: self.intake.preview_import(self.user, self.path)
        if route == "import":
            # Exercise apply_import's own validation with a matching input digest,
            # rather than allowing preview's rejection to stand in for import's.
            clean = copy.deepcopy(document)
            clean["answers"] = []
            preview = self.preview(clean)
            self.write_document(document)
            preview["sha256"] = hashlib.sha256(self.path.read_bytes()).hexdigest()
            preview["document"] = copy.deepcopy(document)
            return lambda: self.intake.apply_import(self.user, preview, confirm=True)
        return lambda: self.intake.update_profile(self.user, {"answers": document["answers"]}, confirm=True)

    def assert_rejected_without_writes(self, action):
        before = self.applied_bytes()
        storage = self.module("storage")
        with patch.object(storage, "atomic_write_json", wraps=storage.atomic_write_json) as writes:
            try:
                with self.assertRaises(ValueError) as caught:
                    action()
            finally:
                self.assertEqual(self.applied_bytes(), before, "Rejected input changed applied data")
                writes.assert_not_called()
        self.assertNotIn("synthetic-private-answer", str(caught.exception))

    def assert_states(self, expected_answers, valid):
        profile, ledger = self.read("profile.json"), self.read("answers.json")
        self.assertEqual(profile["answers"], ledger["answers"])
        self.assertEqual(ledger["profile_revision"], profile["revision"])
        self.assertIs(ledger["valid"], bool(valid) and all(valid))
        expected = {identity(answer): (answer, state) for answer, state in zip(expected_answers, valid)}
        self.assertEqual(len(expected), len(expected_answers))
        self.assertEqual(len(profile["answers"]), len(expected_answers))
        self.assertEqual({identity(answer) for answer in profile["answers"]}, set(expected))
        for saved in profile["answers"]:
            answer, state = expected[identity(saved)]
            self.assertIs(saved["valid"], state)
            self.assertEqual(saved["provenance"], answer["provenance"])
            self.assertEqual(saved["value"], answer["value"])
            for source in (profile, {**profile, "answers": ledger["answers"]}):
                scope = answer["scope"]
                resolved = [self.resolver.resolve_answer(source, answer["question"], scope)]
                # resolve_field constructs precisely the five documented scope keys.
                if set(scope) == set(SCOPE):
                    resolved.append(
                        self.resolver.resolve_field(
                            source,
                            {"id": "arbitrary-field-id", "label": answer["question"]},
                            job(
                                country=scope["country"],
                                company=scope["employer"],
                                level=scope["job_type"],
                                start_date=scope["effective_at"],
                            ),
                        )
                    )
                for result in resolved:
                    self.assertEqual(result["status"], "confirmed" if state else "unknown")
                    self.assertEqual(result["value"], answer["value"] if state else None)

    def fresh(self, answer):
        saved = next(item for item in self.read("profile.json")["answers"] if identity(item) == identity(answer))
        boundaries = [instant(saved["confirmed_at"])]
        if saved.get("invalidated_at"):
            boundaries.append(instant(saved["invalidated_at"]))
        updated = copy.deepcopy(answer)
        updated["confirmed_at"] = (max(boundaries) + timedelta(seconds=1)).isoformat()
        return updated

    def save_answers(self, route, answers):
        if route == "update":
            return self.intake.update_profile(self.user, {"answers": answers}, confirm=True)
        document = copy.deepcopy(self.document)
        document["answers"] = copy.deepcopy(answers)
        return self.import_document(document)

    def test_duplicate_stale_answers_rejected_by_every_entry_point_without_writes(self):
        for route in ("preview", "import", "update"):
            for different_id in (False, True):
                with self.subTest(route=route, different_id=different_id):
                    self.prepare(f"duplicate-{route}-{different_id}")
                    self.invalidate()
                    first = copy.deepcopy(self.document["answers"][0])
                    second = copy.deepcopy(first)
                    if different_id:
                        second["id"] = "different:presentation"
                    duplicate = copy.deepcopy(self.document)
                    duplicate["answers"] = [first, second]
                    self.assert_rejected_without_writes(self.rejection_action(route, duplicate))

    def test_first_import_duplicates_rejected_without_creating_applied_data(self):
        for route in ("preview", "import", "update"):
            for different_id in (False, True):
                with self.subTest(route=route, different_id=different_id):
                    self.prepare(f"first-{route}-{different_id}", imported=False)
                    duplicate = copy.deepcopy(self.document)
                    duplicate["answers"].append(copy.deepcopy(duplicate["answers"][0]))
                    if different_id:
                        duplicate["answers"][1]["id"] = "another:display"
                    self.assert_rejected_without_writes(self.rejection_action(route, duplicate))

    def test_duplicate_identity_ignores_scope_key_order_and_answer_value(self):
        for route in ("preview", "import", "update"):
            with self.subTest(route=route):
                self.prepare("key-order-" + route)
                duplicate = copy.deepcopy(self.document)
                second = copy.deepcopy(duplicate["answers"][0])
                second.update(id="new:display", value="synthetic-private-answer-B")
                second["scope"] = dict(reversed(list(second["scope"].items())))
                duplicate["answers"].append(second)
                self.assert_rejected_without_writes(self.rejection_action(route, duplicate))

    def test_same_display_id_different_scopes_survive_reordering(self):
        for field, value in (("employer", "Other Example Labs"), ("job_type", "co-op")):
            for shared_id in (False, True):
                for route in ("import", "update"):
                    with self.subTest(field=field, shared_id=shared_id, route=route):
                        first = self.answer()
                        second = self.answer(
                            scope={**SCOPE, field: value},
                            value="synthetic-private-answer-B",
                            answer_id=first["id"] if shared_id else "different:display",
                        )
                        self.prepare(f"reorder-{field}-{shared_id}-{route}", [first, second])
                        self.assert_states([first, second], [True, True])
                        self.save_answers(route, [second, first])
                        self.assert_states([first, second], [True, True])

    def test_reconfirmation_is_independent_for_scopes_with_same_display_id(self):
        for field, value in (("employer", "Other Example Labs"), ("job_type", "co-op")):
            for route in ("import", "update"):
                for fresh_index in (0, 1):
                    with self.subTest(field=field, route=route, fresh_index=fresh_index):
                        answers = [
                            self.answer(),
                            self.answer(scope={**SCOPE, field: value}, value="synthetic-private-answer-B"),
                        ]
                        self.prepare(f"fresh-{field}-{route}-{fresh_index}", answers)
                        self.invalidate()
                        answers[fresh_index] = self.fresh(answers[fresh_index])
                        self.save_answers(route, list(reversed(answers)))
                        self.assert_states(answers, [fresh_index == 0, fresh_index == 1])

    def test_full_scope_includes_additional_and_nested_keys(self):
        first = self.answer(scope={**SCOPE, "context": {"region": "east", "policy": {"version": 1}}})
        second = self.answer(
            scope={**SCOPE, "context": {"policy": {"version": 2}, "region": "east"}}, value="synthetic-private-answer-B"
        )
        self.prepare(answers=[first, second])
        self.save_answers("import", [second, first])
        self.assert_states([first, second], [True, True])

    def test_nested_scope_key_order_is_also_duplicate_identity(self):
        for route in ("preview", "import", "update"):
            with self.subTest(route=route):
                first = self.answer(scope={**SCOPE, "context": {"region": "east", "policy": {"v": 1, "mode": "x"}}})
                self.prepare("nested-" + route, [first])
                second = copy.deepcopy(first)
                second["id"] = "another:display"
                second["scope"]["context"] = {"policy": {"mode": "x", "v": 1}, "region": "east"}
                document = copy.deepcopy(self.document)
                document["answers"] = [first, second]
                self.assert_rejected_without_writes(self.rejection_action(route, document))

    def test_question_identity_preserves_exact_text(self):
        for index, question in enumerate(
            (SCOPE["question"].lower(), " " + SCOPE["question"], SCOPE["question"] + "\n")
        ):
            with self.subTest(question=question):
                first = self.answer()
                second = self.answer(scope={**SCOPE, "question": question}, value="synthetic-private-answer-B")
                self.prepare("question-" + str(index), [first, second])
                self.save_answers("import", [second, first])
                self.assert_states([first, second], [True, True])

    def test_changed_display_id_cannot_revive_stale_answer(self):
        for route in ("import", "update"):
            with self.subTest(route=route):
                self.prepare("stale-id-" + route)
                self.invalidate()
                answer = copy.deepcopy(self.document["answers"][0])
                answer["id"] = "replacement:display"
                self.save_answers(route, [answer])
                self.assert_states([answer], [False])

    def test_fresh_confirmation_accepts_changed_or_missing_display_id(self):
        for route in ("import", "update"):
            for missing_id in (False, True):
                with self.subTest(route=route, missing_id=missing_id):
                    self.prepare(f"fresh-id-{route}-{missing_id}")
                    self.invalidate()
                    answer = self.fresh(self.document["answers"][0])
                    if missing_id:
                        answer.pop("id")
                    else:
                        answer["id"] = "replacement:display"
                    self.save_answers(route, [answer])
                    self.assert_states([answer], [True])
                    self.assertEqual(self.read("profile.json")["answers"][0]["confirmed_at"], answer["confirmed_at"])

    def test_display_id_change_alone_preserves_a_valid_answer(self):
        for route in ("import", "update"):
            with self.subTest(route=route):
                self.prepare("valid-id-" + route)
                answer = copy.deepcopy(self.document["answers"][0])
                answer["id"] = "replacement:display"
                self.save_answers(route, [answer])
                self.assert_states([answer], [True])

    def test_omission_and_stale_readdition_preserve_invalidation_history(self):
        for route in ("import", "update"):
            with self.subTest(route=route):
                self.prepare("omission-" + route)
                self.invalidate()
                saved = copy.deepcopy(self.read("profile.json")["answers"])
                self.save_answers(route, [])
                self.assertEqual(self.read("profile.json")["answers"], saved)
                self.save_answers(route, self.document["answers"])
                self.assertEqual(self.read("profile.json")["answers"], saved)
                self.assert_states(self.document["answers"], [False])

    def test_equal_invalidation_in_different_timezone_is_stale_but_one_second_newer_is_fresh(self):
        for route in ("import", "update"):
            with self.subTest(route=route):
                self.prepare("offset-" + route)
                self.invalidate()
                answer = copy.deepcopy(self.document["answers"][0])
                saved = self.read("profile.json")["answers"][0]
                boundary = instant(saved["invalidated_at"])
                answer["confirmed_at"] = boundary.astimezone(timezone(timedelta(hours=-7))).isoformat()
                self.assertNotEqual(answer["confirmed_at"], saved["invalidated_at"])
                self.save_answers(route, [answer])
                self.assert_states([answer], [False])
                answer["confirmed_at"] = (
                    (boundary + timedelta(seconds=1)).astimezone(timezone(timedelta(hours=9))).isoformat()
                )
                self.save_answers(route, [answer])
                self.assert_states([answer], [True])

    def test_reconfirmation_must_be_strictly_newer_than_previous_confirmation_too(self):
        for route in ("import", "update"):
            for delta in (-1, 0, 1):
                with self.subTest(route=route, delta=delta):
                    answer = self.answer()
                    answer["confirmed_at"] = "2099-01-01T00:00:00Z"
                    self.prepare(f"previous-time-{route}-{delta}", [answer])
                    self.invalidate()
                    incoming = copy.deepcopy(answer)
                    incoming["confirmed_at"] = (
                        (instant(answer["confirmed_at"]) + timedelta(seconds=delta))
                        .astimezone(timezone(timedelta(hours=-5)))
                        .isoformat()
                    )
                    self.save_answers(route, [incoming])
                    self.assert_states([answer], [delta > 0])

    def test_naive_confirmation_rejected_without_timezone_guessing(self):
        for route in ("preview", "import", "update"):
            with self.subTest(route=route):
                self.prepare("naive-" + route)
                self.invalidate()
                document = copy.deepcopy(self.document)
                document["answers"][0]["confirmed_at"] = "2099-01-01T00:00:00"
                self.assert_rejected_without_writes(self.rejection_action(route, document))

    def test_fresh_update_profile_synchronizes_both_stores_and_both_resolvers(self):
        answers = [
            self.answer(),
            self.answer(scope={**SCOPE, "employer": "Other Example Labs"}, value="synthetic-private-answer-B"),
        ]
        self.prepare(answers=answers)
        self.invalidate()
        fresh = self.fresh(answers[0])
        self.save_answers("update", [answers[1], fresh])
        self.assert_states([fresh, answers[1]], [True, False])
        self.assertEqual(self.read("profile.json")["answers"][0]["confirmed_at"], fresh["confirmed_at"])

    def test_duplicate_previous_identity_fails_closed(self):
        for route in ("preview", "import", "update"):
            with self.subTest(route=route):
                self.prepare("previous-duplicate-" + route)
                saved = self.read("profile.json")
                duplicate = copy.deepcopy(saved["answers"][0])
                duplicate["id"] = "another:display"
                saved["answers"].append(duplicate)
                self.write_json(str(self.user.relative_to(self.root) / "profile.json"), saved)
                # Fail closed permits rejection or retaining only unusable evidence;
                # it must never silently choose one duplicate as a confirmed fact.
                before = self.applied_bytes()
                try:
                    if route == "preview":
                        result = self.preview(self.document)
                        self.assertTrue(result["needs_reconfirmation"])
                        self.assertEqual(self.applied_bytes(), before)
                    else:
                        self.save_answers(route, self.document["answers"])
                        data = self.read("profile.json")
                        resolved = self.resolver.resolve_answer(data, SCOPE["question"], SCOPE)
                        self.assertEqual(resolved["status"], "unknown")
                        self.assertIsNone(resolved["value"])
                except ValueError:
                    self.assertEqual(self.applied_bytes(), before)
