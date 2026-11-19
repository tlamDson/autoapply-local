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
GAP_GROUPS = {}
GAPS = {key: reason for reason, keys in GAP_GROUPS.items() for key in keys.split()}
# Known gaps by (ats, Workday field id, label): the repo matches a different key than the original.
LABEL_GAPS = {}
# (ats, label) pairs that pick the same key as the original today; a drop means a matcher regressed.
AGREE_FLOOR = 0


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
