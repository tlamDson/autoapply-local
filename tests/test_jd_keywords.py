"""JD keyword extraction and resume coverage: pure functions, no LLM."""

import unittest

from test_support import ReleaseCase


class KeywordTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.kw = self.module("jd_keywords")

    def test_known_terms_are_found_case_insensitively_and_sorted(self):
        found = self.kw.keywords("We use PYTHON, postgresql and Docker/Kubernetes.")
        self.assertEqual(found, ["docker", "kubernetes", "postgresql", "python"])

    def test_symbol_terms_and_multiword_terms(self):
        found = self.kw.keywords("C++ and C# plus .NET, Node.js, machine learning, CI/CD.")
        for term in ("c++", "c#", ".net", "node.js", "machine learning", "ci/cd"):
            self.assertIn(term, found)

    def test_terms_match_whole_tokens_only(self):
        self.assertEqual(self.kw.keywords("Javascript developers; rusty skills; a gopher; cobalt"), ["javascript"])

    def test_single_letter_languages_are_not_keywords(self):
        self.assertNotIn("r", self.kw.keywords("R&D team, r-and-d"))
        self.assertNotIn("c", self.kw.keywords("Plan A/B, vitamin C"))

    def test_coverage_is_the_share_of_job_keywords_found_in_the_resume(self):
        jd = "Python, Docker, Kubernetes and AWS"
        self.assertEqual(self.kw.coverage(jd, "Python Docker"), 0.5)
        self.assertEqual(self.kw.coverage(jd, "python docker kubernetes aws"), 1.0)
        self.assertEqual(self.kw.coverage(jd, "Cooking"), 0.0)

    def test_coverage_is_none_when_the_job_names_no_known_keyword(self):
        self.assertIsNone(self.kw.coverage("Join our friendly team.", "Python"))
        self.assertIsNone(self.kw.coverage("", "Python"))

    def test_resume_keywords_outside_the_job_do_not_raise_the_score(self):
        self.assertEqual(self.kw.coverage("Python", "Python Go Rust Java SQL"), 1.0)
        self.assertEqual(self.kw.coverage("Python Java", "Python Go Rust"), 0.5)
