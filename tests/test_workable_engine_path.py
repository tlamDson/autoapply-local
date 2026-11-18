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

AUTH = "Are you legally authorized to work in the United States?"
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
            self.client.interact_fields = Mock(side_effect=self.interact_fields)
        self.engine_result = {"filled": ["QA_100", "QA_102", "education"], "blocked": [], "status": "filled"}

    def evaluate(self, tab, script, *, operation):
        self.calls.append((operation, script))
        return self.audits.pop(0) if operation == "audit" else self.fill

    def interact(self, tab, data, job, blocked, pdf=None):
        self.calls.append(("interact", [item["id"] for item in blocked], pdf))
        return dict(self.engine_result)

    def interact_fields(self, tab, job, fields):
        self.calls.append(("interact_fields", [dict(item) for item in fields]))
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
    """After the script pass the engine gets only the listed fields, never the form."""

    def bridge(self, recorder):
        bridge = browser.BrowserBridge(recorder.client)
        bridge.tabs["tab-1"] = {"url": JOB["url"]}
        return bridge

    def answered(self, fields):
        """The fixture form without its required questions that have no confirmed fact."""
        return [item for item in fields if item["id"] not in ("QA_101", "QA_104", "gdpr")]

    def test_production_hands_the_engine_only_the_native_click_fields_with_their_values(self):
        recorder = Recorder([{"fields": scan()}, self.uploaded_scan()], engine=True)
        bridge = self.bridge(recorder)
        result = bridge.fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual([call[0] for call in recorder.calls], ["audit", "upload", "audit", "fill", "interact_fields"])
        recorder.client.interact.assert_not_called()
        handed = recorder.calls[4][1]
        self.assertEqual([item["id"] for item in handed], ["education", "QA_100", "QA_102"])
        self.assertTrue(all("value" in item for item in handed))
        for key in ("firstname", "lastname", "email", "phone", "resume", "QA_101", "QA_104", "gdpr", *NOT_HANDED):
            self.assertNotIn(key, [item["id"] for item in handed])
        self.assertEqual(result["driver"], "workable")
        self.assertEqual(result["filled"], [*answers(), "QA_100", "QA_102", "education"])
        self.assertEqual(result["upload_receipt"], self.receipt)
        self.assertEqual(bridge.uploads["tab-1"], self.receipt)
        self.assertEqual(bridge.observed_uploads["tab-1"], self.receipt)

    def test_required_questions_without_a_confirmed_fact_hold_the_tab_instead_of_reaching_the_engine(self):
        recorder = Recorder([{"fields": scan()}, self.uploaded_scan()], engine=True)
        result = self.bridge(recorder).fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual(result["status"], "needs_manual")
        self.assertEqual(
            [item for item in result["blocked"] if item["reason"] == "no_confirmed_fact"],
            [{"id": key, "reason": "no_confirmed_fact"} for key in ("QA_101", "QA_104", "gdpr")],
        )
        self.assertEqual([item["id"] for item in result["unknown"]], ["QA_101", "QA_104", "gdpr"])

    def test_form_with_every_required_question_answered_is_filled(self):
        uploaded = self.uploaded_scan()["fields"]
        recorder = Recorder([{"fields": self.answered(scan())}, {"fields": self.answered(uploaded)}], engine=True)
        result = self.bridge(recorder).fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["blocked"], [])
        self.assertEqual(result["unknown"], [])

    def test_engine_status_and_blocked_reasons_are_kept(self):
        uploaded = self.uploaded_scan()["fields"]
        recorder = Recorder([{"fields": self.answered(scan())}, {"fields": self.answered(uploaded)}], engine=True)
        recorder.engine_result = {
            "filled": [],
            "blocked": [{"id": "QA_100", "reason": "option_missing"}],
            "status": "needs_manual",
        }
        result = self.bridge(recorder).fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual(result["status"], "needs_manual")
        self.assertEqual(result["blocked"], [{"id": "QA_100", "reason": "option_missing"}])
        self.assertEqual([item["id"] for item in result["unknown"]], ["education", "QA_100", "QA_102"])

    def test_nothing_left_for_the_engine_means_no_engine_call(self):
        keep = lambda fields: [  # noqa: E731
            item for item in self.answered(fields) if item["id"] not in ("education", "QA_100", "QA_102")
        ]
        recorder = Recorder([{"fields": keep(scan())}, {"fields": keep(self.uploaded_scan()["fields"])}], engine=True)
        result = self.bridge(recorder).fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual([call[0] for call in recorder.calls], ["audit", "upload", "audit", "fill"])
        recorder.client.interact.assert_not_called()
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["upload_receipt"], self.receipt)

    def test_a_tab_the_script_holds_never_reaches_the_engine(self):
        recorder = Recorder([{"blocked": [{"id": "page", "reason": "captcha"}]}], engine=True)
        result = self.bridge(recorder).fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual([call[0] for call in recorder.calls], ["audit"])
        self.assertEqual(result["status"], "needs_manual")

    def test_no_resume_field_holds_the_tab_and_the_engine_never_uploads(self):
        fields = [item for item in scan() if item["id"] != "resume"]
        recorder = Recorder([{"fields": fields}], engine=True)
        bridge = self.bridge(recorder)
        result = bridge.fill("tab-1", profile(), JOB, pdf=str(self.pdf))
        self.assertEqual([call[0] for call in recorder.calls], ["audit"])
        recorder.client.interact.assert_not_called()
        self.assertEqual(result["status"], "needs_manual")
        self.assertEqual(result["blocked"], [{"id": None, "reason": "resume_field_not_found"}])
        self.assertNotIn("tab-1", bridge.uploads)

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
        self.assertEqual(json.dumps(recorder.calls[4][1]).lower().count("submit"), 0)


class ScopedEngineRequestTests(unittest.TestCase):
    """The scoped engine request names only the listed fields: no profile, PDF or upload."""

    def request(self):
        engine = Mock()
        engine.run.return_value = {"filled": ["QA_100"], "blocked": [], "status": "filled"}
        client = browser.EngineMCPClient(engine, "http://127.0.0.1:9222")
        client.list_pages = Mock(return_value=[{"id": "tab-1", "url": JOB["url"]}])
        fields = [
            {
                "id": "QA_100",
                "label": AUTH,
                "type": "radiogroup",
                "required": True,
                "options": ["Yes", "No"],
                "reason": "agent_interaction_required",
                "value": "Yes",
            }
        ]
        result = client.interact_fields("tab-1", JOB, fields)
        return result, engine.run.call_args.args[0]

    def test_request_carries_only_the_listed_fields_and_their_values(self):
        result, request = self.request()
        self.assertEqual(result["status"], "filled")
        payload = json.loads(request["prompt"][request["prompt"].index("{") :])
        self.assertEqual(payload["operation"], "field_fill")
        self.assertEqual(
            payload["fields"],
            [{"id": "QA_100", "label": AUTH, "type": "radiogroup", "options": ["Yes", "No"], "value": "Yes"}],
        )
        for key in ("profile", "pdf", "blocked", "job"):
            self.assertNotIn(key, payload)
        self.assertIn("only", payload["instructions"].lower())
        self.assertNotIn("upload the", payload["instructions"].lower())

    def test_request_cannot_upload_or_run_scripts(self):
        _, request = self.request()
        tools = [name.rsplit("__", 1)[1] for name in request["allowed_tools"]]
        for tool in ("upload_file", "fill_form", "evaluate_script", "new_page"):
            self.assertNotIn(tool, tools)
        self.assertIn("click", tools)
        self.assertNotIn("upload_receipt", request["schema"]["properties"])


if __name__ == "__main__":
    unittest.main()
