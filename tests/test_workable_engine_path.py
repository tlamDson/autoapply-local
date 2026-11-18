"""Workable in production (plan aa-16): script driver first, engine second.

Production fills through the selected engine's MCP client. For Workable the
bridge runs the script driver before the engine, so only the fields the script
could not set reach the engine. Workable parses the uploaded resume into the
form, so the driver uploads the selected PDF first, scans again, proves the
upload from the browser's own File readback and only then fills. Clients are
mocks; the PDF is a synthetic file in a temporary directory.
"""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS))

from autoapply_local import browser  # noqa: E402
from autoapply_local.lever_driver import LeverDriver  # noqa: E402
from autoapply_local.script_driver import ScriptDriver  # noqa: E402
from autoapply_local.workable_driver import WorkableDriver  # noqa: E402
from test_workable_driver import JOB, answers, field, profile, scan  # noqa: E402

NOT_HANDED = ["headline", "address", "experience", "summary", "cover_letter", "QA_103"]


class Recorder:
    """A mock browser client that records the order of every call."""

    def __init__(self, audits, fill=None, upload=None, engine=False):
        self.calls = []
        self.audits = list(audits)
        self.fill = fill or {"filled": list(answers()), "blocked": [], "uploads": []}
        self.client = Mock(spec=browser.EngineMCPClient if engine else ["list_pages", "evaluate_script", "upload_file"])
        self.client.list_pages.return_value = [{"id": "tab-1", "url": JOB["url"]}]
        self.client.evaluate_script.side_effect = self.evaluate
        self.client.upload_file.side_effect = lambda tab, path: self.calls.append(("upload", path)) or upload
        if engine:
            self.client.interact.side_effect = self.interact
        self.engine_result = {"filled": ["QA_100", "QA_102", "education"], "blocked": [], "status": "filled"}

    def evaluate(self, tab, script, *, operation):
        self.calls.append((operation, script))
        return self.audits.pop(0) if operation == "audit" else self.fill

    def interact(self, tab, data, job, blocked, pdf=None):
        self.calls.append(("interact", [item["id"] for item in blocked], pdf))
        return dict(self.engine_result)


class PdfCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.pdf = Path(directory.name) / "Alex_Rivera_SWE.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n% synthetic resume\n")
        data = self.pdf.read_bytes()
        self.receipt = {"name": self.pdf.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def uploaded_scan(self, receipt=None):
        fields = scan()
        field(fields, "resume").update(value=self.pdf.name, files=[receipt or self.receipt])
        return {"fields": fields}


class WorkableUploadFirstTests(PdfCase):
    def test_resume_is_uploaded_and_proved_before_the_fill(self):
        recorder = Recorder([{"fields": scan()}, self.uploaded_scan()])
        report = WorkableDriver().run(recorder.client, "tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual([call[0] for call in recorder.calls], ["audit", "upload", "audit", "fill"])
        self.assertEqual(recorder.calls[1][1], str(self.pdf))
        self.assertNotIn(str(self.pdf), recorder.calls[3][1])
        self.assertNotIn('"resume"', recorder.calls[3][1])
        self.assertEqual(report["upload_receipt"], self.receipt)
        self.assertEqual(report["uploads"], [{"id": "resume", "path": str(self.pdf)}])
        self.assertNotIn("resume", [item["id"] for item in report["unknown"]])

    def test_upload_without_a_matching_browser_file_holds_the_tab_before_any_fill(self):
        for receipt in (None, {**self.receipt, "sha256": "0" * 64}):
            with self.subTest(receipt=receipt):
                fields = scan() if receipt is None else self.uploaded_scan(receipt)["fields"]
                recorder = Recorder([{"fields": scan()}, {"fields": fields}])
                report = WorkableDriver().run(recorder.client, "tab-1", profile(), JOB, pdf=str(self.pdf))
                self.assertEqual([call[0] for call in recorder.calls], ["audit", "upload", "audit"])
                self.assertEqual(report["status"], "needs_manual")
                self.assertEqual(report["blocked"], [{"id": "resume", "reason": "upload_not_observed"}])
                self.assertEqual(report["filled"], [])

    def test_upload_tool_reporting_needs_manual_holds_the_tab(self):
        recorder = Recorder([{"fields": scan()}], upload={"status": "needs_manual"})
        report = WorkableDriver().run(recorder.client, "tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual([call[0] for call in recorder.calls], ["audit", "upload"])
        self.assertEqual(report["status"], "needs_manual")

    def test_page_that_stops_reading_after_the_upload_holds_the_tab(self):
        recorder = Recorder([{"fields": scan()}, {"blocked": [{"id": "page", "reason": "captcha"}]}])
        report = WorkableDriver().run(recorder.client, "tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual([call[0] for call in recorder.calls], ["audit", "upload", "audit"])
        self.assertEqual(report["status"], "needs_manual")
        self.assertEqual(report["blocked"], [{"id": "page", "reason": "captcha"}])

    def test_without_a_pdf_nothing_is_uploaded(self):
        recorder = Recorder([{"fields": scan()}])
        report = WorkableDriver().run(recorder.client, "tab-1", profile(), JOB)
        self.assertEqual([call[0] for call in recorder.calls], ["audit", "fill"])
        self.assertIsNone(report["upload_receipt"])

    def test_drivers_that_do_not_parse_the_resume_keep_the_upload_after_the_fill(self):
        self.assertIs(ScriptDriver.upload_before_fill, False)
        self.assertIs(LeverDriver.upload_before_fill, True)


class WorkableEnginePathTests(PdfCase):
    def bridge(self, recorder):
        bridge = browser.BrowserBridge(recorder.client)
        bridge.tabs["tab-1"] = {"url": JOB["url"]}
        return bridge

    def test_production_runs_the_script_driver_then_hands_only_the_rest_to_the_engine(self):
        recorder = Recorder([{"fields": scan()}, self.uploaded_scan()], engine=True)
        bridge = self.bridge(recorder)
        result = bridge.fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual([call[0] for call in recorder.calls], ["audit", "upload", "audit", "fill", "interact"])
        _, handed, engine_pdf = recorder.calls[4]
        self.assertIsNone(engine_pdf)
        for key in ("firstname", "lastname", "email", "phone", "resume", *NOT_HANDED):
            self.assertNotIn(key, handed)
        self.assertEqual(handed, ["education", "QA_100", "QA_101", "QA_102", "QA_104", "gdpr"])
        self.assertEqual(result["driver"], "workable")
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["filled"], [*answers(), "QA_100", "QA_102", "education"])
        self.assertEqual(result["upload_receipt"], self.receipt)
        self.assertEqual(bridge.uploads["tab-1"], self.receipt)
        self.assertEqual(bridge.observed_uploads["tab-1"], self.receipt)

    def test_engine_status_and_blocked_reasons_are_kept(self):
        recorder = Recorder([{"fields": scan()}, self.uploaded_scan()], engine=True)
        recorder.engine_result = {"filled": [], "blocked": [{"id": "QA_104", "reason": "unknown"}], "status": "needs_manual"}
        result = self.bridge(recorder).fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual(result["status"], "needs_manual")
        self.assertEqual(result["blocked"], [{"id": "QA_104", "reason": "unknown"}])

    def test_a_tab_the_script_holds_never_reaches_the_engine(self):
        recorder = Recorder([{"blocked": [{"id": "page", "reason": "captcha"}]}], engine=True)
        result = self.bridge(recorder).fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual([call[0] for call in recorder.calls], ["audit"])
        self.assertEqual(result["status"], "needs_manual")

    def test_engine_uploads_and_proves_the_resume_when_the_script_found_no_resume_field(self):
        fields = [item for item in scan() if item["id"] != "resume"]
        recorder = Recorder([{"fields": fields}], engine=True)
        recorder.engine_result = {"filled": [], "blocked": [], "status": "filled", "upload_receipt": self.receipt}
        bridge = self.bridge(recorder)
        result = bridge.fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual([call[0] for call in recorder.calls], ["audit", "fill", "interact"])
        self.assertEqual(recorder.calls[2][2], str(self.pdf))
        self.assertEqual(result["upload_receipt"], self.receipt)
        self.assertEqual(bridge.observed_uploads["tab-1"], self.receipt)

    def test_engine_receipt_for_another_file_is_refused(self):
        fields = [item for item in scan() if item["id"] != "resume"]
        recorder = Recorder([{"fields": fields}], engine=True)
        recorder.engine_result = {
            "filled": [],
            "blocked": [],
            "status": "filled",
            "upload_receipt": {**self.receipt, "size": 1},
        }
        with self.assertRaises(ValueError):
            self.bridge(recorder).fill("tab-1", profile(), JOB, pdf=str(self.pdf))

    def test_other_ats_keep_the_single_engine_fill(self):
        recorder = Recorder([], engine=True)
        self.bridge(recorder).fill("tab-1", profile(), {**JOB, "ats": "greenhouse"})
        self.assertEqual([call[0] for call in recorder.calls], ["interact"])

    def test_engine_never_gets_a_submit_instruction_from_the_script_path(self):
        recorder = Recorder([{"fields": scan()}, self.uploaded_scan()], engine=True)
        self.bridge(recorder).fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        for call in recorder.calls:
            if call[0] in ("audit", "fill"):
                self.assertNotRegex(call[1], r"\.click\s*\(|requestSubmit|\.submit\s*\(")
        self.assertEqual(json.dumps(recorder.calls[4][1]).count("submit"), 0)


if __name__ == "__main__":
    unittest.main()
