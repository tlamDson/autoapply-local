"""Offline regression contracts for strict UTF-8 PDF tool output.

Giả định của người thực thi: reuse synthetic Tailor fixtures and mock only
subprocess.run; this test-only turn leaves the plan and export allowlist untouched.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import call, patch

import test_tailor
from test_support import ReleaseCase


UNICODE_TEXT = "Phạm · café — 12% 名"
FAILED_CHECKS = {"page_count": 0, "text_ok": False, "layout_ok": False}


class ToolTextTests(ReleaseCase):
    def tool_text(self, args, timeout):
        # Import inside each test so a missing helper does not hide other tests.
        from autoapply_local.tailor import _tool_text

        return _tool_text(args, timeout)

    def test_roundtrip_preserves_every_unicode_character_and_whitespace(self):
        tailor = self.module("tailor")
        args = ["pdftotext", "-enc", "UTF-8", "synthetic.pdf", "-"]
        expected = "\n " + UNICODE_TEXT + " \n"
        with patch.object(
            tailor.subprocess, "run", return_value=subprocess.CompletedProcess(args, 0, expected.encode("utf-8"))
        ) as run:
            self.assertEqual(self.tool_text(args, 17), expected)
        run.assert_called_once_with(args, capture_output=True, timeout=17, check=False)

    def test_invalid_or_missing_output_and_failed_exit_return_none(self):
        tailor = self.module("tailor")
        args = ["pdfinfo", "-enc", "UTF-8", "synthetic.pdf"]
        for stdout, returncode in ((None, 0), ("Pages: 1\n", 0), (b"\xb7", 0), (b"Pages: 1\n", 1)):
            with self.subTest(stdout=stdout, returncode=returncode):
                with patch.object(
                    tailor.subprocess, "run", return_value=subprocess.CompletedProcess(args, returncode, stdout)
                ):
                    self.assertIsNone(self.tool_text(args, 20))

    def test_oserror_and_timeout_return_none(self):
        tailor = self.module("tailor")
        args = ["pdfinfo", "-enc", "UTF-8", "synthetic.pdf"]
        for error in (OSError("synthetic unavailable tool"), subprocess.TimeoutExpired(args, 20)):
            with self.subTest(error=type(error).__name__):
                with patch.object(tailor.subprocess, "run", side_effect=error):
                    self.assertIsNone(self.tool_text(args, 20))


class RendererEncodingTests(ReleaseCase):
    def check_with(self, info=b"Pages: 1\n", text=None, failed_tool=None, returncode=0, error=None):
        tailor = self.module("tailor")
        pdf = self.root / "synthetic.pdf"
        outputs = {"pdfinfo": info, "pdftotext": UNICODE_TEXT.encode("utf-8") if text is None else text}

        def complete(args, **kwargs):
            self.assertEqual(args[:3], [args[0], "-enc", "UTF-8"])
            self.assertEqual(kwargs, {"capture_output": True, "timeout": 20, "check": False})
            if args[0] == failed_tool and error is not None:
                raise error
            return subprocess.CompletedProcess(args, returncode if args[0] == failed_tool else 0, outputs[args[0]])

        with patch.object(tailor.subprocess, "run", side_effect=complete) as run:
            checks = tailor.Renderer().check(pdf)
        return checks, run, pdf

    def test_both_pdf_tools_request_utf8_bytes_and_unicode_checks_succeed(self):
        checks, run, pdf = self.check_with()
        self.assertEqual(checks, {"page_count": 1, "text_ok": True, "layout_ok": True})
        self.assertEqual(
            run.call_args_list,
            [
                call(["pdfinfo", "-enc", "UTF-8", str(pdf)], capture_output=True, timeout=20, check=False),
                call(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, timeout=20, check=False),
            ],
        )

    def test_none_stdout_from_either_tool_fails_closed(self):
        tailor = self.module("tailor")
        for failed_tool in ("pdfinfo", "pdftotext"):
            with self.subTest(tool=failed_tool):

                def complete(args, **kwargs):
                    self.assertNotIn("text", kwargs)
                    stdout = None if args[0] == failed_tool else b"Pages: 1\nSynthetic text"
                    return subprocess.CompletedProcess(args, 0, stdout)

                with patch.object(tailor.subprocess, "run", side_effect=complete):
                    self.assertEqual(tailor.Renderer().check(self.root / "synthetic.pdf"), FAILED_CHECKS)

    def test_invalid_utf8_from_either_tool_fails_closed(self):
        for tool in ("pdfinfo", "pdftotext"):
            with self.subTest(tool=tool):
                checks, _, _ = self.check_with(
                    **({"info": b"Pages: 1\n\xb7"} if tool == "pdfinfo" else {"text": b"\xb7"})
                )
                self.assertEqual(checks, FAILED_CHECKS)

    def test_nonzero_exit_from_either_tool_fails_closed(self):
        for tool in ("pdfinfo", "pdftotext"):
            with self.subTest(tool=tool):
                checks, _, _ = self.check_with(failed_tool=tool, returncode=1)
                self.assertEqual(checks, FAILED_CHECKS)

    def test_missing_pages_line_fails_closed_even_with_nonempty_text(self):
        for info in (b"Title: Synthetic\n", b"Title: Pages: 1\n", b"Pages: unknown\n"):
            with self.subTest(info=info):
                checks, _, _ = self.check_with(info=info)
                self.assertEqual(checks, FAILED_CHECKS)

    def test_timeout_or_oserror_from_either_tool_fails_closed(self):
        for tool in ("pdfinfo", "pdftotext"):
            for error in (subprocess.TimeoutExpired([tool], 20), OSError("synthetic unavailable tool")):
                with self.subTest(tool=tool, error=type(error).__name__):
                    checks, _, _ = self.check_with(failed_tool=tool, error=error)
                    self.assertEqual(checks, FAILED_CHECKS)

    def test_success_keeps_existing_page_and_empty_text_semantics(self):
        for info, text, expected in (
            (b"Title: Synthetic\nPages: 2\n", b"Readable", {"page_count": 2, "text_ok": True, "layout_ok": True}),
            (b"Pages: 1\n", b" \n\t\f", {"page_count": 1, "text_ok": False, "layout_ok": True}),
        ):
            with self.subTest(info=info, text=text):
                checks, _, _ = self.check_with(info=info, text=text)
                self.assertEqual(checks, expected)


class TailorEncodingTests(ReleaseCase):
    prepared = test_tailor.TailorTests.prepared
    config = test_tailor.TailorTests.config

    def test_invalid_pdf_text_keeps_drafts_and_records_failed_checks_despite_visual_approval(self):
        runner = self.prepared()
        tailor = self.module("tailor")
        runner.renderer = tailor.Renderer()

        def complete(args, **kwargs):
            self.assertNotIn("text", kwargs)
            if args[0] == "pdftoppm":
                Path(args[-1] + "-1.png").write_bytes(b"synthetic rendered page")
                stdout = b""
            else:
                stdout = b"Pages: 1\n" if args[0] == "pdfinfo" else b"\xb7"
            return subprocess.CompletedProcess(args, 0, stdout, b"")

        with patch.object(tailor.subprocess, "run", side_effect=complete):
            result = runner.run(self.jd, self.profile, engine="codex", config=self.config())
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["checks"], FAILED_CHECKS)
        self.inspector.inspect.assert_called_once_with([Path(result["images"][0])])
        self.assertEqual(self.inspector.inspect.return_value, {"approved": True})
        self.assertTrue(Path(result["tex"]).is_file())
        self.assertTrue(Path(result["pdf"]).is_file())
        changes = (Path(result["output_dir"]) / "changes.md").read_text(encoding="utf-8")
        self.assertIn("Status: needs_review", changes)
        checks_line = next(line for line in changes.splitlines() if line.startswith("Mechanical checks: "))
        self.assertEqual(json.loads(checks_line.removeprefix("Mechanical checks: ")), FAILED_CHECKS)


class CompilerEncodingTests(ReleaseCase):
    def test_invalid_utf8_in_unused_logs_does_not_decode_and_returns_pdf(self):
        tailor = self.module("tailor")
        tex = self.root / "synthetic.tex"
        tex.write_text("synthetic source", encoding="utf-8")
        pdf = tex.with_suffix(".pdf")
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")
        with patch.object(
            tailor.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"\xb7", b"\xb7")
        ) as run:
            self.assertEqual(tailor.Compiler().compile(tex, self.root), pdf.resolve())
        run.assert_called_once()
        kwargs = run.call_args.kwargs
        for forbidden in ("text", "encoding", "errors"):
            self.assertNotIn(forbidden, kwargs)
        self.assertIs(kwargs["capture_output"], True)
        self.assertIs(kwargs["check"], True)

    def test_nonzero_compiler_exit_still_raises_called_process_error(self):
        tailor = self.module("tailor")
        tex = self.root / "synthetic.tex"
        tex.write_text("synthetic source", encoding="utf-8")

        def failed(args, **kwargs):
            self.assertIs(kwargs["check"], True)
            raise subprocess.CalledProcessError(1, args, output=b"\xb7", stderr=b"\xb7")

        with patch.object(tailor.subprocess, "run", side_effect=failed):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                tailor.Compiler().compile(tex, self.root)
        self.assertEqual(raised.exception.returncode, 1)
        self.assertEqual(raised.exception.output, b"\xb7")
        self.assertEqual(raised.exception.stderr, b"\xb7")
