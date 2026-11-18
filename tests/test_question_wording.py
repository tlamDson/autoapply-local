"""Catalog prompts read as correct questions; fetch --url trims pasted URLs."""

import unittest
from unittest import mock

from test_support import ReleaseCase


class QuestionWordingTests(ReleaseCase):
    def prompts(self):
        catalog = self.module("guided_catalog")
        return {q["id"]: q["prompt"] for q in catalog.walk()}

    def test_no_english_prompt_has_broken_your_phrasing(self):
        for qid, prompt in self.prompts().items():
            text = prompt["en"].casefold()
            for broken in ("your your", "your whether", "is your one", "your one or more"):
                self.assertNotIn(broken, text, qid)
            self.assertTrue(prompt["en"].endswith("?"), qid)

    def test_whether_labels_become_yes_no_questions(self):
        prompts = self.prompts()
        self.assertEqual(prompts["authorization.records.authorized_now"]["en"],
                         "For work authorization, can you tell us whether you are authorized to work now in this scope?")
        self.assertEqual(prompts["contact.portfolio"]["en"],
                         "For contact details, what is your portfolio URL?")
        self.assertEqual(prompts["targets.roles"]["en"],
                         "For job preferences, what are your target roles (one or more)?")

    def test_no_vietnamese_prompt_repeats_ban(self):
        for qid, prompt in self.prompts().items():
            text = prompt["vi"]
            self.assertNotIn("của bạn của bạn", text, qid)
            self.assertNotIn("cho việc", text, qid)
            self.assertNotIn("bạn chọn gì cho", text, qid)
            self.assertTrue(text.endswith("?"), qid)
        prompts = self.prompts()
        self.assertEqual(prompts["contact.portfolio"]["vi"],
                         "Về thông tin liên hệ, đường dẫn portfolio của bạn là gì?")
        self.assertEqual(prompts["education.transcript_available"]["vi"],
                         "Về học tập, bạn trả lời thế nào về việc có bảng điểm?")


class FetchUrlTrimTests(ReleaseCase):
    def test_import_url_strips_whitespace_and_crlf(self):
        fetch = self.module("fetch")
        transport = mock.Mock(spec=["get_json", "post_json"])
        result = fetch.Fetcher(transport=transport).import_url("  https://careers.example.invalid/custom/123\r\n")
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["url"], "https://careers.example.invalid/custom/123")
        transport.get_json.assert_not_called()

    def test_trimmed_greenhouse_url_is_recognised(self):
        fetch = self.module("fetch")
        transport = mock.Mock(spec=["get_json", "post_json"])
        transport.get_json.return_value = {"jobs": []}
        result = fetch.Fetcher(transport=transport).import_url("\thttps://job-boards.greenhouse.io/synthetic/jobs/123\r\n")
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["url"], "https://job-boards.greenhouse.io/synthetic/jobs/123")


if __name__ == "__main__":
    unittest.main()
