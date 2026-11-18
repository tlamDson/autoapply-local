"""Offline regression contracts for strict UTF-8 PDF tool output.

Only subprocess.run is mocked; the helper backs source extraction.
"""

import subprocess
from unittest.mock import patch

from test_support import ReleaseCase


UNICODE_TEXT = "Hạnh · café — 12% 名"


class ToolTextTests(ReleaseCase):
    def tool_text(self, args, timeout):
        # Import inside each test so a missing helper does not hide other tests.
        from autoapply_local.extraction import _tool_text

        return _tool_text(args, timeout)

    def test_roundtrip_preserves_every_unicode_character_and_whitespace(self):
        extraction = self.module("extraction")
        args = ["pdftotext", "-enc", "UTF-8", "synthetic.pdf", "-"]
        expected = "\n " + UNICODE_TEXT + " \n"
        with patch.object(
            extraction.subprocess, "run", return_value=subprocess.CompletedProcess(args, 0, expected.encode("utf-8"))
        ) as run:
            self.assertEqual(self.tool_text(args, 17), expected)
        run.assert_called_once_with(args, capture_output=True, timeout=17, check=False)

    def test_invalid_or_missing_output_and_failed_exit_return_none(self):
        extraction = self.module("extraction")
        args = ["pdfinfo", "-enc", "UTF-8", "synthetic.pdf"]
        for stdout, returncode in ((None, 0), ("Pages: 1\n", 0), (b"\xb7", 0), (b"Pages: 1\n", 1)):
            with self.subTest(stdout=stdout, returncode=returncode):
                with patch.object(
                    extraction.subprocess, "run", return_value=subprocess.CompletedProcess(args, returncode, stdout)
                ):
                    self.assertIsNone(self.tool_text(args, 20))

    def test_oserror_and_timeout_return_none(self):
        extraction = self.module("extraction")
        args = ["pdfinfo", "-enc", "UTF-8", "synthetic.pdf"]
        for error in (OSError("synthetic unavailable tool"), subprocess.TimeoutExpired(args, 20)):
            with self.subTest(error=type(error).__name__):
                with patch.object(extraction.subprocess, "run", side_effect=error):
                    self.assertIsNone(self.tool_text(args, 20))
