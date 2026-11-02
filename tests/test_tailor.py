"""Source-bound tailoring: isolated engine, immutable facts and checked new PDFs.

Giả định của người thực thi: Tailor(root,engines,compiler,renderer,inspector).run
(jd,profile,engine,config) returns output_dir/status. Engine.run returns claims;
compiler.compile(tex,output_dir,shell_escape=False,timeout=...) returns PDF path;
renderer.render(pdf) returns page images and inspector.inspect(images) a verdict.
"""

import copy
from pathlib import Path
from unittest.mock import Mock

from test_support import ReleaseCase, candidate


def sources():
    return [
        {
            "id": "experience:1",
            "type": "experience",
            "status": "confirmed",
            "employer": "Example Systems",
            "title": "Engineering Intern",
            "start": "2029-06",
            "end": "2029-08",
            "metric": "12%",
            "text": "Reduced Python service latency by 12%.",
        },
        {
            "id": "project:1",
            "type": "project",
            "status": "confirmed",
            "text": "Built a Python API.",
            "skills": ["Python", "HTTP"],
        },
    ]


class TailorTests(ReleaseCase):
    def prepared(self, inspected=True):
        tailor = self.module("tailor")
        self.jd = self.root / "synthetic-jd.txt"
        self.jd.write_text("Example Labs\nBackend Intern\nPython, HTTP and Rust", encoding="utf-8")
        self.profile = candidate()
        self.profile["fact_sources"] = sources()
        self.engines = {"codex": self.fake_engine(), "claude": self.fake_engine()}
        self.claim = copy.deepcopy(sources()[0])
        self.claim["source_ids"] = ["experience:1"]
        for engine in self.engines.values():
            engine.run.return_value = {"claims": [self.claim], "company": "Example Labs", "role": "Backend Intern"}
        self.compiler = Mock(spec=["compile"])

        def compile_pdf(tex, output_dir, **kwargs):
            pdf = Path(output_dir) / "resume.pdf"
            pdf.write_bytes(b"%PDF-1.4\n% synthetic compiler output\n%%EOF")
            return pdf

        self.compiler.compile.side_effect = compile_pdf
        self.renderer = Mock(spec=["render", "check"])
        self.renderer.render.return_value = ["synthetic-page.png"]
        self.renderer.check.return_value = {"page_count": 1, "text_ok": True, "layout_ok": True}
        self.inspector = Mock(spec=["inspect"]) if inspected else None
        if self.inspector:
            self.inspector.inspect.return_value = {"approved": True}
        return tailor.Tailor(
            self.root, engines=self.engines, compiler=self.compiler, renderer=self.renderer, inspector=self.inspector
        )

    def config(self):
        return {
            "enabled": True,
            "scope": "all",
            "pages": 1,
            "missing_facts": "omit",
            "filename": "resume",
            "allow_rewrite": True,
            "allow_reselection": True,
        }

    def test_selected_engine_only_and_no_browser_dependency(self):
        runner = self.prepared()
        result = runner.run(self.jd, self.profile, engine="codex", config=self.config())
        self.engines["codex"].run.assert_called_once()
        self.engines["claude"].run.assert_not_called()
        self.assertTrue(Path(result["output_dir"]).is_dir())
        # This constructor deliberately has no browser parameter. Engine tasks
        # must also describe tailoring, never an apply/open-form workflow.
        task = self.engines["codex"].run.call_args.args[0]
        self.assertEqual(task["kind"], "tailor")
        self.assertNotIn("browser", task)

    def test_immutable_employer_title_dates_and_metrics_cannot_change(self):
        tailor = self.module("tailor")
        for field, value in {
            "employer": "Invented Employer",
            "title": "Senior Engineer",
            "start": "2028-01",
            "end": "2030-01",
            "metric": "90%",
        }.items():
            with self.subTest(field=field):
                claim = {**sources()[0], "source_ids": ["experience:1"], field: value}
                with self.assertRaises(ValueError):
                    tailor.validate_claims(sources(), [claim])

    def test_claim_requires_existing_typed_confirmed_source(self):
        tailor = self.module("tailor")
        for source_id in (None, "experience:missing", "project:1"):
            with self.subTest(source_id=source_id):
                claim = {**sources()[0], "source_ids": [] if source_id is None else [source_id]}
                with self.assertRaises(ValueError):
                    tailor.validate_claims(sources(), [claim])
        unconfirmed = sources()
        unconfirmed[0]["status"] = "unknown"
        with self.assertRaises(ValueError):
            tailor.validate_claims(unconfirmed, [{**sources()[0], "source_ids": ["experience:1"]}])

    def test_rewrite_cannot_hide_invented_metric_in_free_text(self):
        claim = {**sources()[0], "source_ids": ["experience:1"], "text": "Led 50 engineers and reduced latency by 90%."}
        with self.assertRaises(ValueError):
            self.module("tailor").validate_claims(sources(), [claim])

    def test_keywords_distinguish_have_adjacent_missing_with_source_evidence(self):
        result = self.module("tailor").classify_keywords(
            ["Python", "REST", "Rust"], sources(), adjacency={"REST": ["HTTP"]}
        )
        self.assertEqual(result["Python"]["category"], "HAVE")
        self.assertEqual(result["REST"]["category"], "ADJACENT")
        self.assertEqual(result["Rust"]["category"], "MISSING")
        self.assertIn("project:1", result["Python"]["source_ids"])
        self.assertEqual(result["Rust"]["source_ids"], [])

    def test_tex_escaping_prevents_user_text_becoming_executable_commands(self):
        tailor = self.module("tailor")
        self.assertEqual(
            tailor.escape_latex("A&B_50% #1 {x} $5 ~ ^ \\"),
            r"A\&B\_50\% \#1 \{x\} \$5 \textasciitilde{} \textasciicircum{} \textbackslash{}",
        )
        escaped = tailor.escape_latex(r"\input{private} & 12%")
        self.assertNotIn(r"\input{private}", escaped)
        self.assertIn(r"\textbackslash{}input\{private\}", escaped)

    def test_compile_disables_shell_escape_and_has_finite_timeout(self):
        runner = self.prepared()
        runner.run(self.jd, self.profile, engine="codex", config=self.config())
        self.compiler.compile.assert_called_once()
        self.assertIs(self.compiler.compile.call_args.kwargs["shell_escape"], False)
        timeout = self.compiler.compile.call_args.kwargs["timeout"]
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 120)
        self.renderer.render.assert_called_once()
        self.renderer.check.assert_called_once()
        self.inspector.inspect.assert_called_once()

    def test_each_run_uses_new_safe_path_and_preserves_source(self):
        runner = self.prepared()
        original = self.jd.read_bytes()
        self.engines["codex"].run.return_value.update({"company": "../../Example Labs", "role": "../Backend:Intern"})
        first = runner.run(self.jd, self.profile, engine="codex", config=self.config())
        second = runner.run(self.jd, self.profile, engine="codex", config=self.config())
        first_path, second_path = Path(first["output_dir"]), Path(second["output_dir"])
        base = (self.root / "user/resumes/tailored").resolve()
        self.assertTrue(first_path.resolve().is_relative_to(base))
        self.assertTrue(second_path.resolve().is_relative_to(base))
        self.assertNotEqual(first_path, second_path)
        self.assertTrue((first_path / "resume.tex").is_file())
        self.assertTrue((first_path / "resume.pdf").is_file())
        self.assertTrue((first_path / "changes.md").is_file())
        self.assertEqual(self.jd.read_bytes(), original)

    def test_without_visual_inspection_pdf_remains_needs_review(self):
        runner = self.prepared(inspected=False)
        result = runner.run(self.jd, self.profile, engine="codex", config=self.config())
        self.assertEqual(result["status"], "needs_review")

    def test_invalid_page_text_or_layout_checks_cannot_approve_pdf(self):
        runner = self.prepared()
        for checks in (
            {"page_count": 2, "text_ok": True, "layout_ok": True},
            {"page_count": 1, "text_ok": False, "layout_ok": True},
            {"page_count": 1, "text_ok": True, "layout_ok": False},
        ):
            with self.subTest(checks=checks):
                self.renderer.check.return_value = checks
                result = runner.run(self.jd, self.profile, engine="codex", config=self.config())
                self.assertNotEqual(result["status"], "approved")

    def test_one_targeted_repair_maximum_and_no_engine_fallback(self):
        runner = self.prepared()
        self.engines["codex"].run.return_value = {
            "claims": [{**self.claim, "title": "Invented Senior Title"}],
            "company": "Example Labs",
            "role": "Intern",
        }
        with self.assertRaises(ValueError):
            runner.run(self.jd, self.profile, engine="codex", config=self.config())
        self.assertEqual(self.engines["codex"].run.call_count, 2)
        self.engines["claude"].run.assert_not_called()
        self.compiler.compile.assert_not_called()

    def test_scope_exclusion_does_not_generate_or_compile(self):
        runner = self.prepared()
        config = {**self.config(), "scope": "priority", "employers": ["Other Example"]}
        result = runner.run(self.jd, self.profile, engine="codex", config=config)
        self.assertEqual(result["status"], "out_of_scope")
        self.engines["codex"].run.assert_not_called()
        self.compiler.compile.assert_not_called()

    def test_pdf_extraction_produces_unconfirmed_draft_only(self):
        tailor = self.module("tailor")
        path = self.root / "synthetic.pdf"
        path.write_bytes(b"%PDF-1.4\n% synthetic\n%%EOF")
        extractor = Mock(return_value="Alex Example, Engineering Intern, 2029")
        result = tailor.extract_source(path, extractor=extractor)
        self.assertEqual(result["status"], "draft")
        self.assertTrue(result["requires_confirmation"])
        self.assertTrue(all(item["status"] != "confirmed" for item in result["facts"]))

    def test_tex_source_is_read_as_text_without_executing_it(self):
        tailor = self.module("tailor")
        path = self.root / "synthetic.tex"
        path.write_text(r"\documentclass{article}\immediate\write18{UNSAFE_SYNTHETIC}", encoding="utf-8")
        result = tailor.extract_source(path)
        self.assertEqual(result["status"], "draft")
        self.assertIn("UNSAFE_SYNTHETIC", result["text"])

    def test_configured_output_filename_is_used_in_new_run_directory(self):
        runner = self.prepared()
        result = runner.run(
            self.jd, self.profile, engine="codex", config={**self.config(), "filename": "Alex_Example_Backend"}
        )
        output = Path(result["output_dir"])
        self.assertTrue((output / "Alex_Example_Backend.tex").is_file())
        self.assertTrue((output / "Alex_Example_Backend.pdf").is_file())
        self.assertTrue((output / "changes.md").is_file())

    def test_extracted_source_draft_cannot_be_reused_as_confirmed_claim_evidence(self):
        tailor = self.module("tailor")
        path = self.root / "synthetic.pdf"
        path.write_bytes(b"%PDF-1.4\n% synthetic\n%%EOF")
        draft = tailor.extract_source(path, extractor=Mock(return_value="Reduced latency by 12%."))
        self.assertTrue(draft["facts"])
        evidence = {**sources()[0], "status": "draft", "provenance": {"kind": "pdf_extraction"}}
        with self.assertRaises(ValueError):
            tailor.validate_claims([evidence], [{**sources()[0], "source_ids": ["experience:1"]}])
