"""Ported answer rules against the original pipeline's real question corpus.

tests/cases/corpus.json holds real form labels with the rule key
``k`` the original pipeline's judge() picks for each (``kByAts`` overrides it
per ATS, ``ats`` limits the ATSs; ``null`` means no rule may match). Only the
label, kind, Workday field id and expected key were copied; places and
employers in labels were swapped for invented ones. Each label runs through
this repo's planner for the same ATS with a synthetic profile whose every fact
names itself, so the planned value tells which fact the label matched.

The repo deliberately answers far fewer labels than the original: it never
infers an answer from wording. Every mismatch is listed; a known gap needs an
entry with its reason in GAPS (by original key, when the repo matches nothing)
or in LABEL_GAPS (by label, when the repo matches a different key). Unused
entries fail too, so the allowlist cannot go stale.
"""

import json
import sys
import unittest
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from autoapply_local import rippling_driver  # noqa: E402
from autoapply_local.greenhouse_driver import GreenhouseDriver  # noqa: E402
from autoapply_local.profile import fact  # noqa: E402
from autoapply_local.workday_driver import WorkdayDriver  # noqa: E402

CORPUS = json.loads((RELEASE / "tests" / "cases" / "corpus.json").read_text(encoding="utf-8"))
ATS = ("wd", "gh", "rp")
JOB = {"company": "Example Co", "country": "US", "level": "internship", "start_date": "2027-06-01"}
SEL = (
    "I am currently eligible to work, and will in the future be eligible to work, "
    "in the country of this position without visa sponsorship."
)

# This repo's fact names -> the original pipeline's rule keys (the one rename table).
RENAMED = {
    "contact.legal_name": "full_name",
    "contact.first_name": "first",
    "contact.last_name": "last",
    "contact.preferred_name": "preferred_first",
    "contact.application_email": "email",
    "contact.phone": "phone",
    "contact.city": "city_text",
    "contact.state": "state",
    "contact.street": "address",
    "contact.postcode": "zip",
    "contact.linkedin": "linkedin",
    "contact.github": "github",
    "contact.portfolio": "website",
    "eeo.gender": "gender",
    "eeo.race": "race",
    "eeo.hispanic_ethnicity": "hispanic",
    "eeo.veteran_status": "veteran",
    "eeo.disability_status": "disability",
    "resume": "resume",
}
CONTACT = [name.split(".", 1)[1] for name in RENAMED if name.startswith("contact.")]
EEO = [name.split(".", 1)[1] for name in RENAMED if name.startswith("eeo.")]
# Original keys whose answer says the candidate needs visa sponsorship.
SPONSORSHIP_NEEDED = {
    "sponsor",
    "sponsor_note",
    "sponsor_followup",
    "sponsor_3to7",
    "sponsor_text",
    "sponsor_other",
    "sponsorship_type",
}
KIND_TYPES = {
    "text": "text",
    "digits": "text",
    "textarea": "textarea",
    "choice": "select",
    "select": "select",
    "radio": "radio",
    "checks": "checkbox-group",
    "multi": "checkbox-group",
    "box": "checkbox",
    "file": "file",
}

