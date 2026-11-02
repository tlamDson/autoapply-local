"""All fetch payloads are synthetic and all transports are injected mocks.

Giả định của người thực thi: Fetcher(transport,timeout,retries).fetch(ats,board)
returns jobs; transport.get_json(url,timeout=...) is the sole HTTP boundary.
evaluate_job(job,profile) returns decision/unknowns; import_url reports status.
"""

from unittest.mock import Mock

from test_support import ReleaseCase, candidate, fact, job


class FetchTests(ReleaseCase):
    def fetcher(self, payload):
        fetch = self.module("fetch")
        self.transport = Mock(spec=["get_json", "post_json"])
        self.transport.get_json.return_value = payload
        self.transport.post_json.return_value = payload
        return fetch.Fetcher(transport=self.transport, timeout=5, retries=2)

    def test_greenhouse_payload_normalizes_identity_title_location_and_unknown_eligibility(self):
        fetcher = self.fetcher(
            {
                "jobs": [
                    {
                        "id": 123,
                        "title": "Backend Software Engineering Intern",
                        "absolute_url": "https://jobs.example.invalid/123",
                        "location": {"name": "Remote, US"},
                        "content": "Build Python services.",
                        "updated_at": "2030-01-01T00:00:00Z",
                    }
                ]
            }
        )
        jobs = fetcher.fetch("greenhouse", {"board": "synthetic", "company": "Example Labs"})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["ats"], "greenhouse")
        self.assertEqual(jobs[0]["role"], "Backend Software Engineering Intern")
        self.assertEqual(jobs[0]["location"], "Remote, US")
        self.assertEqual(jobs[0]["eligibility"], "unknown")
        self.assertTrue(jobs[0]["id"])

    def test_workday_cxs_payload_and_pagination_use_bounded_transport(self):
        payload = {
            "total": 1,
            "jobPostings": [
                {
                    "title": "Software Engineering Intern",
                    "externalPath": "/job/Remote/Software-Engineering-Intern_R123",
                    "locationsText": "Remote, US",
                    "bulletFields": ["R123"],
                    "postedOn": "Posted Today",
                }
            ],
        }
        fetcher = self.fetcher(payload)
        jobs = fetcher.fetch(
            "workday",
            {"tenant": "synthetic", "host": "synthetic.example.invalid", "site": "Careers", "company": "Example Labs"},
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["ats"], "workday")
        self.assertTrue(jobs[0]["url"].endswith("Software-Engineering-Intern_R123"))
        calls = self.transport.get_json.call_args_list + self.transport.post_json.call_args_list
        self.assertTrue(calls)
        self.assertTrue(all(call.kwargs["timeout"] == 5 for call in calls))

    def test_rippling_payload_normalizes_public_posting(self):
        fetcher = self.fetcher(
            {
                "jobs": [
                    {
                        "id": "synthetic-r1",
                        "name": "Software Engineering Intern",
                        "url": "https://ats.example.invalid/synthetic/jobs/synthetic-r1",
                        "location": "New York, US",
                        "description": "Build Python services.",
                    }
                ]
            }
        )
        jobs = fetcher.fetch("rippling", {"board": "synthetic", "company": "Example Labs"})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["ats"], "rippling")
        self.assertEqual(jobs[0]["role"], "Software Engineering Intern")

    def test_timeout_retries_are_bounded_and_report_unknown_instead_of_dropping_board(self):
        fetcher = self.fetcher({})
        self.transport.get_json.side_effect = TimeoutError("synthetic timeout")
        self.transport.post_json.side_effect = TimeoutError("synthetic timeout")
        with self.assertRaises(TimeoutError):
            fetcher.fetch("greenhouse", {"board": "synthetic", "company": "Example Labs"})
        calls = self.transport.get_json.call_args_list + self.transport.post_json.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call.kwargs["timeout"] == 5 for call in calls))

    def test_jd_silence_is_unknown_and_config_can_require_explicit_suitability(self):
        fetch = self.module("fetch")
        profile = candidate()
        broad = fetch.evaluate_job(job(), profile)
        self.assertEqual(broad["decision"], "keep")
        self.assertIn("eligibility", broad["unknowns"])
        profile["preferences"]["eligibility"] = "explicit_only"
        strict = fetch.evaluate_job(job(), profile)
        self.assertEqual(strict["decision"], "exclude")
        self.assertEqual(job()["eligibility"], "unknown")

    def test_confirmed_target_filters_country_role_level_and_window(self):
        fetch = self.module("fetch")
        for change in (
            {"country": "CA"},
            {"role": "Sales Intern"},
            {"level": "senior"},
            {"season": "fall"},
            {"year": 2032},
        ):
            with self.subTest(change=change):
                self.assertEqual(fetch.evaluate_job(job(**change), candidate())["decision"], "exclude")

    def test_no_sponsor_conflict_follows_preference_without_rewriting_facts(self):
        fetch = self.module("fetch")
        profile = candidate()
        profile["authorization"][0]["sponsorship_future"] = fact(True)
        restricted = job(description="No sponsorship now or in the future.", sponsorship="not_supported")
        self.assertEqual(fetch.evaluate_job(restricted, profile)["decision"], "review")
        profile["preferences"]["no_sponsor_conflict"] = "exclude"
        self.assertEqual(fetch.evaluate_job(restricted, profile)["decision"], "exclude")
        self.assertIs(profile["authorization"][0]["sponsorship_future"]["value"], True)

    def test_big_tech_is_a_user_choice_without_hidden_company_exclusions(self):
        fetch = self.module("fetch")
        profile = candidate()
        item = job(company="Example Large Technology", employer_group="big_tech")
        self.assertEqual(fetch.evaluate_job(item, profile)["decision"], "keep")
        profile["preferences"]["include_big_tech"] = False
        self.assertEqual(fetch.evaluate_job(item, profile)["decision"], "exclude")

    def test_explicit_excluded_employer_is_filtered(self):
        profile = candidate()
        profile["preferences"]["excluded_employers"] = ["Example Labs"]
        self.assertEqual(self.module("fetch").evaluate_job(job(), profile)["decision"], "exclude")

    def test_unsupported_manual_url_is_reported_without_network_guessing(self):
        fetcher = self.fetcher({})
        result = fetcher.import_url("https://careers.example.invalid/custom/123")
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["url"], "https://careers.example.invalid/custom/123")
        self.transport.get_json.assert_not_called()
        self.transport.post_json.assert_not_called()

    def test_closed_manual_posting_is_reported_not_silently_lost(self):
        fetcher = self.fetcher({"jobs": []})
        result = fetcher.import_url("https://job-boards.greenhouse.io/synthetic/jobs/123")
        self.assertEqual(result["status"], "closed")

    def test_pagination_deduplicates_repeated_postings(self):
        posting = {
            "id": 123,
            "title": "Software Engineering Intern",
            "absolute_url": "https://jobs.example.invalid/123",
            "location": {"name": "US"},
        }
        fetcher = self.fetcher({"jobs": [posting, posting]})
        jobs = fetcher.fetch("greenhouse", {"board": "synthetic", "company": "Example Labs"})
        self.assertEqual(len(jobs), 1)
