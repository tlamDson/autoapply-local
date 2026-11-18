"""Fetch with the filters derived in setup, posting dates and the first-fetch report (plan aa-09).

Board responses are recorded shapes in tests/board_payloads.json for invented
employers; the transport is a mock, so no request leaves the machine.
"""

import copy
import json
from datetime import date
from unittest.mock import Mock, patch
from urllib.error import URLError

from test_support import RELEASE, ReleaseCase, fact

TODAY = date(2026, 11, 18)
PAYLOADS = json.loads((RELEASE / "tests" / "board_payloads.json").read_text(encoding="utf-8"))
CORP = {"ats": "greenhouse", "board": "examplecorp", "company": "Example Corp", "group": "sp500"}
CLOUD = {
    "ats": "workday",
    "host": "examplecloud.wd5.myworkdayjobs.com",
    "tenant": "examplecloud",
    "site": "Careers",
    "company": "Example Cloud",
    "group": "unicorn",
}
BROKEN = {"ats": "greenhouse", "board": "brokenco", "company": "Broken Co"}
OFF = {"ats": "greenhouse", "board": "offco", "company": "Off Co", "group": "extra"}


def profile(**preferences):
    return {
        "schema_version": 1,
        "revision": 1,
        "targets": {"roles": ["swe"], "job_types": ["internship"], "countries": ["US"], "season": "summer", "year": 2027},
        "logistics": {},
        "authorization": [],
        "preferences": {
            "posted_within_days": 7,
            "jd_exclude": ["no_sponsorship"],
            "title_include": ["intern"],
            "title_exclude": ["senior", "staff", "full-time"],
            "excluded_employers": [],
            **preferences,
        },
    }


def job(**changes):
    value = {
        "id": "greenhouse:examplecorp:1",
        "ats": "greenhouse",
        "company": "Example Corp",
        "role": "Software Engineer Intern, Summer 2027",
        "location": "New York, United States",
        "country": "US",
        "url": "https://job-boards.greenhouse.io/examplecorp/jobs/1",
        "description": "Build services.",
        "eligibility": "unknown",
        "season": "summer",
        "year": 2027,
        "level": "internship",
        "posted_at": "2026-11-17",
    }
    value.update(changes)
    return value


