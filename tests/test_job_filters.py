"""Table tests: job filters derived from setup answers, and their offline evaluation."""

import copy
import unittest

from test_support import ReleaseCase, fact

ALL_JD = ["no_sponsorship", "us_citizen", "clearance", "itar"]
SENIOR = ["senior", "sr.", "staff", "principal", "lead", "manager", "director"]
FULL_TIME = ["full-time", "new grad"]


class DerivationTests(ReleaseCase):
    def groups(self, **changes):
        groups = copy.deepcopy(self.module("intake").blank_intake()["groups"])
        for dotted, value in changes.items():
            group, name = dotted.split("__")
            groups[group][name] = value
        return groups

    def derive(self, **changes):
        return self.module("job_filters").derive(self.groups(**changes))

    def test_sponsorship_need_drops_restricted_jobs_only_when_answered_yes(self):
        record = {"country": "US", "sponsorship_now": False, "sponsorship_future": True}
        table = [
            ("now yes", {"authorization__sponsorship_now": True}, ALL_JD),
            ("future yes", {"authorization__sponsorship_future": True}, ALL_JD),
            ("fact-wrapped yes", {"authorization__sponsorship_future": fact(True)}, ALL_JD),
            ("record yes", {"authorization__records": [record]}, ALL_JD),
            ("both no", {"authorization__sponsorship_now": False, "authorization__sponsorship_future": False}, []),
            ("unknown never inferred", {}, []),
            ("unknown fact", {"authorization__sponsorship_now": fact(None, status="unknown")}, []),
        ]
        for name, changes, expected in table:
            with self.subTest(name):
                self.assertEqual(self.derive(**changes)["jd_exclude"], expected)

    def test_season_and_year_come_from_answers(self):
        table = [
            ("summer 2027", {"logistics__season": "summer", "logistics__year": 2027}, ("summer", 2027)),
            ("fact-wrapped", {"logistics__season": fact("fall"), "logistics__year": fact(2026)}, ("fall", 2026)),
            ("unanswered", {}, (None, None)),
        ]
        for name, changes, (season, year) in table:
            with self.subTest(name):
                derived = self.derive(**changes)
                self.assertEqual((derived["season"], derived["year"]), (season, year))

    def test_job_types_drive_title_include_and_exclude(self):
        table = [
            ("internship only", ["internship"], ["intern"], SENIOR + FULL_TIME),
            ("internship and co-op", ["internship", "co-op"], ["intern", "co-op"], SENIOR + FULL_TIME),
            ("co-op only", ["co-op"], ["co-op"], SENIOR + FULL_TIME),
            ("intern and new grad", ["internship", "newgrad"], [], SENIOR),
            ("new grad only", ["newgrad"], [], SENIOR),
            ("experienced", ["experienced"], [], []),
            ("unanswered", [], [], []),
        ]
        for name, job_types, include, exclude in table:
            with self.subTest(name):
                derived = self.derive(targets__job_types=job_types)
                self.assertEqual(derived["title_include"], include)
                self.assertEqual(derived["title_exclude"], exclude)

    def test_roles_and_excluded_roles(self):
        table = [
            ("roles", {"targets__roles": ["SWE", "ML"]}, ["SWE", "ML"], []),
            ("roles plus includes, no duplicates", {"targets__roles": ["SWE"], "targets__include_roles": ["data", "swe"]},
             ["SWE", "data"], []),
            ("excluded roles join the title exclusions", {"targets__exclude_roles": ["Mobile"]}, [], ["mobile"]),
            ("unanswered", {}, [], []),
        ]
        for name, changes, roles, exclude in table:
            with self.subTest(name):
                derived = self.derive(**changes)
                self.assertEqual(derived["roles"], roles)
                self.assertEqual(derived["title_exclude"], exclude)

    def test_entry_seniority_drops_senior_titles(self):
        self.assertEqual(self.derive(targets__seniority="entry")["title_exclude"], SENIOR)

    def test_posted_within_days(self):
        table = [
            ("default seven", {}, 7),
            ("user edit kept", {"filters__posted_within_days": 3}, 3),
        ]
        for name, changes, days in table:
            with self.subTest(name):
                self.assertEqual(self.derive(**changes)["posted_within_days"], days)

    def test_excluded_companies_merge_filters_and_history(self):
        table = [
            ("none", {}, []),
            ("filters", {"filters__excluded_employers": ["Example Corp"]}, ["Example Corp"]),
            ("history joins, case-insensitive dedup",
             {"filters__excluded_employers": ["Example Corp"], "history__avoid_employers": ["example corp", "Sample Inc"]},
             ["Example Corp", "Sample Inc"]),
        ]
        for name, changes, expected in table:
            with self.subTest(name):
                self.assertEqual(self.derive(**changes)["excluded_employers"], expected)

    def test_intake_values_are_the_editable_filter_fields(self):
        derived = self.derive(authorization__sponsorship_now=True, targets__job_types=["internship"])
        values = self.module("job_filters").intake_values(derived)
        self.assertEqual(set(values), {"filters.posted_within_days", "filters.jd_exclude", "filters.title_include",
                                       "filters.title_exclude", "filters.excluded_employers"})
        self.assertEqual(values["filters.jd_exclude"], ALL_JD)
        self.assertEqual(values["filters.title_include"], ["intern"])

    def test_summary_names_every_filter(self):
        derived = self.derive(authorization__sponsorship_now=True, targets__job_types=["internship"],
                              targets__roles=["SWE"], logistics__season="summer", logistics__year=2027,
                              filters__excluded_employers=["Example Corp"])
        text = self.module("job_filters").summary(derived, "en")
        for needle in ("7 days", "ITAR", "security clearance", "intern", "senior", "Summer 2027", "SWE", "Example Corp"):
            self.assertIn(needle, text)
        self.assertIn("ITAR", self.module("job_filters").summary(derived, "vi"))