# Known gaps: why this repo picks no rule for labels the original answers, by original key.
GAP_GROUPS = {
    "authorization, sponsorship, citizenship, visa and export status are never inferred from wording; "
    "they come only from an answer the user confirmed for this exact question": (
        "auth_any auth_canada auth_change auth_change_explain auth_country_live auth_country_located "
        "auth_expiring auth_non_us auth_permanent auth_permanent_any_employer auth_statement_select auth_uk "
        "auth_us citizenship_country citizenship_country_text citizenship_status clearance_able currently_cpt "
        "currently_f1_holder deemed_export i9_verification irca_ack live_in_us opt_stem_eligible require_auth "
        "right_to_work_text select_one_citizen sponsor sponsor_3to7 sponsor_followup sponsor_note sponsor_other "
        "sponsor_text sponsorship_type student_visa temp_work_auth unrestricted us_person us_person_export "
        "visa_details visa_exp visa_status_text work_auth_status work_eligibility_type work_live_us work_proof"
    ),
    "voluntary self-identification fills only the ATS's fixed EEO field ids (Greenhouse, Lever) "
    "from confirmed answers, never a label": (
        "accommodation age_category disability first_gen gender hispanic military_service military_status "
        "orientation pronouns pronouns_text race socio_economic transgender veteran"
    ),
    "contact facts match only an exact common label or the ATS's fixed field id "
    "(Rippling: field names only); other wordings are held for the user": (
        "address address_full alt_email city_state_text city_text contact_best_text country country_res county "
        "current_location_text email first full_name github github_username last legal_first legal_last "
        "linkedin location name_pronunciation perm_city perm_state phone phone_digits10 phone_type "
        "preferred_first preferred_last ssn state state_text website zip"
    ),
    "education facts fill only through a record binding (school, degree, major); "
    "every other education question is held for the user": (
        "class_standing class_standing_next coop_term degree degree_program_text degrees_completed discipline "
        "edu_completed edu_end_month edu_end_year edu_end_year_choice edu_highest edu_start_month edu_start_year "
        "edu_start_year_choice enrolled enrolled_during final_internship gpa gpa_choice gpa_major gpa_major_choice "
        "gpa_other gpa_scale gpa_scale_text gpa_threshold grad_before grad_between grad_choice grad_mmyy "
        "grad_mmyyyy grad_month grad_month_choice grad_text grad_year high_school highest_edu_text hs_year "
        "hs_year_choice major_minor_text major_text masters_gap_job minor minor_choice months_remaining "
        "other_school program_year pursue_masters pursuing_grad recent_grad_or_student returning_program "
        "scholar school school_approval school_choice school_email senior_or_grad_future stem_studying "
        "student_in_area student_level_list study_level transcript_avail tutored_ta"
    ),
    "start dates, schedules, location, relocation and role preferences are employer-scoped; "
    "only the user's answer bank answers them": (
        "ack_no_housing avail_fulltime driver eng_role fall_2027 fulltime40 fulltime_start fulltime_start_month "
        "hours_school_year housing intern_duration intern_end intern_location intern_schedule_statement "
        "intern_timeframe intern_track location_pref location_requirement notice_period notice_period_text "
        "office_days_text onsite onsite_days_text onsite_requirements open_locations_text other_positions "
        "overtime_weekends reloc_assist relocate_cities_text relocate_willing role_preference service_line "
        "shift_availability start_availability_text start_date start_date_month_year term_start_fall2027 "
        "time_constraints transportation travel travel_assist weeks_available work_location_status"
    ),
    "pay expectations are never inferred": "comp_bucket comp_comfort pay_range_align salary salary_choice salary_numeric",
    "consent, agreements, background and employer-history questions bind to one employer "
    "and are never inferred": (
        "age18 ai_consent ai_consent_screening ai_interview associate_status background basic_requirements "
        "biometric conflict_of_interest current_contractor dismissed employee_id explicit_consent felony "
        "felony_explain finra_certs finra_detail finra_exam former_worker_record full_legal_name_ack gov_coi "
        "gov_entity gov_exclusion gov_official id_match inquire_employer interview_recording license_denied "
        "limiting_agreement meets_min_qual no_impediment noncompete outstanding_offers own_business people_screening "
        "political_contrib prev_applied prev_interviewed privacy_ack regulatory relatives relatives_text "
        "securities_conviction securities_license securities_license_detail share_resume_clients sms "
        "sms_marketing third_party_survey"
    ),
    "skills, experience, languages, referrals and free-form answers come from the user's answer bank, "
    "not from a rule": (
        "actuarial_exams assessment_lang assessment_lang_text assessment_type bilingual bilingual_text "
        "career_fair conferences current_company employed_before_text english_level followup "
        "github_proud_project hear_choice hear_specify hear_text lang_fluent_box lang_level "
        "lang_other_than_english languages_choice licenses licenses_detail linux_install optional_detail "
        "preferred prior_internship prior_internships references referral referral_text skill_exp "
        "tech_proficiency years_exp years_exp_choice"
    ),
}
GAPS = {key: reason for reason, keys in GAP_GROUPS.items() for key in keys.split()}
# Known gaps by (ats, Workday field id, label): the repo matches a different key than the original.
LABEL_GAPS = {
    ("wd", "name--legalName--firstName", "First Name"): (
        "the original fills Workday's legal name outside its rule table (k=null); "
        "this repo maps the fixed field id to the confirmed first name"
    ),
    ("wd", "name--preferredName--firstName", "First Name"): (
        "the original leaves the preferred-name block to its name step (k=null); this repo's exact "
        "'first name' label puts the legal first name there (follow-up: map the field id to preferred_name)"
    ),
}
# (ats, label) pairs that pick the same key as the original today; a drop means a matcher regressed.
AGREE_FLOOR = 456


def profile():
    """A synthetic candidate whose every fact's value is its own name."""
    return {
        "schema_version": 1,
        "revision": 1,
        "contact": {name: fact("contact." + name) for name in CONTACT},
        "compensation": {"eeo": fact({name: "eeo." + name for name in EEO})},
    }


