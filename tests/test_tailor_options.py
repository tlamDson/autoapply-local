"""Additional plan options, kept separate from unchanged phase-A assertions.

Giả định của người thực thi: an ask-mode engine questions array returns
needs_input before compilation; real PDF smoke is optional when tools are absent.
"""

import shutil
import subprocess
from pathlib import Path

import test_tailor
from test_support import ReleaseCase


class TailorOptionTests(ReleaseCase):
    prepared = test_tailor.TailorTests.prepared
    config = test_tailor.TailorTests.config

    def test_ask_mode_returns_questions_before_any_resume_is_built(self):
        runner = self.prepared()
        self.engines["codex"].run.return_value = {
            "questions": ["Can you confirm an example of HTTP work?"],
            "company": "Example Labs",
            "role": "Backend Intern",
        }
        result = runner.run(self.jd, self.profile, engine="codex", config={**self.config(), "missing_facts": "ask"})
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["questions"], ["Can you confirm an example of HTTP work?"])
        self.engines["codex"].run.assert_called_once()
        self.compiler.compile.assert_not_called()
        self.assertFalse((self.root / "user/resumes/tailored").exists())

    def test_required_source_cannot_be_silently_omitted(self):
        runner = self.prepared()
        with self.assertRaises(ValueError):
            runner.run(
                self.jd, self.profile, engine="codex", config={**self.config(), "required_entries": ["project:1"]}
            )
        self.compiler.compile.assert_not_called()

    def test_excluded_source_cannot_appear_in_generated_resume(self):
        runner = self.prepared()
        with self.assertRaises(ValueError):
            runner.run(
                self.jd, self.profile, engine="codex", config={**self.config(), "excluded_bullets": ["experience:1"]}
            )
        self.compiler.compile.assert_not_called()

    def test_disabled_rewrite_requires_source_text_verbatim(self):
        runner = self.prepared()
        self.engines["codex"].run.return_value["claims"][0]["text"] = "Reduced Python latency by 12%."
        with self.assertRaises(ValueError):
            runner.run(self.jd, self.profile, engine="codex", config={**self.config(), "allow_rewrite": False})
        self.compiler.compile.assert_not_called()

    def test_disabled_reselection_keeps_every_nonexcluded_source(self):
        runner = self.prepared()
        with self.assertRaises(ValueError):
            runner.run(self.jd, self.profile, engine="codex", config={**self.config(), "allow_reselection": False})
        self.compiler.compile.assert_not_called()

    def test_section_order_is_taken_from_user_configuration(self):
        runner = self.prepared()
        config = {**self.config(), "section_order": ["skills", "education", "experience"]}
        result = runner.run(self.jd, self.profile, engine="codex", config=config)
        tex = Path(result["tex"]).read_text(encoding="utf-8")
        self.assertLess(tex.index(r"\cvsection{Skills}"), tex.index(r"\cvsection{Education}"))
        self.assertLess(tex.index(r"\cvsection{Education}"), tex.index(r"\cvsection{Experience}"))

    def test_real_synthetic_pdf_build_and_render_when_tools_available(self):
        required = ("pdflatex", "pdftotext", "pdfinfo", "pdftoppm")
        missing = [tool for tool in required if shutil.which(tool) is None]
        if missing:
            self.skipTest("Optional synthetic PDF smoke lacks installed tools: " + ", ".join(missing))
        runner = self.prepared(inspected=False)
        tailor = self.module("tailor")
        runner.compiler, runner.renderer = tailor.Compiler(), tailor.Renderer()
        result = runner.run(self.jd, self.profile, engine="codex", config=self.config())
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["checks"]["page_count"], 1)
        self.assertTrue(result["checks"]["text_ok"])
        self.assertTrue(result["images"])
        self.assertTrue(all(Path(image).is_file() for image in result["images"]))
        text = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", result["pdf"], "-"], check=True, capture_output=True, timeout=20
        ).stdout.decode("utf-8")
        self.assertIn("Reduced Python service latency by 12%.", text)
        self.engines["claude"].run.assert_not_called()
