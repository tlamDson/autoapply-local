"""Unknown required questions (plan aa-17), the whole loop through the engine.

A Workday apply runs through the real bridge and engine MCP client: the engine
reports an unknown required question and keeps the tab; the end-of-run popup
asks it once; the answer goes to the bank; a new process restores the held tab
and refills it by native engine interaction, then reads it back; the next job
gets the banked answer without asking. The engine is a recorded fake, the
candidate and employer are invented, and nothing here clicks Submit.
"""

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import Mock

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from test_support import NOW, ReleaseCase, candidate, job  # noqa: E402
from test_unanswered import HEAR, unknown  # noqa: E402

ENDPOINT = "http://127.0.0.1:9222"


class FakeChrome:
    """Recorded engine answers keyed by browser operation, plus Chrome's tab list."""

    def __init__(self, pdf, url):
        self.pages, self.url, self.calls = [], url, []
        self.receipt = {"name": pdf.name, "size": pdf.stat().st_size, "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
        self.unknown = [unknown("question_1", HEAR)]
        self.question_value = ""

    def run(self, task):
        kind = task["kind"]
        payload = json.loads(task["prompt"][task["prompt"].index("{"):])
        self.calls.append((kind, payload))
        if kind == "browser_open":
            self.pages.append({"id": f"tab-{len(self.pages) + 1}", "url": self.url})
            return {"status": "opened"}
        if kind == "browser_native_fill":
            return {"filled": ["name"], "blocked": [], "unknown": self.unknown, "status": "needs_manual",
                    "upload_receipt": self.receipt}
        if kind == "browser_native_refill":
            self.question_value = payload["answers"]["question_1"]
            return {"filled": ["question_1"], "blocked": [], "status": "filled"}
        if kind == "browser_evaluate":
            fields = [{"id": "question_1", "label": HEAR, "required": True, "value": self.question_value},
                      {"id": "resume", "type": "file", "files": [self.receipt]}]
            return {"result": {"fields": fields, "errors": [],
                               "required_unknown": [] if self.question_value else ["question_1"]}}
        raise AssertionError(f"unexpected browser operation {kind}")


class EngineLoopTests(ReleaseCase):
    def bridge(self, chrome):
        from autoapply_local.browser import BrowserBridge, EngineMCPClient

        engine = Mock(spec=["run"])
        engine.run.side_effect = chrome.run
        client = EngineMCPClient(engine, ENDPOINT)
        client.list_pages = Mock(side_effect=lambda: list(chrome.pages))
        return BrowserBridge(client)

    def test_workday_question_is_collected_asked_once_banked_refilled_and_reused(self):
        workflow, unanswered = self.module("workflow"), self.module("unanswered")
        self.write_json("profile.json", candidate("authorized"))
        pdf = self.root / "synthetic.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")
        posting = job(ats="workday")
        chrome = FakeChrome(pdf, posting["url"])
        runner = workflow.Workflow(self.root, browser=self.bridge(chrome), engines={"codex": self.fake_engine()},
                                   default_engine="codex", clock=lambda: NOW)
        runner.store.enqueue(posting)

        applied = runner.apply(["synthetic-1"], pdf=pdf)
        self.assertEqual(applied["jobs"][0]["status"], "needs_manual")
        self.assertEqual(applied["jobs"][0]["questions"], 1)
        held = runner.store.get("synthetic-1")
        self.assertEqual([item["id"] for item in held["unanswered"]], ["question_1"])

        questions = unanswered.Questions(self.root)
        self.assertEqual(questions.collect(runner.store.jobs())["pending"], 1)
        packet = questions.packet("claude")
        self.assertEqual(len(packet["ui"]["calls"]), 1)
        option = next(item for item in packet["questions"][0]["choices"] if item["label"] == "Friend")
        path = self.root / "answers.json"
        path.write_text(json.dumps({"batch_id": packet["batch_id"], "answers": [
            {"question_id": packet["questions"][0]["id"], "option_id": option["id"]}]}), encoding="utf-8")
        plan = questions.answer(path)["refill"]
        self.assertEqual(plan, {"synthetic-1": {"question_1": "12"}})

        # questions --answer runs in a new process: the held tab is restored from the ledger.
        later = self.bridge(chrome)
        later.restore(runner.store.get("synthetic-1"))
        second = workflow.Workflow(self.root, browser=later, engines={}, default_engine="codex", clock=lambda: NOW)
        result = second.refill("synthetic-1", plan["synthetic-1"])
        self.assertEqual(result["status"], "filled")
        self.assertEqual(second.store.get("synthetic-1")["state"], "filled")
        self.assertEqual(second.store.get("synthetic-1")["unanswered"], [])
        kinds = [kind for kind, _ in chrome.calls]
        self.assertIn("browser_native_refill", kinds)
        self.assertEqual(kinds[-1], "browser_evaluate")  # the refill is read back before it counts

        chrome.calls.clear()
        chrome.url, chrome.unknown = job("synthetic-2", ats="workday")["url"], []
        runner.store.enqueue(job("synthetic-2", ats="workday"))
        runner.apply(["synthetic-2"], pdf=pdf)
        fill = next(payload for kind, payload in chrome.calls if kind == "browser_native_fill")
        self.assertEqual([(item["question"], item["value"]) for item in fill["bank_answers"]], [(HEAR, "Friend")])
