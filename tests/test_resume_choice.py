"""Resume choice by role tag, JD keyword score tie-break, and the remembered no-match answer.

Offline: synthetic PDFs in temporary folders, resume text injected, no tool or network call.
"""

import io
import json
from pathlib import Path
from unittest.mock import Mock, patch

from test_support import ReleaseCase, candidate, job


class Base(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.rc = self.module("resume_choice")
        self.text = {}
        self.paths = {}
        for name in ("main", "backend", "ai", "devops"):
            pdf = self.root / f"{name}.pdf"
            pdf.write_bytes(b"%PDF-1.4\n% synthetic\n%%EOF")
            self.paths[name] = pdf.resolve()

    def text_of(self, path):
        return self.text.get(Path(path).stem, "")

    def config(self, **resumes):
        base = {"main_pdf": str(self.paths["main"]), "alternatives": [], "role_mapping": {}}
        base.update(resumes)
        return {"resumes": base}

    def alt(self, name, *tags):
        return {"id": name, "path": str(self.paths[name]), "tags": list(tags)}

    def memory(self):
        return self.rc.Memory(self.root / "user/resume-choices.json")

    def choose(self, config, role, description="", **kwargs):
        job = {"role": role, "description": description}
        kwargs.setdefault("text_of", self.text_of)
        return self.rc.choose(config, job, **kwargs)


class TagMatch(Base):
    def test_single_tag_match_picks_that_resume(self):
        config = self.config(alternatives=[self.alt("backend", "backend"), self.alt("ai", "ml", "ai")])
        got = self.choose(config, "Backend Software Engineering Intern")
        self.assertEqual(got.path, self.paths["backend"])
        self.assertEqual(got.reason, "role_tag")
        self.assertEqual(got.tag, "backend")

    def test_main_pdf_tags_take_part_in_the_match(self):
        config = self.config(main_tags=["swe", "software"], alternatives=[self.alt("ai", "ai")])
        got = self.choose(config, "Software Engineer Intern")
        self.assertEqual(got.path, self.paths["main"])

    def test_tag_matches_whole_words_only(self):
        config = self.config(alternatives=[self.alt("ai", "ai", "ml"), self.alt("backend", "backend")])
        self.assertEqual(self.choose(config, "Backend Intern").path, self.paths["backend"])
        for role in ("Email Platform Intern", "HTML Engineer Intern"):
            with self.subTest(role=role), self.assertRaises(self.rc.ResumeChoiceNeeded):
                self.choose(config, role)

    def test_match_ignores_case_hyphen_and_punctuation(self):
        config = self.config(alternatives=[self.alt("devops", "dev-ops", "site reliability"), self.alt("ai", "ai")])
        self.assertEqual(self.choose(config, "SITE   RELIABILITY Engineer, Intern").path, self.paths["devops"])
        self.assertEqual(self.choose(config, "Dev Ops Intern").path, self.paths["devops"])

    def test_legacy_role_mapping_token_counts_as_a_tag_of_the_mapped_resume(self):
        config = self.config(
            alternatives=[{"id": "backend", "path": str(self.paths["backend"])}],
            role_mapping={"backend": "backend", "platform": str(self.paths["devops"])},
        )
        self.assertEqual(self.choose(config, "Backend Intern").path, self.paths["backend"])
        self.assertEqual(self.choose(config, "Platform Intern").path, self.paths["devops"])

    def test_alternative_may_be_a_plain_path_without_tags(self):
        config = self.config(alternatives=[str(self.paths["backend"])], main_tags=["swe"])
        self.assertEqual(self.choose(config, "SWE Intern").path, self.paths["main"])


class RoleAliases(Base):
    """Common role abbreviations match their long form both ways; every other tag stays whole-word."""

    def test_swe_tag_matches_software_engineering_titles(self):
        config = self.config(main_tags=["SWE"], alternatives=[self.alt("ai", "ai")])
        for role in (
            "Software Engineering Intern-Information Systems (Summer 2027)",
            "Software Engineer Intern",
            "SWE Intern",
        ):
            with self.subTest(role=role):
                got = self.choose(config, role)
                self.assertEqual((got.path, got.reason, got.tag), (self.paths["main"], "role_tag", "SWE"))

    def test_each_abbreviation_matches_its_long_form(self):
        pairs = (
            ("ml", "Machine Learning Intern"),
            ("SDE", "Software Development Engineer Intern"),
            ("FE", "Front-End Developer Intern"),
            ("fe", "Frontend Engineer Intern"),
            ("BE", "Back End Engineer Intern"),
            ("be", "Backend Developer Intern"),
            ("AI", "Artificial Intelligence Research Intern"),
        )
        for tag, role in pairs:
            with self.subTest(tag=tag, role=role):
                config = self.config(alternatives=[self.alt("devops", "devops"), self.alt("ai", tag)])
                self.assertEqual(self.choose(config, role).path, self.paths["ai"])

    def test_long_form_tag_matches_the_abbreviation_in_the_title(self):
        pairs = (
            ("software engineering", "SWE Intern"),
            ("Software Engineer", "SWE II"),
            ("machine learning", "ML Engineer Intern"),
            ("software development engineer", "SDE Intern"),
            ("frontend", "FE Engineer Intern"),
            ("back-end", "BE Engineer Intern"),
            ("artificial intelligence", "AI/ML Intern"),
        )
        for tag, role in pairs:
            with self.subTest(tag=tag, role=role):
                config = self.config(alternatives=[self.alt("devops", "devops"), self.alt("ai", tag)])
                self.assertEqual(self.choose(config, role).path, self.paths["ai"])

    def test_abbreviation_reached_through_an_alias_must_be_uppercase_in_the_title(self):
        config = self.config(alternatives=[self.alt("backend", "backend"), self.alt("devops", "devops")])
        with self.assertRaises(self.rc.ResumeChoiceNeeded):
            self.choose(config, "Intern - may be remote")

    def test_aliases_stay_whole_word(self):
        config = self.config(alternatives=[self.alt("ai", "swe", "ml"), self.alt("devops", "devops")])
        for role in ("Software Engineeringly Intern", "Machine Learnings Intern", "Hardware Engineer Intern"):
            with self.subTest(role=role), self.assertRaises(self.rc.ResumeChoiceNeeded):
                self.choose(config, role)

    def test_alias_match_reports_the_users_own_tag(self):
        config = self.config(alternatives=[self.alt("ai", "ML"), self.alt("devops", "devops")])
        self.assertEqual(self.choose(config, "Machine Learning Intern").tag, "ML")


class NoTerminalFallback(Base):
    def setUp(self):
        super().setUp()
        self.cfg = self.config(alternatives=[self.alt("backend", "backend"), self.alt("ai", "ai")])

    def test_without_a_terminal_the_main_resume_is_used_with_a_reason(self):
        memory = self.memory()
        got = self.choose(self.cfg, "Quant Research Intern", memory=memory, fallback=True)
        self.assertEqual((got.path, got.reason), (self.paths["main"], "no_terminal"))
        self.assertFalse((self.root / "user/resume-choices.json").exists())

    def test_fallback_never_overrides_a_tag_match_or_a_remembered_answer(self):
        memory = self.memory()
        memory.set("Quant Research Intern", self.paths["ai"])
        self.assertEqual(self.choose(self.cfg, "Backend Intern", fallback=True).path, self.paths["backend"])
        got = self.choose(self.cfg, "Quant Research Intern", memory=memory, fallback=True)
        self.assertEqual((got.path, got.reason), (self.paths["ai"], "remembered"))

    def test_a_declined_question_still_stops_even_with_fallback(self):
        with self.assertRaises(self.rc.ResumeChoiceNeeded):
            self.choose(self.cfg, "Quant Research Intern", ask=lambda role, options: None, fallback=True)

    def test_described_fallback_says_why(self):
        runtime = self.module("runtime")
        line = runtime.describe_choice(self.choose(self.cfg, "Quant Research Intern", fallback=True))
        self.assertIn(str(self.paths["main"]), line)
        self.assertIn("main resume", line)
        self.assertIn("no terminal", line)


class KeywordTieBreak(Base):
    def setUp(self):
        super().setUp()
        self.config_two = self.config(
            main_tags=["software"], alternatives=[self.alt("backend", "software"), self.alt("ai", "ai")]
        )
        self.jd = "You will use Python, PostgreSQL, Docker and Kubernetes on AWS."

    def test_several_tag_matches_choose_the_highest_keyword_score(self):
        self.text["main"] = "Python, React"
        self.text["backend"] = "Python PostgreSQL Docker Kubernetes AWS"
        got = self.choose(self.config_two, "Software Engineer Intern", self.jd)
        self.assertEqual(got.path, self.paths["backend"])
        self.assertEqual(got.reason, "keyword_score")
        self.assertEqual(got.score, 1.0)

    def test_only_tagged_matches_compete_not_every_resume(self):
        self.text["ai"] = "Python PostgreSQL Docker Kubernetes AWS"
        self.text["main"] = "Python"
        self.text["backend"] = "Python Docker"
        got = self.choose(self.config_two, "Software Engineer Intern", self.jd)
        self.assertEqual(got.path, self.paths["backend"])

    def test_equal_scores_keep_configuration_order_with_main_first(self):
        self.text["main"] = self.text["backend"] = "Python Docker"
        got = self.choose(self.config_two, "Software Engineer Intern", self.jd)
        self.assertEqual(got.path, self.paths["main"])

    def test_a_job_description_without_known_keywords_keeps_configuration_order(self):
        self.text["backend"] = "Python Docker"
        got = self.choose(self.config_two, "Software Engineer Intern", "Join our friendly team.")
        self.assertEqual(got.path, self.paths["main"])
        self.assertEqual(got.reason, "role_tag")

    def test_unreadable_resume_scores_zero_instead_of_failing(self):
        def broken(path):
            if Path(path).stem == "main":
                raise ValueError("PDF extraction failed")
            return "Python Docker"

        got = self.choose(self.config_two, "Software Engineer Intern", self.jd, text_of=broken)
        self.assertEqual(got.path, self.paths["backend"])

    def test_default_text_reader_uses_the_pdf_extractor(self):
        self.assertEqual(self.rc.resume_text(self.paths["main"], extractor=lambda path: "Hi"), "Hi")
        with patch("autoapply_local.extraction.extract_source", side_effect=ValueError("x")):
            self.assertEqual(self.rc.resume_text(self.paths["main"]), "")
        with patch("autoapply_local.extraction.extract_source", return_value={"text": "Python"}):
            self.assertEqual(self.rc.resume_text(self.paths["main"]), "Python")


class NoMatch(Base):
    def setUp(self):
        super().setUp()
        self.cfg = self.config(alternatives=[self.alt("backend", "backend"), self.alt("ai", "ai")])

    def test_one_resume_only_needs_no_question(self):
        config = {"resumes": {"main_pdf": str(self.paths["main"])}}
        ask = Mock()
        got = self.choose(config, "Anything Intern", ask=ask)
        self.assertEqual(got.path, self.paths["main"])
        self.assertEqual(got.reason, "only_resume")
        ask.assert_not_called()

    def test_no_match_asks_once_remembers_and_does_not_ask_again(self):
        ask = Mock(return_value=str(self.paths["ai"]))
        memory = self.memory()
        first = self.choose(self.cfg, "Quant Research Intern", ask=ask, memory=memory)
        self.assertEqual((first.path, first.reason), (self.paths["ai"], "asked"))
        ask.assert_called_once()
        role, options = ask.call_args.args
        self.assertEqual(role, "Quant Research Intern")
        self.assertEqual([item["id"] for item in options], ["main", "backend", "ai"])
        again = self.choose(self.cfg, "  quant research INTERN ", ask=ask, memory=memory)
        self.assertEqual((again.path, again.reason), (self.paths["ai"], "remembered"))
        ask.assert_called_once()

    def test_answer_survives_a_new_process(self):
        self.choose(self.cfg, "Quant Research Intern", ask=lambda role, options: "backend", memory=self.memory())
        saved = json.loads((self.root / "user/resume-choices.json").read_text(encoding="utf-8"))
        self.assertEqual(list(saved["choices"].values()), [str(self.paths["backend"])])
        ask = Mock()
        got = self.choose(self.cfg, "Quant Research Intern", ask=ask, memory=self.memory())
        self.assertEqual((got.path, got.reason), (self.paths["backend"], "remembered"))
        ask.assert_not_called()

    def test_season_and_year_do_not_make_a_new_role(self):
        ask = Mock(return_value="ai")
        memory = self.memory()
        self.choose(self.cfg, "Quant Research Intern - Summer 2030", ask=ask, memory=memory)
        self.choose(self.cfg, "Quant Research Intern (Fall 2031)", ask=ask, memory=memory)
        ask.assert_called_once()

    def test_a_different_role_asks_again(self):
        ask = Mock(return_value="ai")
        memory = self.memory()
        self.choose(self.cfg, "Quant Research Intern", ask=ask, memory=memory)
        self.choose(self.cfg, "Hardware Design Intern", ask=ask, memory=memory)
        self.assertEqual(ask.call_count, 2)

    def test_tag_match_beats_a_remembered_answer(self):
        memory = self.memory()
        memory.set("Backend Intern", self.paths["ai"])
        got = self.choose(self.cfg, "Backend Intern", memory=memory)
        self.assertEqual((got.path, got.reason), (self.paths["backend"], "role_tag"))

    def test_remembered_resume_that_left_the_configuration_asks_again(self):
        memory = self.memory()
        memory.set("Quant Research Intern", self.root / "deleted.pdf")
        ask = Mock(return_value="main")
        got = self.choose(self.cfg, "Quant Research Intern", ask=ask, memory=memory)
        self.assertEqual((got.path, got.reason), (self.paths["main"], "asked"))
        ask.assert_called_once()

    def test_without_a_way_to_ask_the_choice_is_reported_not_guessed(self):
        with self.assertRaises(self.rc.ResumeChoiceNeeded) as caught:
            self.choose(self.cfg, "Quant Research Intern")
        self.assertIsInstance(caught.exception, ValueError)
        self.assertIn("Quant Research Intern", str(caught.exception))
        self.assertEqual([item["id"] for item in caught.exception.options], ["main", "backend", "ai"])

    def test_declined_question_is_not_remembered(self):
        memory = self.memory()
        with self.assertRaises(self.rc.ResumeChoiceNeeded):
            self.choose(self.cfg, "Quant Research Intern", ask=lambda role, options: None, memory=memory)
        self.assertFalse((self.root / "user/resume-choices.json").exists())

    def test_answer_outside_the_configured_resumes_is_rejected_and_not_remembered(self):
        memory = self.memory()
        with self.assertRaisesRegex(ValueError, "configured resumes"):
            self.choose(
                self.cfg, "Quant Research Intern", ask=lambda role, options: str(self.root / "x.pdf"), memory=memory
            )
        self.assertFalse((self.root / "user/resume-choices.json").exists())

    def test_ask_without_memory_still_answers_the_current_job(self):
        got = self.choose(self.cfg, "Quant Research Intern", ask=lambda role, options: "ai")
        self.assertEqual((got.path, got.reason), (self.paths["ai"], "asked"))

    def test_no_configured_resume_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "main PDF"):
            self.choose({"resumes": {}}, "Backend Intern")

    def test_corrupt_memory_file_is_ignored(self):
        path = self.root / "user/resume-choices.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        ask = Mock(return_value="ai")
        got = self.choose(self.cfg, "Quant Research Intern", ask=ask, memory=self.rc.Memory(path))
        self.assertEqual(got.path, self.paths["ai"])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 1)


