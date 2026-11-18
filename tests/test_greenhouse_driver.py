"""Script driver interface and the Greenhouse script driver (plan aa-11).

The driver plans every field it can from confirmed facts with no engine call,
fills them through the Greenhouse adapter, and reports the rest as unknown for
the engine. tests/greenhouse_form.json holds the synthetic DOM, the adapter's recorded
scan of it and the planned answers; adapters.test.cjs proves the adapter
produces that scan and fills those answers. Candidate and employer are invented.
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from autoapply_local import browser, script_driver  # noqa: E402
from autoapply_local.greenhouse_driver import GreenhouseDriver  # noqa: E402
from autoapply_local.profile import fact  # noqa: E402

FIXTURE = json.loads((RELEASE / "tests" / "greenhouse_form.json").read_text(encoding="utf-8"))
JOB = {
    "id": "gh-1",
    "ats": "greenhouse",
    "url": "https://job-boards.greenhouse.io/exampleco/jobs/1001",
    "company": "Example Co",
    "country": "US",
    "level": "internship",
    "start_date": "2027-06-01",
}
AUTH_QUESTION = "Are you legally authorized to work in the United States?"


def recorded(name):
    return {"fields": copy.deepcopy(FIXTURE["scan"])} if name == "scan.json" else {"answers": copy.deepcopy(FIXTURE["answers"])}


def profile():
    scope = {
        "country": "US",
        "job_type": "internship",
        "employer": "Example Co",
        "question": AUTH_QUESTION,
        "effective_at": "2027-06-01",
    }
    return {
        "schema_version": 1,
        "revision": 1,
        "contact": {
            "first_name": fact("Alex"),
            "last_name": fact("Rivera"),
            "preferred_name": fact(None, "unknown"),
            "application_email": fact("alex.rivera@example.com"),
            "phone": fact("+1 555 0100"),
            "city": fact("Springfield"),
            "linkedin": fact("https://www.linkedin.com/in/alex-rivera-example"),
            "portfolio": fact("https://alex-rivera.example"),
        },
        "compensation": {"eeo": fact({"gender": "Decline To Self Identify"})},
        "answers": [{"question": AUTH_QUESTION, **fact("Yes", scope=scope)}],
    }


def unknown_by_id(plan):
    return {item["id"]: item for item in plan["unknown"]}


class GreenhousePlanTests(unittest.TestCase):
    def plan(self, pdf=None):
        return GreenhouseDriver().plan(recorded("scan.json")["fields"], profile(), JOB, pdf=pdf)

    def test_every_known_field_is_planned_from_confirmed_facts(self):
        self.assertEqual(self.plan()["answers"], recorded("answers.json")["answers"])

    def test_unknown_fields_are_reported_with_reason_and_required_flag(self):
        unknown = unknown_by_id(self.plan())
        self.assertEqual(
            {key: (item["reason"], item["required"]) for key, item in unknown.items()},
            {
                "preferred_name": ("no_confirmed_fact", False),
                "candidate-location": ("agent_interaction_required", True),
                "cover_letter": ("no_confirmed_fact", False),
                "question_1004": ("no_confirmed_fact", True),
                "question_1005": ("no_confirmed_fact", True),
                "gender": ("agent_interaction_required", False),
                "gdpr_consent": ("no_confirmed_fact", False),
            },
        )
        self.assertEqual(unknown["question_1004"]["label"], recorded("scan.json")["fields"][11]["label"])
        self.assertEqual(unknown["question_1004"]["options"][1], {"value": "1", "label": "Yes"})

    def test_widgets_carry_the_confirmed_value_so_the_engine_only_picks_it(self):
        unknown = unknown_by_id(self.plan())
        self.assertEqual(unknown["candidate-location"]["value"], "Springfield")
        self.assertEqual(unknown["gender"]["value"], "Decline To Self Identify")
        self.assertNotIn("value", unknown["question_1004"])

    def test_sponsorship_is_never_inferred_without_an_exact_answer(self):
        self.assertNotIn("question_1004", self.plan()["answers"])

    def test_resume_field_gets_the_selected_pdf_and_is_not_unknown(self):
        plan = self.plan(pdf="C:/synthetic/Alex_Rivera_SWE.pdf")
        self.assertEqual(plan["answers"]["resume"], "C:/synthetic/Alex_Rivera_SWE.pdf")
        self.assertEqual(plan["resume"], "resume")
        self.assertNotIn("resume", unknown_by_id(plan))
        self.assertEqual(self.plan()["resume"], "resume")
        self.assertNotIn("resume", self.plan()["answers"])

    def test_choice_without_a_matching_option_is_reported_not_guessed(self):
        data = profile()
        data["answers"][0]["value"] = "Maybe"
        plan = GreenhouseDriver().plan(recorded("scan.json")["fields"], data, JOB)
        self.assertNotIn("question_1003", plan["answers"])
        self.assertEqual(unknown_by_id(plan)["question_1003"]["reason"], "option_not_found")

    def test_already_answered_fields_without_a_fact_are_not_reported(self):
        fields = copy.deepcopy(recorded("scan.json")["fields"])
        fields[12]["value"] = "Typed by the candidate in the held tab."
        self.assertNotIn("question_1005", unknown_by_id(GreenhouseDriver().plan(fields, profile(), JOB)))

    def test_unconfirmed_facts_are_not_used(self):
        data = profile()
        data["contact"]["phone"] = fact("+1 555 0199", "unknown")
        plan = GreenhouseDriver().plan(recorded("scan.json")["fields"], data, JOB)
        self.assertNotIn("phone", plan["answers"])
        self.assertEqual(unknown_by_id(plan)["phone"]["reason"], "no_confirmed_fact")


class ScriptDriverRunTests(unittest.TestCase):
    def client(self, fill_result=None):
        client = Mock(spec=["evaluate_script"])
        audit = {"fields": recorded("scan.json")["fields"], "page": {"index": 1, "total": 1, "title": ""}}
        fill = fill_result or {"filled": list(recorded("answers.json")["answers"]), "blocked": [], "uploads": []}
        client.evaluate_script.side_effect = [audit, fill]
        return client

    def test_run_scans_fills_planned_answers_and_reports_unknown_for_the_engine(self):
        client = self.client()
        report = GreenhouseDriver().run(client, "tab-1", profile(), JOB)
        (_, audit_script), audit_kw = client.evaluate_script.call_args_list[0]
        (_, fill_script), fill_kw = client.evaluate_script.call_args_list[1]
        self.assertEqual((audit_kw["operation"], fill_kw["operation"]), ("audit", "fill"))
        self.assertIn("AutoApplyGreenhouse.audit(document)", audit_script)
        sent = {key: {"value": value, "status": "confirmed"} for key, value in recorded("answers.json")["answers"].items()}
        self.assertIn(json.dumps(sent, ensure_ascii=False), fill_script)
        self.assertEqual(report["driver"], "greenhouse")
        self.assertEqual(report["filled"], list(recorded("answers.json")["answers"]))
        self.assertEqual(report["blocked"], [])
        self.assertEqual(
            [item["id"] for item in report["unknown"]],
            ["preferred_name", "candidate-location", "cover_letter", "question_1004", "question_1005", "gender", "gdpr_consent"],
        )
        self.assertEqual(report["status"], "needs_engine")

    def test_adapter_blocks_become_unknown_fields(self):
        answers = recorded("answers.json")["answers"]
        filled = [key for key in answers if key != "question_1003"]
        client = self.client({"filled": filled, "blocked": [{"id": "question_1003", "reason": "option_not_found"}], "uploads": []})
        report = GreenhouseDriver().run(client, "tab-1", profile(), JOB)
        self.assertIn({"id": "question_1003", "reason": "option_not_found"}, report["blocked"])
        blocked = [item for item in report["unknown"] if item["id"] == "question_1003"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual((blocked[0]["reason"], blocked[0]["required"]), ("option_not_found", True))

    def test_status_is_filled_when_no_required_field_is_unknown(self):
        fields = [field for field in recorded("scan.json")["fields"] if not field["required"] or field["id"] in recorded("answers.json")["answers"]]
        client = Mock(spec=["evaluate_script"])
        client.evaluate_script.side_effect = [{"fields": fields}, {"filled": [], "blocked": [], "uploads": []}]
        self.assertEqual(GreenhouseDriver().run(client, "tab-1", profile(), JOB)["status"], "filled")


class SubmitGuardTests(unittest.TestCase):
    def test_greenhouse_scripts_contain_no_click_or_submit_call(self):
        driver = GreenhouseDriver()
        for script in (driver.script("audit"), driver.script("fill", recorded("answers.json")["answers"])):
            self.assertNotRegex(script, r"\.click\s*\(|requestSubmit|\.submit\s*\(")

    def test_a_driver_whose_adapter_can_click_is_refused(self):
        class Clicking(script_driver.ScriptDriver):
            name = "workday"  # workday.cjs clicks Next/Back, so it cannot be a script driver

        with self.assertRaisesRegex(ValueError, "click"):
            Clicking().script("fill", {})


class RegistryAndBridgeTests(unittest.TestCase):
    def test_registry_returns_the_greenhouse_driver_and_none_for_others(self):
        self.assertIsInstance(script_driver.driver_for("greenhouse"), GreenhouseDriver)
        self.assertIsNone(script_driver.driver_for("icims"))

    def test_bridge_fills_greenhouse_through_the_script_driver(self):
        client = Mock(spec=["new_page", "list_pages", "evaluate_script", "upload_file", "close_page"])
        client.list_pages.return_value = [{"id": "tab-1", "url": JOB["url"]}]
        client.evaluate_script.side_effect = [
            {"fields": recorded("scan.json")["fields"]},
            {"filled": list(recorded("answers.json")["answers"]), "blocked": [], "uploads": []},
        ]
        result = browser.BrowserBridge(client).fill("tab-1", profile(), JOB)
        self.assertEqual(result["driver"], "greenhouse")
        self.assertEqual(result["filled"], list(recorded("answers.json")["answers"]))
        self.assertIn("question_1005", [item["id"] for item in result["unknown"]])
        client.close_page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