class EvaluationTests(ReleaseCase):
    FILTERS = {
        "posted_within_days": 7, "jd_exclude": ALL_JD, "title_include": ["intern"],
        "title_exclude": SENIOR + FULL_TIME, "excluded_employers": ["Example Corp"],
        "roles": ["SWE"], "season": "summer", "year": 2027,
    }

    def evaluate(self, today="2027-01-10", **job):
        base = {"title": "Software Engineer Intern, Summer 2027", "company": "Sample Labs",
                "description": "Build services.", "posted_at": "2027-01-08"}
        base.update(job)
        return self.module("job_filters").evaluate(base, self.FILTERS, today=today)

    def test_table(self):
        table = [
            ("keeps a matching job", {}, []),
            ("ITAR", {"description": "This role requires ITAR compliance."}, ["jd:itar"]),
            ("U.S. person", {"description": "Applicants must be a U.S. person."}, ["jd:itar"]),
            ("'curious person' is not ITAR", {"description": "We want a curious person."}, []),
            ("citizen", {"description": "Must be a U.S. citizen."}, ["jd:us_citizen"]),
            ("neutral citizenship line", {"description": "U.S. citizenship required: No"}, []),
            ("clearance", {"description": "Candidates must obtain an active security clearance."}, ["jd:clearance"]),
            ("no sponsorship", {"description": "We will not provide visa sponsorship."}, ["jd:no_sponsorship"]),
            ("do not sponsor", {"description": "We do not sponsor visas."}, ["jd:no_sponsorship"]),
            ("senior title", {"title": "Senior Software Engineer Intern"}, ["title:senior"]),
            ("full-time title", {"title": "Software Engineer Intern (Full-time)"}, ["title:full-time"]),
            ("no intern in title", {"title": "Software Engineer"}, ["title_type"]),
            ("internship counts as intern", {"title": "Software Engineering Internship"}, []),
            ("'internal' is not intern", {"title": "Internal Tools Software Engineer"}, ["title_type"]),
            ("other role", {"title": "Marketing Intern"}, ["role"]),
            ("other season", {"title": "Software Engineer Intern, Fall 2027"}, ["season"]),
            ("other year", {"title": "Software Engineer Intern, Summer 2026"}, ["year"]),
            ("too old", {"posted_at": "2026-12-20"}, ["posted"]),
            ("unknown date kept", {"posted_at": None}, []),
            ("excluded company", {"company": "example corp"}, ["excluded_company"]),
        ]
        for name, job, reasons in table:
            with self.subTest(name):
                result = self.evaluate(**job)
                self.assertEqual(result["reasons"], reasons)
                self.assertEqual(result["keep"], not reasons)

    def test_empty_filters_keep_everything(self):
        empty = {"posted_within_days": None, "jd_exclude": [], "title_include": [], "title_exclude": [],
                 "excluded_employers": [], "roles": [], "season": None, "year": None}
        job = {"title": "Senior Staff Engineer", "company": "Example Corp", "description": "ITAR. No sponsorship.",
               "posted_at": "2020-01-01"}
        self.assertEqual(self.module("job_filters").evaluate(job, empty, today="2027-01-10"),
                         {"keep": True, "reasons": []})


if __name__ == "__main__":
    unittest.main()
