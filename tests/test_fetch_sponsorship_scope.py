"""Synthetic, offline contracts for job-scoped sponsorship filtering.

Giả định của người thực thi: unresolved keys use the stable labels
``sponsorship_now`` and ``sponsorship_future`` in unknowns; only test files may
be changed in this phase, so the plan's assumptions section is left untouched.
"""

from copy import deepcopy
from itertools import permutations

from test_support import ReleaseCase, candidate, fact, job


KEYS = ("sponsorship_now", "sponsorship_future")


def record(now=False, future=False, **context):
    return {"sponsorship_now": fact(now), "sponsorship_future": fact(future), **context}


class SponsorshipScopeTests(ReleaseCase):
    def evaluate(self, records, *, item=None, preference="exclude"):
        profile = candidate()
        profile["authorization"] = deepcopy(records)
        profile["preferences"]["no_sponsor_conflict"] = preference
        item = deepcopy(item) if item is not None else job(sponsorship="not_supported")
        return self.module("fetch").evaluate_job(item, profile)

    def assert_result(self, result, decision, unresolved=(), reasons=()):
        self.assertEqual(set(result), {"decision", "unknowns", "reasons"})
        self.assertEqual(result["decision"], decision)
        self.assertEqual(result["reasons"], list(reasons))
        self.assertEqual([key for key in result["unknowns"] if key in KEYS], list(unresolved))
        self.assertEqual(len(result["unknowns"]), len(set(result["unknowns"])))

    def test_internship_and_fulltime_answers_do_not_leak_in_either_direction(self):
        for internship_need in (False, True):
            for current_type in ("internship", "fulltime"):
                with self.subTest(internship_need=internship_need, job_type=current_type):
                    records = [
                        record(future=internship_need, job_type="internship"),
                        record(future=not internship_need, job_type="fulltime"),
                    ]
                    item = job(level=current_type, sponsorship="not_supported")
                    # Both types are targeted; this assertion isolates sponsorship.
                    profile = candidate()
                    profile["targets"]["job_types"] = ["internship", "fulltime"]
                    profile["authorization"] = records
                    profile["preferences"]["no_sponsor_conflict"] = "exclude"
                    result = self.module("fetch").evaluate_job(item, profile)
                    needed = internship_need if current_type == "internship" else not internship_need
                    self.assert_result(
                        result,
                        "exclude" if needed else "keep",
                        reasons=("sponsorship_conflict",) if needed else (),
                    )

    def test_known_context_mismatch_does_not_poison_global_false(self):
        for context in (
            {"country": "CA"},
            {"country": "United States"},  # No guessed equivalence to US.
            {"job_type": "fulltime"},
            {"employer": "Other Example"},
            {"effective_at": "2030-06-02"},
            {"valid_from": "2030-07-01"},
            {"valid_to": "2030-05-31"},
        ):
            for enveloped in (False, True):
                with self.subTest(context=context, enveloped=enveloped):
                    metadata = {key: fact(value) if enveloped else value for key, value in context.items()}
                    result = self.evaluate(
                        [record(), record(True, True, **metadata)],
                        item=job(start_date="2030-06-01", sponsorship="not_supported"),
                    )
                    self.assert_result(result, "keep")

    def test_matching_raw_and_confirmed_context_establish_conflict(self):
        for enveloped in (False, True):
            with self.subTest(enveloped=enveloped):
                context = {"country": "US", "job_type": "internship", "employer": "Example Labs"}
                if enveloped:
                    context = {key: fact(value) for key, value in context.items()}
                self.assert_result(
                    self.evaluate([record(future=True, **context)]),
                    "exclude",
                    reasons=("sponsorship_conflict",),
                )

    def test_job_type_and_employer_job_aliases_supply_context(self):
        item = job(job_type="internship", employer="Example Labs", sponsorship="not_supported")
        del item["level"]
        del item["company"]
        self.assert_result(
            self.evaluate([record(future=True, job_type="internship", employer="Example Labs")], item=item),
            "exclude",
            reasons=("sponsorship_conflict",),
        )

    def test_fact_scope_retains_narrower_restrictions_from_both_sources(self):
        for outer, inner in (
            ({"country": "US"}, {"job_type": "fulltime"}),
            ({"job_type": "fulltime"}, {"country": "US"}),
            ({"country": "US"}, {"country": "CA"}),
            ({"country": "CA"}, {"country": "US"}),
            ({"employer": "Other Example"}, {"employer": "Example Labs"}),
        ):
            with self.subTest(outer=outer, inner=inner):
                scoped = {**outer, "sponsorship_future": fact(True, scope=inner)}
                self.assert_result(self.evaluate([record(), scoped]), "keep")
        matched = {"country": fact("US"), "sponsorship_future": fact(True, scope={"job_type": "internship"})}
        self.assert_result(
            self.evaluate([matched]),
            "exclude",
            ("sponsorship_now",),
            ("sponsorship_conflict",),
        )

    def test_context_envelope_scope_cannot_lose_its_employer_restriction(self):
        for employer, decision in (("Example Labs", "exclude"), ("Other Example", "keep")):
            with self.subTest(employer=employer):
                scoped = record(future=True, country=fact("US", scope={"employer": employer}))
                # A matching false record would contradict True, so include it
                # only for the known mismatch case.
                records = [scoped] if decision == "exclude" else [record(), scoped]
                self.assert_result(
                    self.evaluate(records),
                    decision,
                    reasons=("sponsorship_conflict",) if decision == "exclude" else (),
                )

    def test_now_and_future_resolve_independently(self):
        for key in KEYS:
            with self.subTest(true_key=key):
                evidence = record()
                evidence[key] = fact(True)
                self.assert_result(
                    self.evaluate([evidence]),
                    "exclude",
                    reasons=("sponsorship_conflict",),
                )
                self.assert_result(self.evaluate([evidence], preference="review"), "review")
        self.assert_result(self.evaluate([record()]), "keep")

    def test_missing_key_is_unknown_but_does_not_hide_independent_true(self):
        for present, missing in ((KEYS[0], KEYS[1]), (KEYS[1], KEYS[0])):
            for value in (False, True):
                with self.subTest(present=present, value=value):
                    self.assert_result(
                        self.evaluate([{present: fact(value)}]),
                        "exclude" if value else "review",
                        (missing,),
                        ("sponsorship_conflict",) if value else (),
                    )
        self.assert_result(self.evaluate([]), "review", KEYS)

    def test_inclusive_date_window_before_during_and_after(self):
        for enveloped in (False, True):
            window = {"valid_from": "2030-06-01", "valid_to": "2030-08-31"}
            if enveloped:
                window = {key: fact(value) for key, value in window.items()}
            for start, matches in (
                ("2030-05-31", False),
                ("2030-06-01", True),
                ("2030-07-15", True),
                ("2030-08-31", True),
                ("2030-09-01", False),
            ):
                with self.subTest(enveloped=enveloped, start=start):
                    self.assert_result(
                        self.evaluate(
                            [record(future=True, **window)],
                            item=job(
                                start_date=start,
                                sponsorship="not_supported",
                            ),
                        ),
                        "exclude" if matches else "review",
                        () if matches else KEYS,
                        ("sponsorship_conflict",) if matches else (),
                    )

    def test_effective_at_is_exact_not_a_lower_bound(self):
        for enveloped in (False, True):
            effective = fact("2030-06-01") if enveloped else "2030-06-01"
            for start in ("2030-05-31", "2030-06-01", "2030-06-02"):
                with self.subTest(enveloped=enveloped, start=start):
                    matches = start == "2030-06-01"
                    self.assert_result(
                        self.evaluate(
                            [record(future=True, effective_at=effective)],
                            item=job(
                                start_date=start,
                                sponsorship="not_supported",
                            ),
                        ),
                        "exclude" if matches else "review",
                        () if matches else KEYS,
                        ("sponsorship_conflict",) if matches else (),
                    )

    def test_dated_evidence_requires_actual_precise_job_date(self):
        for start in (None, "", "unknown", "2030", "2030-06"):
            for context in (
                {"effective_at": "2030-06-01"},
                {"valid_from": "2030-06-01", "valid_to": "2030-08-31"},
            ):
                with self.subTest(start=start, context=context):
                    self.assert_result(
                        self.evaluate(
                            [record(future=True, **context)],
                            item=job(
                                start_date=start,
                                sponsorship="not_supported",
                                # Season/year must not fabricate the missing date.
                                season="summer",
                                year=2030,
                            ),
                        ),
                        "review",
                        KEYS,
                    )

    def test_date_restrictions_from_record_and_fact_are_both_retained(self):
        for scope in (
            {"valid_from": "2030-07-01"},
            {"valid_to": "2030-05-31"},
            {"effective_at": "2030-06-02"},
        ):
            with self.subTest(scope=scope):
                evidence = {
                    "valid_from": "2030-06-01",
                    "valid_to": "2030-08-31",
                    "sponsorship_future": fact(True, scope=scope),
                }
                self.assert_result(
                    self.evaluate(
                        [record(), evidence],
                        item=job(start_date="2030-06-01", sponsorship="not_supported"),
                    ),
                    "keep",
                )

    def test_visa_expiry_does_not_define_authorization_window(self):
        for expiry in ("2029-01-01", "2031-01-01"):
            with self.subTest(expiry=expiry):
                self.assert_result(
                    self.evaluate(
                        [record(future=True, visa_expiry=fact(expiry))],
                        item=job(start_date="2030-06-01", sponsorship="not_supported"),
                    ),
                    "exclude",
                    reasons=("sponsorship_conflict",),
                )

    def test_same_key_contradictions_are_unresolved_regardless_of_order_or_scope(self):
        for key in KEYS:
            for context in ({}, {"employer": "Example Labs"}):
                evidence = record(**context)
                evidence[key] = fact(True)
                for records in permutations([record(), evidence]):
                    with self.subTest(key=key, context=context, order=records):
                        self.assert_result(self.evaluate(records), "review", (key,))

    def test_known_mismatch_false_does_not_poison_matching_true(self):
        self.assert_result(
            self.evaluate([record(job_type="fulltime"), record(future=True, job_type="internship")]),
            "exclude",
            reasons=("sponsorship_conflict",),
        )

    def test_unusable_fact_envelopes_never_establish_true_or_false(self):
        stale = fact(True)
        stale["valid"] = False
        missing_provenance = fact(True)
        del missing_provenance["provenance"]
        unconfirmed = fact(True)
        del unconfirmed["confirmed_at"]
        for unusable in (
            stale,
            fact(True, "stale"),
            fact(None, "unknown"),
            fact(None, "declined"),
            fact(None, "not_applicable"),
            fact(True, "unknown"),
            fact("yes"),
            fact(1),
            fact("False"),
            missing_provenance,
            unconfirmed,
        ):
            with self.subTest(unusable=unusable):
                self.assert_result(
                    self.evaluate(
                        [
                            {
                                "sponsorship_now": fact(False),
                                "sponsorship_future": unusable,
                            }
                        ]
                    ),
                    "review",
                    ("sponsorship_future",),
                )

    def test_raw_booleans_are_not_implicitly_confirmed_and_do_not_crash(self):
        for value in (True, False):
            with self.subTest(value=value):
                try:
                    result = self.evaluate(
                        [
                            {
                                "sponsorship_now": fact(False),
                                "sponsorship_future": value,
                            }
                        ]
                    )
                except (AttributeError, TypeError) as error:
                    self.fail(f"Unconfirmed raw boolean must remain unknown without crashing: {error}")
                self.assert_result(result, "review", ("sponsorship_future",))

    def test_missing_or_unconfirmed_required_context_cannot_establish_match(self):
        for context_key, job_key in (
            ("country", "country"),
            ("job_type", "level"),
            ("employer", "company"),
        ):
            value = {"country": "US", "job_type": "internship", "employer": "Example Labs"}[context_key]
            for missing in (None, "", "unknown"):
                with self.subTest(context_key=context_key, missing=missing):
                    item = job(sponsorship="not_supported")
                    if missing is None:
                        del item[job_key]
                    else:
                        item[job_key] = missing
                    self.assert_result(
                        self.evaluate(
                            [record(future=True, **{context_key: value})],
                            item=item,
                        ),
                        "review",
                        KEYS,
                    )
            for metadata in (None, "unknown", fact(None, "unknown"), fact(value, "declined")):
                with self.subTest(context_key=context_key, metadata=metadata):
                    self.assert_result(
                        self.evaluate(
                            [
                                record(),
                                {context_key: metadata, "sponsorship_future": fact(True)},
                            ]
                        ),
                        "review",
                        ("sponsorship_future",),
                    )

    def test_extra_question_or_definition_scope_is_not_global_evidence(self):
        for restriction in (
            {"question": "Will you require employer support for this specific program?"},
            {"definition": "Sponsorship includes university paperwork"},
            {"unrecognized_restriction": "only one program"},
        ):
            for per_fact in (False, True):
                with self.subTest(restriction=restriction, per_fact=per_fact):
                    evidence = (
                        {"sponsorship_future": fact(True, scope=restriction)}
                        if per_fact
                        else {**restriction, "sponsorship_future": fact(True)}
                    )
                    self.assert_result(self.evaluate([record(), evidence]), "review", ("sponsorship_future",))

    def test_known_mismatch_takes_precedence_over_unknown_extra_scope(self):
        evidence = {
            "job_type": "fulltime",
            "sponsorship_future": fact(True, scope={"question": "Program-specific support?"}),
        }
        self.assert_result(self.evaluate([record(), evidence]), "keep")

    def test_unknown_matching_context_can_ambiguate_true_without_becoming_false(self):
        for key in KEYS:
            for possible_value in (False, True):
                evidence = record()
                evidence[key] = fact(True)
                ambiguous = {"country": fact(None, "unknown"), key: fact(possible_value)}
                for records in permutations([evidence, ambiguous]):
                    with self.subTest(key=key, possible_value=possible_value, order=records):
                        self.assert_result(self.evaluate(records), "review", (key,))

    def test_jd_restriction_and_preferences_keep_existing_global_true_behavior(self):
        for restriction in (
            {"sponsorship": "not_supported"},
            {"description": "No sponsorship is available."},
            {"description": "We cannot sponsor candidates."},
            {"description": "We do not sponsor candidates."},
        ):
            for preference in ("review", "exclude"):
                with self.subTest(restriction=restriction, preference=preference):
                    self.assert_result(
                        self.evaluate(
                            [record(future=True)],
                            item=job(**restriction),
                            preference=preference,
                        ),
                        preference,
                        reasons=("sponsorship_conflict",) if preference == "exclude" else (),
                    )

    def test_jd_silence_and_all_false_do_not_establish_eligibility(self):
        for records in ([], [record()], [record(future=True)]):
            with self.subTest(records=records):
                result = self.evaluate(records, item=job())
                self.assert_result(result, "keep", KEYS if not records else ())
                self.assertIn("eligibility", result["unknowns"])
                profile = candidate()
                profile["authorization"] = records
                profile["preferences"]["eligibility"] = "explicit_only"
                strict = self.module("fetch").evaluate_job(job(), profile)
                self.assert_result(strict, "exclude", KEYS if not records else (), ("explicit_eligibility_required",))

    def test_student_mechanism_graduation_and_eeo_do_not_answer_sponsorship(self):
        profile = candidate()
        profile["authorization"][0]["mechanism_status"] = fact("granted")
        profile["eeo"] = {"citizenship": fact("US")}
        self.assert_result(
            self.module("fetch").evaluate_job(
                job(sponsorship="not_supported"),
                profile,
            ),
            "review",
            KEYS,
        )

    def test_other_confirmed_exclusions_still_apply_when_sponsorship_is_unknown(self):
        for changes, reason in (
            ({"country": "CA"}, "country"),
            ({"role": "Sales Intern"}, "role"),
            ({"level": "senior"}, "level"),
            ({"season": "fall"}, "season"),
            ({"year": 2031}, "year"),
            ({"company": "Excluded Example"}, "excluded_employer"),
            ({"location": "Excluded City"}, "excluded_location"),
        ):
            with self.subTest(changes=changes):
                profile = candidate()
                profile["authorization"] = []
                profile["preferences"]["no_sponsor_conflict"] = "exclude"
                profile["preferences"]["excluded_employers"] = ["Excluded Example"]
                profile["logistics"] = {"excluded_locations": ["Excluded City"]}
                self.assert_result(
                    self.module("fetch").evaluate_job(
                        job(sponsorship="not_supported", **changes),
                        profile,
                    ),
                    "exclude",
                    KEYS,
                    (reason,),
                )

    def test_evaluation_preserves_deep_profile_job_facts_and_form_answers(self):
        for preference in ("review", "exclude"):
            with self.subTest(preference=preference):
                profile = candidate()
                profile["authorization"] = [record(future=True, job_type="internship")]
                profile["preferences"]["no_sponsor_conflict"] = preference
                scope = {"country": "US", "job_type": "internship", "employer": "Example Labs"}
                profile["answers"] = [{"question": "Sponsorship?", **fact(False, scope=scope)}]
                item = job(sponsorship="not_supported", start_date="2030-06-01")
                before_profile, before_job = deepcopy(profile), deepcopy(item)
                factual_resolver = self.module("profile")
                answer_before = factual_resolver.resolve_answer(profile, "Sponsorship?", scope)
                result = self.module("fetch").evaluate_job(item, profile)
                self.assert_result(
                    result, preference, reasons=("sponsorship_conflict",) if preference == "exclude" else ()
                )
                self.assertEqual(profile, before_profile)
                self.assertEqual(item, before_job)
                self.assertEqual(factual_resolver.resolve_answer(profile, "Sponsorship?", scope), answer_before)
