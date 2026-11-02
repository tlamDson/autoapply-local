"""Persistent clean lanes and independent browser proof, complete-plan items 1/5.

Giả định của người thực thi: BrowserBridge(client) wraps a normalized MCP client
with new_page/list_pages/evaluate_script/upload_file methods. Native list_pages is
the authority for current URL/tab identity, not JSON reported by an agent. Field
file metadata/readback must independently establish the actual attachment. Only
the tests' injected client is used; Chrome and connectors are never contacted.
"""

import copy
import hashlib
from pathlib import Path
from unittest.mock import Mock

from test_support import ReleaseCase, job, snapshot


class BrowserCompleteTests(ReleaseCase):
    def bridge(self):
        self.client = Mock(spec=["new_page", "list_pages", "evaluate_script", "upload_file", "close_page"])
        self.client.new_page.return_value = {"id": "tab-1", "url": job()["url"]}
        self.client.list_pages.return_value = [{"id": "tab-1", "url": job()["url"]}]
        self.client.evaluate_script.return_value = snapshot()
        return self.module("browser").BrowserBridge(self.client)

    def test_lane_profile_is_persistent_between_commands_and_keeps_user_sign_in_data(self):
        browser = self.module("browser")
        spawn = Mock(return_value=Mock(pid=1234))
        first = browser.launch_chrome(self.root, executable="synthetic-chrome", spawn=spawn)[0]
        sentinel = Path(first["profile_dir"]) / "synthetic-cookie-sentinel.txt"
        sentinel.write_text("Synthetic sign-in state, not a real cookie", encoding="utf-8")
        second = browser.launch_chrome(self.root, executable="synthetic-chrome", spawn=spawn)[0]
        self.assertEqual(Path(first["profile_dir"]), Path(second["profile_dir"]))
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "Synthetic sign-in state, not a real cookie")

    def test_page_and_job_proof_cannot_override_actual_native_current_url(self):
        bridge = self.bridge()
        tab = bridge.open(job()["url"])
        self.client.list_pages.return_value = [{"id": tab, "url": "https://jobs.example.invalid/another-job"}]
        # An engine/DOM response claims the expected job and URL, but the native
        # browser is currently looking at a different application.
        self.client.evaluate_script.return_value = snapshot()
        with self.assertRaises(ValueError):
            bridge.snapshot(tab, job_id="synthetic-1")
        self.client.list_pages.assert_called()
        self.client.close_page.assert_not_called()

    def test_closed_tab_cannot_be_replaced_with_agent_supplied_page_id(self):
        bridge = self.bridge()
        tab = bridge.open(job()["url"])
        self.client.list_pages.return_value = []
        self.client.evaluate_script.return_value = snapshot(page_id="agent-invented-page")
        with self.assertRaises(ValueError):
            bridge.snapshot(tab, job_id="synthetic-1")
        self.client.close_page.assert_not_called()

    def test_agent_supplied_page_id_does_not_become_native_page_identity(self):
        bridge = self.bridge()
        tab = bridge.open(job()["url"])
        self.client.evaluate_script.return_value = snapshot(page_id="agent-invented-page")
        observed = bridge.snapshot(tab, job_id="synthetic-1")
        self.assertEqual(observed["url"], job()["url"])
        self.assertEqual(observed["tab_id"], tab)
        self.assertTrue(observed["page_id"])
        self.assertNotEqual(observed["page_id"], "agent-invented-page")
        self.client.list_pages.assert_called()

    def test_expected_hash_echo_without_selected_file_readback_is_not_attachment_proof(self):
        bridge = self.bridge()
        tab = bridge.open(job()["url"])
        pdf = self.root / "synthetic.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% synthetic bytes\n%%EOF")
        expected = hashlib.sha256(pdf.read_bytes()).hexdigest()
        bridge.upload_file(tab, str(pdf))
        self.client.evaluate_script.return_value = snapshot(resume_sha256=expected)
        # No File/attachment observation exists. Matching caller-provided hash
        # alone must not be promoted to independently verified upload proof.
        with self.assertRaises(ValueError):
            bridge.snapshot(tab, job_id="synthetic-1")
        self.client.upload_file.assert_called_once()
        self.client.close_page.assert_not_called()

    def test_real_selected_file_mismatch_is_not_hidden_by_echoed_expected_digest(self):
        bridge = self.bridge()
        tab = bridge.open(job()["url"])
        pdf = self.root / "synthetic.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% synthetic bytes\n%%EOF")
        expected = hashlib.sha256(pdf.read_bytes()).hexdigest()
        bridge.upload_file(tab, str(pdf))
        self.client.evaluate_script.return_value = snapshot(
            resume_sha256=expected,
            fields=[
                {
                    "id": "resume",
                    "label": "Resume",
                    "type": "file",
                    "required": True,
                    "value": "other.pdf",
                    "files": [{"name": "other.pdf", "size": 13, "sha256": "0" * 64}],
                }
            ],
        )
        with self.assertRaises(ValueError):
            bridge.snapshot(tab, job_id="synthetic-1")
        self.client.close_page.assert_not_called()

    def test_attachment_readback_script_does_not_receive_expected_digest_to_echo(self):
        bridge = self.bridge()
        tab = bridge.open(job()["url"])
        pdf = self.root / "synthetic.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% synthetic bytes\n%%EOF")
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        bridge.upload_file(tab, str(pdf))
        self.client.evaluate_script.return_value = snapshot(
            resume_sha256=digest,
            fields=[
                {
                    "id": "resume",
                    "label": "Resume",
                    "type": "file",
                    "required": True,
                    "value": pdf.name,
                    "files": [{"name": pdf.name, "size": pdf.stat().st_size, "sha256": digest}],
                }
            ],
        )
        observed = bridge.snapshot(tab, job_id="synthetic-1")
        self.assertEqual(observed["resume_sha256"], digest)
        self.client.evaluate_script.assert_called()
        self.assertNotIn(digest, repr(self.client.evaluate_script.call_args_list))

    def test_blocked_workday_controls_remain_unverified_and_tab_is_held(self):
        bridge = self.bridge()
        tab = bridge.open(job()["url"])
        blocked = {"id": "name--firstName", "reason": "agent_interaction_required"}
        self.client.evaluate_script.return_value = {"filled": [], "blocked": [blocked], "uploads": []}
        result = bridge.fill(tab, profile=copy.deepcopy({"contact": {}}), job=job(ats="workday"))
        self.assertIn(blocked, result["blocked"])
        self.assertNotIn("name--firstName", result["filled"])
        self.client.close_page.assert_not_called()
