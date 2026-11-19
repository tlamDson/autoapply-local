"""script_then_engine keeps only engine-filled IDs it asked for (PR #16 review)."""

import unittest

from autoapply_local.script_handoff import script_then_engine


class FakeDriver:
    name = "workable"

    def run(self, scripts, tab_id, profile, job, pdf=None, bank=None):
        return {
            "status": "filled",
            "filled": ["first_name"],
            "blocked": [],
            "unknown": [
                {"id": "country", "label": "Country", "type": "select", "required": True,
                 "value": "Canada", "reason": "agent_interaction_required"},
            ],
        }


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def interact_fields(self, tab_id, pending):
        self.calls.append([item["id"] for item in pending])
        return self.reply


class FakeBridge:
    def __init__(self, reply):
        self.client = FakeClient(reply)
        self.uploads, self.observed_uploads = {}, {}

    def scripts_for(self, name):
        return {}


def run(reply):
    bridge = FakeBridge(reply)
    profile = {"name": "Alex Rivera"}
    return bridge, script_then_engine(bridge, FakeDriver(), 7, profile, {"id": "job-1"}, "resume.pdf")


class EngineIdTests(unittest.TestCase):
    def test_unrequested_engine_id_is_blocked_and_holds_tab(self):
        bridge, result = run({"filled": ["country", "salary"], "blocked": [], "status": "filled"})
        self.assertEqual(bridge.client.calls, [["country"]])
        self.assertIn("country", result["filled"])
        self.assertNotIn("salary", result["filled"])
        self.assertIn({"id": "salary", "reason": "unrequested_field"}, result["blocked"])
        self.assertEqual(result["status"], "needs_manual")

    def test_unrequested_id_does_not_cover_missing_requested_field(self):
        _, result = run({"filled": ["salary"], "blocked": [], "status": "filled"})
        self.assertNotIn("salary", result["filled"])
        self.assertNotIn("country", result["filled"])
        self.assertIn("country", [item["id"] for item in result["unknown"]])
        self.assertEqual(result["status"], "needs_manual")

    def test_clean_reply_still_fills(self):
        _, result = run({"filled": ["country"], "blocked": [], "status": "filled"})
        self.assertEqual(result["filled"], ["first_name", "country"])
        self.assertEqual(result["blocked"], [])
        self.assertEqual(result["status"], "filled")


if __name__ == "__main__":
    unittest.main()