def field(row, index):
    return {
        "id": row.get("fid") or f"question_{index}",
        "label": row["q"],
        "type": KIND_TYPES[row["kind"]],
        "required": True,
        "options": [],
        "value": "",
    }


def picked(ats, row, index, candidate):
    """The original rule key this repo's planner matches for the row's label, or None."""
    item = field(row, index)
    if ats == "rp":
        supplied = rippling_driver.plan(candidate, [item], JOB)["answers"].get(item["id"], {})
        value = supplied.get("value") if isinstance(supplied, dict) else None
    else:
        driver = WorkdayDriver() if ats == "wd" else GreenhouseDriver()
        plan = driver.plan([item], candidate, JOB)
        if plan["resume"] == item["id"]:
            value = "resume"
        else:
            value = plan["answers"].get(item["id"])
            for entry in plan["unknown"]:
                if entry["id"] == item["id"] and "value" in entry:
                    value = entry["value"]
    if value is None:
        return None
    return RENAMED.get(value, "unmapped:" + str(value))


def expected(row, ats):
    by_ats = row.get("kByAts") or {}
    return by_ats[ats] if ats in by_ats else row["k"]


def compare():
    """Every (ats, label) the repo answers unlike the original, and the allowlist entries used."""
    candidate = profile()
    mismatches, used_gaps, used_labels, agree = [], set(), set(), 0
    for index, row in enumerate(CORPUS):
        for ats in row.get("ats") or ATS:
            want, got = expected(row, ats), picked(ats, row, index, candidate)
            if want == got:
                agree += 1
                continue
            if got is None and want in GAPS:
                used_gaps.add(want)
                continue
            label_key = (ats, row.get("fid"), row["q"])
            if label_key in LABEL_GAPS:
                used_labels.add(label_key)
                continue
            mismatches.append(f"[{ats}] want={want} got={got} q={row['q'][:90]!r}")
    return mismatches, used_gaps, used_labels, agree


class PortCasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mismatches, cls.used_gaps, cls.used_labels, cls.agree = compare()

    def test_corpus_holds_only_labels_kinds_and_keys(self):
        self.assertGreater(len(CORPUS), 1000)
        allowed = {"q", "kind", "k", "kByAts", "fid", "ats"}
        for row in CORPUS:
            self.assertLessEqual(set(row), allowed, row)
            self.assertIn(row["kind"], KIND_TYPES)
            self.assertIn("k", row)

    def test_every_label_matches_the_original_rule_key_or_a_listed_gap(self):
        self.assertEqual(
            self.mismatches,
            [],
            f"{len(self.mismatches)} labels pick a different rule than the original:\n" + "\n".join(self.mismatches),
        )

    def test_labels_that_agree_with_the_original_do_not_drop(self):
        self.assertGreaterEqual(self.agree, AGREE_FLOOR)
        self.assertGreater(AGREE_FLOOR, 0)

    def test_every_allowlisted_gap_is_still_a_gap(self):
        self.assertEqual(sorted(set(GAPS) - self.used_gaps), [])
        self.assertEqual(sorted(set(LABEL_GAPS) - self.used_labels), [])
        self.assertEqual(sum(len(keys.split()) for keys in GAP_GROUPS.values()), len(GAPS), "a key sits in two groups")
        for reason in [*GAPS.values(), *LABEL_GAPS.values()]:
            self.assertTrue(reason.strip())

    def test_eligible_without_sponsorship_never_matches_a_sponsorship_needed_rule(self):
        rows = [(index, row) for index, row in enumerate(CORPUS) if row["q"] == SEL]
        self.assertTrue(rows, "the SEL case is missing from the corpus")
        candidate = profile()
        for index, row in rows:
            for ats in row.get("ats") or ATS:
                self.assertNotIn(expected(row, ats), SPONSORSHIP_NEEDED)
                self.assertNotIn(picked(ats, row, index, candidate), SPONSORSHIP_NEEDED)
                self.assertIsNone(picked(ats, row, index, candidate))

    def test_rename_table_maps_every_fact_the_planner_can_pick(self):
        # A planner that picks a fact outside the table shows up as "unmapped:<fact>".
        candidate = profile()
        for index, row in enumerate(CORPUS):
            for ats in row.get("ats") or ATS:
                got = picked(ats, row, index, candidate)
                self.assertFalse(str(got).startswith("unmapped:"), (ats, row["q"], got))


if __name__ == "__main__":
    unittest.main()
