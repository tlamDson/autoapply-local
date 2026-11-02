"""Actual synthetic multi-page CXS pagination, dedup and bounded loops.

Giả định của người thực thi: keep the existing 20-record Workday page size and
2,000-record hard cap; duplicate identities do not count as unseen postings.
No live HTTP request is allowed: all calls use an injected transport mock.
"""

from unittest.mock import Mock

from test_support import ReleaseCase


BOARD = {"tenant": "synthetic", "host": "synthetic.example.invalid", "site": "Careers", "company": "Example Labs"}


def posting(number):
    return {
        "title": "Backend Software Engineering Intern Summer 2030",
        "externalPath": f"/job/Remote/Backend-Intern_R{number}",
        "locationsText": "Remote, US",
        "bulletFields": [f"R{number}"],
    }


class FetchPaginationTests(ReleaseCase):
    def fetcher(self):
        self.transport = Mock(spec=["get_json", "post_json"])
        return self.module("fetch").Fetcher(transport=self.transport, timeout=5, retries=0)

    def test_workday_reads_second_page_with_explicit_offset_and_deduplicates_identity(self):
        fetcher = self.fetcher()
        pages = [
            {"total": 22, "jobPostings": [posting(index) for index in range(20)]},
            {"total": 22, "jobPostings": [posting(19), posting(20), posting(21)]},
        ]
        self.transport.post_json.side_effect = pages
        jobs = fetcher.fetch("workday", BOARD)
        self.assertEqual({item["id"] for item in jobs}, {f"workday:synthetic:R{index}" for index in range(22)})
        self.assertEqual(len(jobs), 22)
        calls = self.transport.post_json.call_args_list
        self.assertEqual([call.kwargs["data"]["offset"] for call in calls], [0, 20])
        self.assertTrue(all(call.kwargs["data"]["limit"] == 20 for call in calls))
        self.assertTrue(all(call.kwargs["timeout"] == 5 for call in calls))
        self.transport.get_json.assert_not_called()

    def test_workday_empty_page_terminates_even_when_total_is_stale(self):
        fetcher = self.fetcher()
        self.transport.post_json.side_effect = [
            {"total": 500, "jobPostings": [posting(index) for index in range(20)]},
            {"total": 500, "jobPostings": []},
        ]
        self.assertEqual(len(fetcher.fetch("workday", BOARD)), 20)
        self.assertEqual(self.transport.post_json.call_count, 2)

    def test_workday_repeated_nonempty_page_is_reported_instead_of_silent_truncation(self):
        fetcher = self.fetcher()
        # Returning the first page for every offset must not make total=40
        # look satisfied merely because 40 array elements were accumulated.
        self.transport.post_json.return_value = {"total": 40, "jobPostings": [posting(index) for index in range(20)]}
        with self.assertRaises(ValueError):
            fetcher.fetch("workday", BOARD)
        self.assertLessEqual(self.transport.post_json.call_count, 100)

    def test_workday_unbounded_total_hits_cap_with_no_request_past_offset_1980(self):
        fetcher = self.fetcher()

        def page(url, *, timeout, data):
            return {
                "total": 5000,
                "jobPostings": [posting(index) for index in range(data["offset"], data["offset"] + 20)],
            }

        self.transport.post_json.side_effect = page
        with self.assertRaises(ValueError):
            fetcher.fetch("workday", BOARD)
        offsets = [call.kwargs["data"]["offset"] for call in self.transport.post_json.call_args_list]
        self.assertEqual(offsets, list(range(0, 2000, 20)))