class RuntimeIntegration(Base):
    def test_runtime_select_resume_uses_tags_and_still_returns_a_path(self):
        runtime = self.module("runtime")
        config = self.config(alternatives=[self.alt("backend", "backend")], main_tags=["swe"])
        self.assertEqual(runtime.select_resume({}, config, {"role": "Backend Intern"}), self.paths["backend"])

    def test_runtime_rejects_a_chosen_file_that_is_not_a_pdf(self):
        runtime = self.module("runtime")
        (self.root / "main.pdf").write_bytes(b"not a pdf")
        with self.assertRaisesRegex(ValueError, "existing PDF"):
            runtime.select_resume({}, self.config(), {"role": "Backend Intern"})

    def test_apply_preflight_names_the_reason_of_each_choice(self):
        runtime = self.module("runtime")
        config = self.config(alternatives=[self.alt("backend", "backend")], main_tags=["swe"])
        choice = runtime.choose_resume({}, config, {"role": "Backend Intern"})
        line = runtime.describe_choice(choice)
        self.assertIn(str(self.paths["backend"]), line)
        self.assertIn("role tag backend", line)


class ApplyPreflight(Base):
    """apply asks once for roles no tag matches, prints each choice, and never starts a browser job on a refusal."""

    def setUp(self):
        super().setUp()
        self.runtime = self.module("runtime")
        self.module("intake").setup(self.root)
        self.write_json("profile.json", candidate("authorized"))
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config["runtime"].update(apply_engine="codex")
        config["resumes"] = self.config(alternatives=[self.alt("backend", "backend")])["resumes"]
        self.write_json("config.json", config)
        store = self.module("storage").Store(self.root)
        store.enqueue(job("q-1", role="Quant Research Intern"))
        store.enqueue(job("q-2", role="Quant Research Intern - Summer 2031"))
        self.store = store

    def run_apply(self, **kwargs):
        engines, browser = self.module("engines"), self.module("browser")
        bridge = Mock(spec=["open", "fill", "upload_file", "snapshot", "submit", "close"])
        bridge.open.return_value = "tab-1"
        bridge.fill.return_value = {"status": "filled"}
        stderr = io.StringIO()
        with (
            patch.object(engines, "Engine", return_value=self.fake_engine()),
            patch.object(browser, "BrowserBridge", return_value=bridge),
            patch.object(browser, "available_memory_mb", return_value=16384),
            patch.object(browser, "launch_chrome", return_value=[{"lane": 1, "profile_dir": str(self.root / "b"),
                                                                   "debug_url": "http://127.0.0.1:9222", "pid": 1}]),
            patch("sys.stderr", stderr),
        ):
            try:
                self.runtime.apply(root=self.root, selection=["q-1", "q-2"], **kwargs)
            except Exception as error:  # the browser part of apply is mocked; the preflight is what is tested
                return stderr.getvalue(), error
        return stderr.getvalue(), None

    def test_unmatched_role_is_asked_once_for_the_whole_run_and_remembered(self):
        ask = Mock(return_value="backend")
        output, _ = self.run_apply(resume_ask=ask)
        ask.assert_called_once()
        self.assertIn(f"{self.paths['backend']} (your answer)", output)
        self.assertEqual(output.count("(your answer)") + output.count("(your earlier answer)"), 2)
        saved = json.loads((self.root / "user/resume-choices.json").read_text(encoding="utf-8"))
        self.assertEqual(list(saved["choices"]), ["quant research intern"])

    def test_refused_question_stops_before_any_browser_work(self):
        output, error = self.run_apply(resume_ask=lambda role, options: None)
        self.assertIsInstance(error, self.module("resume_choice").ResumeChoiceNeeded)
        self.assertEqual(self.store.get("q-1")["state"], "pending")
        self.assertEqual(self.store.get("q-2")["state"], "pending")
        self.assertNotIn("Apply preflight", output)

    def test_non_interactive_apply_falls_back_to_the_main_resume_with_a_printed_reason(self):
        with patch("sys.stdin", io.StringIO("")):
            output, error = self.run_apply()
        self.assertNotIsInstance(error, self.module("resume_choice").ResumeChoiceNeeded)
        self.assertIn("Apply preflight", output)
        self.assertEqual(output.count(f"{self.paths['main']} (main resume: no terminal"), 2)
        self.assertFalse((self.root / "user/resume-choices.json").exists())

    def test_terminal_prompt_maps_a_number_to_the_resume_and_blank_to_none(self):
        options = [{"id": "main", "path": "a.pdf", "tags": []}, {"id": "backend", "path": "b.pdf", "tags": []}]
        for typed, expected in (("2", "backend"), ("", None), ("9", None), ("x", None)):
            with self.subTest(typed=typed), patch("builtins.input", return_value=typed), patch("sys.stderr", io.StringIO()):
                self.assertEqual(self.runtime.terminal_ask("Quant Intern", options), expected)
        with patch("builtins.input", side_effect=EOFError), patch("sys.stderr", io.StringIO()):
            self.assertIsNone(self.runtime.terminal_ask("Quant Intern", options))
