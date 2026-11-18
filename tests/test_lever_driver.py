"""Lever script driver (plan aa-15).

Lever's application form has no stable element IDs, so the adapter binds every
control to its form ``name`` (``name``, ``urls[LinkedIn]``, ``cards[<id>][fieldN]``,
``eeo[gender]``). tests/lever_form.json holds the synthetic DOM, the adapter's
recorded scan of it and the planned answers; adapters.test.cjs proves the
adapter produces that scan and fills those answers. Candidate and employer are
invented.
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
from autoapply_local.lever_driver import LeverDriver  # noqa: E402
from autoapply_local.profile import fact  # noqa: E402

FIXTURE = json.loads((RELEASE / "tests" / "lever_form.json").read_text(encoding="utf-8"))
JOB = {
    "id": "lv-1",
    "ats": "lever",
    "url": "https://jobs.lever.co/exampleco/0000-synthetic/apply",
    "company": "Example Co",
    "country": "US",
    "level": "internship",
    "start_date": "2027-06-01",
}
AUTH_QUESTION = "Are you legally authorized to work in the United States?"
PDF = "C:/synthetic/Alex_Rivera_SWE.pdf"


def scan():
    return copy.deepcopy(FIXTURE["scan"])


def answers():
    return copy.deepcopy(FIXTURE["answers"])


def field(fields, key):
    return next(item for item in fields if item["id"] == key)


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
            "legal_name": fact("Alex Rivera"),
            "first_name": fact("Alex"),
            "last_name": fact("Rivera"),
            "application_email": fact("alex.rivera@example.com"),
            "phone": fact("+1 555 0100"),
            "city": fact("Springfield"),
            "linkedin": fact("https://www.linkedin.com/in/alex-rivera-example"),
            "github": fact(None, "unknown"),
            "portfolio": fact("https://alex-rivera.example"),
        },
        "compensation": {"eeo": fact({"gender": "Decline to self-identify"})},
        "answers": [{"question": AUTH_QUESTION, **fact("Yes", scope=scope)}],
    }


def unknown_by_id(plan):
    return {item["id"]: item for item in plan["unknown"]}


class LeverPlanTests(unittest.TestCase):
    def plan(self, data=None, fields=None, pdf=None):
        return LeverDriver().plan(fields or scan(), data or profile(), JOB, pdf=pdf)

    def test_every_known_field_is_planned_from_confirmed_facts(self):
        self.assertEqual(self.plan()["answers"], answers())

    def test_unknown_fields_are_reported_with_reason_and_required_flag(self):
        self.assertEqual(
            {key: (item["reason"], item["required"]) for key, item in unknown_by_id(self.plan()).items()},
            {
                "location": ("agent_interaction_required", True),
                "org": ("no_confirmed_fact", False),
                "urls[GitHub]": ("no_confirmed_fact", False),
                "cards[a1b2][field1]": ("no_confirmed_fact", True),
                "cards[a1b2][field2]": ("no_confirmed_fact", False),
                "cards[a1b2][field3]": ("no_confirmed_fact", True),
                "comments": ("no_confirmed_fact", False),
                "consent[store]": ("no_confirmed_fact", False),
            },
        )
        sponsorship = unknown_by_id(self.plan())["cards[a1b2][field1]"]
        self.assertEqual(sponsorship["label"], field(scan(), "cards[a1b2][field1]")["label"])
        self.assertEqual(sponsorship["options"], [{"value": "Yes", "label": "Yes"}, {"value": "No", "label": "No"}])

    def test_location_widget_carries_the_confirmed_city_for_the_engine(self):
        location = unknown_by_id(self.plan())["location"]
        self.assertEqual(location["value"], "Springfield")
        self.assertEqual(location["type"], "combobox")

    def test_full_name_falls_back_to_confirmed_first_and_last_name(self):
        data = profile()
        data["contact"]["legal_name"] = fact(None, "unknown")
        self.assertEqual(self.plan(data)["answers"]["name"], "Alex Rivera")

    def test_full_name_is_unknown_when_any_name_part_is_unconfirmed(self):
        data = profile()
        data["contact"]["legal_name"] = fact(None, "unknown")
        data["contact"]["last_name"] = fact("Rivera", "unknown")
        plan = self.plan(data)
        self.assertNotIn("name", plan["answers"])
        self.assertEqual(unknown_by_id(plan)["name"]["reason"], "no_confirmed_fact")

    def test_url_fields_map_by_their_lever_name(self):
        fields = scan()
        field(fields, "urls[LinkedIn]")["label"] = "Profile link"
        self.assertEqual(
            self.plan(fields=fields)["answers"]["urls[LinkedIn]"], "https://www.linkedin.com/in/alex-rivera-example"
        )

    def test_sponsorship_is_never_inferred_without_an_exact_answer(self):
        self.assertNotIn("cards[a1b2][field1]", self.plan()["answers"])

    def test_checkbox_group_is_never_answered_by_the_script(self):
        fields = scan()
        data = profile()
        data["answers"].append({"question": "Which teams interest you?", **fact("Data")})
        plan = self.plan(data, fields)
        self.assertNotIn("cards[a1b2][field2]", plan["answers"])
        self.assertIn("cards[a1b2][field2]", unknown_by_id(plan))

    def test_unconfirmed_eeo_answers_are_never_planned(self):
        for status in ("unknown", "declined", "not_applicable"):
            with self.subTest(status=status):
                data = profile()
                data["compensation"]["eeo"] = {**fact({"gender": "Decline to self-identify"}), "status": status}
                plan = self.plan(data)
                self.assertNotIn("eeo[gender]", plan["answers"])
                self.assertEqual(unknown_by_id(plan)["eeo[gender]"]["reason"], "no_confirmed_fact")

    def test_eeo_value_without_a_matching_option_is_reported_not_guessed(self):
        data = profile()
        data["compensation"]["eeo"] = fact({"gender": "Prefer not to say"})
        plan = self.plan(data)
        self.assertNotIn("eeo[gender]", plan["answers"])
        self.assertEqual(unknown_by_id(plan)["eeo[gender]"]["reason"], "option_not_found")

    def test_resume_field_gets_the_selected_pdf_and_is_not_unknown(self):
        plan = self.plan(pdf=PDF)
        self.assertEqual(plan["answers"]["resume"], PDF)
        self.assertEqual(plan["resume"], "resume")
        self.assertNotIn("resume", unknown_by_id(plan))
        self.assertNotIn("resume", self.plan()["answers"])

    def test_resume_is_found_by_lever_name_not_label(self):
        fields = scan()
        field(fields, "resume")["label"] = "Attach your file"
        self.assertEqual(self.plan(fields=fields, pdf=PDF)["resume"], "resume")

    def test_already_answered_fields_without_a_fact_are_not_reported(self):
        fields = scan()
        field(fields, "cards[a1b2][field3]")["value"] = "Typed by the candidate in the held tab."
        self.assertNotIn("cards[a1b2][field3]", unknown_by_id(self.plan(fields=fields)))

    def test_lever_parses_the_resume_so_the_upload_goes_before_the_fill(self):
        self.assertIs(LeverDriver.upload_before_fill, True)
        self.assertIs(script_driver.ScriptDriver.upload_before_fill, False)


class LeverRunTests(unittest.TestCase):
    def test_run_scans_fills_planned_answers_and_reports_unknown_for_the_engine(self):
        client = Mock(spec=["evaluate_script"])
        client.evaluate_script.side_effect = [{"fields": scan()}, {"filled": list(answers()), "blocked": [], "uploads": []}]
        report = LeverDriver().run(client, "tab-1", profile(), JOB)
        (_, audit_script), audit_kw = client.evaluate_script.call_args_list[0]
        (_, fill_script), fill_kw = client.evaluate_script.call_args_list[1]
        self.assertEqual((audit_kw["operation"], fill_kw["operation"]), ("audit", "fill"))
        self.assertIn("AutoApplyLever.audit(document)", audit_script)
        sent = {key: {"value": value, "status": "confirmed"} for key, value in answers().items()}
        self.assertIn(json.dumps(sent, ensure_ascii=False), fill_script)
        self.assertEqual(report["driver"], "lever")
        self.assertEqual(report["filled"], list(answers()))
        self.assertEqual(report["status"], "needs_engine")
        self.assertEqual(
            [item["id"] for item in report["unknown"]],
            [
                "location",
                "org",
                "urls[GitHub]",
                "cards[a1b2][field1]",
                "cards[a1b2][field2]",
                "cards[a1b2][field3]",
                "comments",
                "consent[store]",
            ],
        )

    def test_captcha_or_closed_posting_holds_the_tab_without_filling(self):
        client = Mock(spec=["evaluate_script"])
        client.evaluate_script.return_value = {"blocked": [{"id": "page", "reason": "captcha"}]}
        report = LeverDriver().run(client, "tab-1", profile(), JOB, pdf=PDF)
        client.evaluate_script.assert_called_once()
        self.assertEqual(report["status"], "needs_manual")
        self.assertEqual(report["uploads"], [])


class LeverSubmitGuardTests(unittest.TestCase):
    def test_lever_scripts_contain_no_click_or_submit_call(self):
        driver = LeverDriver()
        for script in (driver.script("audit"), driver.script("fill", answers())):
            self.assertIn("AutoApplyLever", script)
            self.assertNotRegex(script, r"\.click\s*\(|requestSubmit|\.submit\s*\(")


class LeverRegistryAndBridgeTests(unittest.TestCase):
    def test_registry_returns_the_lever_driver(self):
        self.assertIsInstance(script_driver.driver_for("lever"), LeverDriver)

    def test_bridge_fills_lever_through_the_script_driver(self):
        client = Mock(spec=["new_page", "list_pages", "evaluate_script", "upload_file", "close_page"])
        client.list_pages.return_value = [{"id": "tab-1", "url": JOB["url"]}]
        client.evaluate_script.side_effect = [
            {"fields": scan()},
            {"filled": list(answers()), "blocked": [], "uploads": []},
        ]
        result = browser.BrowserBridge(client).fill("tab-1", profile(), JOB)
        self.assertEqual(result["driver"], "lever")
        self.assertIn("cards[a1b2][field3]", [item["id"] for item in result["unknown"]])
        client.close_page.assert_not_called()
        client.upload_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