class PostingDateTests(ReleaseCase):
    def test_greenhouse_prefers_first_published_then_updated_at(self):
        posted_on = self.module("posting_dates").posted_on
        both = {"first_published": "2026-11-15T23:00:00-05:00", "updated_at": "2026-11-17T01:00:00Z"}
        self.assertEqual(posted_on("greenhouse", both, TODAY), "2026-11-15")
        self.assertEqual(posted_on("greenhouse", {"updated_at": "2026-11-17T01:00:00Z"}, TODAY), "2026-11-17")
        self.assertIsNone(posted_on("greenhouse", {"updated_at": "soon"}, TODAY))
        self.assertIsNone(posted_on("greenhouse", {}, TODAY))

    def test_workday_relative_posted_on_text(self):
        posted_on = self.module("posting_dates").posted_on
        cases = {
            "Posted Today": "2026-11-18",
            "Posted Yesterday": "2026-11-17",
            "Posted 3 Days Ago": "2026-11-15",
            "Posted 30+ Days Ago": "2026-10-19",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(posted_on("workday", {"postedOn": text}, TODAY), expected)
        for text in ("Posted recently", "", None):
            with self.subTest(text=text):
                self.assertIsNone(posted_on("workday", {"postedOn": text}, TODAY))

    def test_rippling_reads_an_iso_created_date_when_present(self):
        posted_on = self.module("posting_dates").posted_on
        self.assertEqual(posted_on("rippling", {"createdAt": "2026-11-10T12:00:00Z"}, TODAY), "2026-11-10")
        self.assertIsNone(posted_on("rippling", {"name": "Intern"}, TODAY))

    def test_fetcher_records_posted_at_from_recorded_board_json(self):
        fetch = self.module("fetch")
        transport = Mock(spec=["get_json", "post_json"])
        transport.get_json.return_value = copy.deepcopy(PAYLOADS["greenhouse"])
        transport.post_json.return_value = copy.deepcopy(PAYLOADS["workday"])
        fetcher = fetch.Fetcher(transport=transport, timeout=5, retries=0, today=TODAY)
        greenhouse = {item["id"]: item["posted_at"] for item in fetcher.fetch("greenhouse", CORP)}
        self.assertEqual(
            greenhouse,
            {
                "greenhouse:examplecorp:4001": "2026-11-16",
                "greenhouse:examplecorp:4002": "2026-11-17",
                "greenhouse:examplecorp:4003": "2026-11-18",
            },
        )
        workday = sorted(item["posted_at"] for item in fetcher.fetch("workday", CLOUD))
        self.assertEqual(workday, ["2026-10-19", "2026-11-16", "2026-11-18"])


class ScreenTests(ReleaseCase):
    def screen(self, item, candidate=None):
        return self.module("fetch_screen").screen(item, candidate or profile(), today=TODAY)

    def test_fitting_job_is_kept(self):
        verdict = self.screen(job())
        self.assertEqual(verdict["decision"], "keep")
        self.assertEqual(verdict["reasons"], [])

    def test_each_derived_filter_excludes_with_its_reason(self):
        cases = (
            ({"posted_at": "2026-11-01"}, ["posted"]),
            ({"description": "We do not sponsor visas."}, ["jd:no_sponsorship"]),
            ({"role": "Senior Software Engineer", "level": None}, ["title:senior", "title_type"]),
            ({"role": "Data Analyst Intern, Summer 2027"}, ["role"]),
        )
        for change, reasons in cases:
            with self.subTest(change=change):
                verdict = self.screen(job(**change))
                self.assertEqual(verdict["decision"], "exclude")
                self.assertEqual(verdict["reasons"], reasons)

    def test_unknown_posting_date_never_excludes(self):
        self.assertEqual(self.screen(job(posted_at=None))["decision"], "keep")

    def test_excluded_employer_reports_one_reason(self):
        verdict = self.screen(job(), profile(excluded_employers=["example corp"]))
        self.assertEqual(verdict["reasons"], ["excluded_employer"])

    def test_filters_read_confirmed_fact_records_and_ignore_unknown_ones(self):
        candidate = profile(posted_within_days=fact(3), jd_exclude=fact(None, status="unknown"))
        screen_module = self.module("fetch_screen")
        filters = screen_module.profile_filters(candidate)
        self.assertEqual(filters["posted_within_days"], 3)
        self.assertEqual(filters["jd_exclude"], [])
        self.assertEqual(filters["roles"], ["swe"])
        self.assertEqual((filters["season"], filters["year"]), ("summer", 2027))
        self.assertEqual(self.screen(job(posted_at="2026-11-14"), candidate)["reasons"], ["posted"])

    def test_profile_without_derived_filters_keeps_the_earlier_screen(self):
        candidate = profile()
        candidate["preferences"] = {}
        self.assertEqual(self.screen(job(posted_at="2025-01-01", role="Senior Software Engineer Intern"), candidate)
                         ["decision"], "keep")


class FirstFetchReportTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        network = patch("autoapply_local.fetch.urlopen", side_effect=AssertionError("No live HTTP"))
        network.start()
        self.addCleanup(network.stop)

    def run_fetch(self, language="en"):
        fetch = self.module("fetch")
        self.write_json(
            "config.json",
            {"runtime": {"language": language}, "resources": {"lanes": 1}, "boards": [CORP, CLOUD, BROKEN, OFF],
             "board_groups": {"extra": False}},
        )
        self.write_json("profile.json", profile())
        transport = Mock(spec=["get_json", "post_json"])

        def get_json(url, timeout):
            if "/brokenco/" in url:
                raise URLError("board unreachable")
            if "/offco/" in url:
                raise AssertionError("a board in a group that is off must not be fetched")
            return copy.deepcopy(PAYLOADS["greenhouse"])

        transport.get_json.side_effect = get_json
        transport.post_json.return_value = copy.deepcopy(PAYLOADS["workday"])
        real = fetch.Fetcher
        with patch.object(fetch, "Fetcher", side_effect=lambda **kw: real(transport=transport, retries=0, **kw)):
            return self.module("runtime").fetch(root=self.root, today=TODAY)

    def test_report_counts_passed_and_filtered_jobs_by_reason(self):
        result = self.run_fetch()
        report = result["report"]
        self.assertEqual(
            {key: report[key] for key in ("boards", "boards_failed", "fetched", "passed", "excluded")},
            {"boards": 3, "boards_failed": 1, "fetched": 6, "passed": 3, "excluded": 3},
        )
        self.assertEqual(report["reasons"], {"jd:no_sponsorship": 1, "posted": 1, "title:senior": 1, "title_type": 1})
        self.assertEqual(
            sorted(item["id"] for item in result["jobs"]),
            ["greenhouse:examplecorp:4001", "workday:examplecloud:R5001", "workday:examplecloud:R5003"],
        )
        self.assertEqual([item["board"]["board"] for item in result["unknown"]], ["brokenco"])
        summary = report["summary"]
        for text in ("6 jobs", "2 of 3 boards", "3 passed", "3 filtered out", "1 board could not be read"):
            with self.subTest(text=text):
                self.assertIn(text, summary)

    def test_report_follows_the_setup_language(self):
        summary = self.run_fetch("vi")["report"]["summary"]
        self.assertIn("3 job qua bộ lọc", summary)
        self.assertIn("6 job", summary)

    def test_fetched_jobs_are_queued_once(self):
        first = self.run_fetch()
        second = self.run_fetch()
        self.assertEqual(len(self.module("storage").Store(self.root).jobs()), 3)
        self.assertEqual(first["report"]["passed"], second["report"]["passed"])
