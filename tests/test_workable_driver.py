"""Workable script driver (plan aa-16).

Workable renders a React form where every question sits in a box marked with
its form-API id (``data-ui="firstname"``, ``data-ui="QA_100"``). The adapter
binds each control to that id. Yes/No questions are role=radio buttons, the
dropdown questions are comboboxes, and education/experience are entry lists
behind an Add control: all of those need a native click, so the driver carries
the confirmed value to the engine and never fills them by script.
tests/workable_form.json holds the synthetic DOM, the adapter's recorded scan
of it and the planned answers; adapters.test.cjs proves the adapter produces
that scan and fills those answers. Candidate and employer are invented.
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
from autoapply_local.profile import fact  # noqa: E402
from autoapply_local.workable_driver import WorkableDriver  # noqa: E402

FIXTURE = json.loads((RELEASE / "tests" / "workable_form.json").read_text(encoding="utf-8"))
JOB = {
    "id": "wk-1",
    "ats": "workable",
    "url": "https://apply.workable.com/exampleco/j/ABC123DEF4/apply",
    "company": "Example Co",
    "country": "US",
    "level": "internship",
    "start_date": "2027-06-01",
}
AUTH_QUESTION = "Are you legally authorized to work in the United States?"
SOURCE_QUESTION = "How did you hear about us?"
PDF = "C:/synthetic/Alex_Rivera_SWE.pdf"


def scan():
    return copy.deepcopy(FIXTURE["scan"])


def answers():
    return copy.deepcopy(FIXTURE["answers"])


def field(fields, key):
    return next(item for item in fields if item["id"] == key)


def scoped(question):
    return {
        "country": "US",
        "job_type": "internship",
        "employer": "Example Co",
        "question": question,
        "effective_at": "2027-06-01",
    }


def profile():
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
        },
        "education": [
            {
                "id": "edu-1",
                "school": fact("Springfield State University"),
                "degree": fact("Bachelor of Science"),
                "major": fact("Computer Science"),
            }
        ],
        "experience": [],
        "answers": [
            {"question": AUTH_QUESTION, **fact("Yes", scope=scoped(AUTH_QUESTION))},
            {"question": SOURCE_QUESTION, **fact("LinkedIn", scope=scoped(SOURCE_QUESTION))},
        ],
    }


def unknown_by_id(plan):
    return {item["id"]: item for item in plan["unknown"]}


class WorkablePlanTests(unittest.TestCase):
    def plan(self, data=None, fields=None, pdf=None):
        return WorkableDriver().plan(fields or scan(), data or profile(), JOB, pdf=pdf)

    def test_every_known_field_is_planned_from_confirmed_facts(self):
        self.assertEqual(self.plan()["answers"], answers())

    def test_unknown_fields_are_reported_with_reason_and_required_flag(self):
        self.assertEqual(
            {key: (item["reason"], item["required"]) for key, item in unknown_by_id(self.plan()).items()},
            {
                "headline": ("no_confirmed_fact", False),
                "address": ("no_confirmed_fact", False),
                "education": ("agent_interaction_required", False),
                "experience": ("no_confirmed_fact", False),
                "summary": ("no_confirmed_fact", False),
                "cover_letter": ("no_confirmed_fact", False),
                "QA_100": ("agent_interaction_required", True),
                "QA_101": ("no_confirmed_fact", True),
                "QA_102": ("agent_interaction_required", False),
                "QA_103": ("no_confirmed_fact", False),
                "QA_104": ("no_confirmed_fact", True),
                "gdpr": ("no_confirmed_fact", True),
            },
        )
        sponsorship = unknown_by_id(self.plan())["QA_101"]
        self.assertEqual(sponsorship["label"], field(scan(), "QA_101")["label"])
        self.assertEqual(sponsorship["options"], [{"value": "Yes", "label": "Yes"}, {"value": "No", "label": "No"}])

    def test_yes_no_buttons_and_dropdowns_carry_the_exact_answer_for_the_engine(self):
        unknown = unknown_by_id(self.plan())
        self.assertEqual((unknown["QA_100"]["type"], unknown["QA_100"]["value"]), ("radiogroup", "Yes"))
        self.assertEqual((unknown["QA_102"]["type"], unknown["QA_102"]["value"]), ("combobox", "LinkedIn"))

    def test_yes_no_answer_outside_the_options_is_reported_not_passed_on(self):
        data = profile()
        data["answers"][0]["value"] = "Maybe"
        unknown = unknown_by_id(self.plan(data))["QA_100"]
        self.assertEqual((unknown["reason"], unknown["value"]), ("option_not_found", "Maybe"))

    def test_education_entries_carry_only_confirmed_record_facts(self):
        data = profile()
        data["education"].append(
            {"id": "edu-2", "school": fact("Unconfirmed College", "unknown"), "degree": fact("Associate")}
        )
        data["education"][0]["major"] = fact("Computer Science", "unknown")
        self.assertEqual(
            unknown_by_id(self.plan(data))["education"]["value"],
            [{"school": "Springfield State University", "degree": "Bachelor of Science"}],
        )

    def test_experience_entries_need_a_confirmed_title_and_employer(self):
        data = profile()
        data["experience"] = [
            {"id": "exp-1", "title": fact("Software Intern"), "employer": fact("Example Labs")},
            {"id": "exp-2", "title": fact("Tutor"), "employer": fact("Somewhere", "unknown")},
        ]
        experience = unknown_by_id(self.plan(data))["experience"]
        self.assertEqual(experience["reason"], "agent_interaction_required")
        self.assertEqual(experience["value"], [{"title": "Software Intern", "employer": "Example Labs"}])

    def test_saved_entries_without_a_fact_are_not_reported(self):
        fields = scan()
        field(fields, "experience")["value"] = ["Software Intern Example Labs"]
        self.assertNotIn("experience", unknown_by_id(self.plan(fields=fields)))

    def test_sponsorship_is_never_inferred_without_an_exact_answer(self):
        plan = self.plan()
        self.assertNotIn("QA_101", plan["answers"])
        self.assertNotIn("value", unknown_by_id(plan)["QA_101"])

    def test_radio_buttons_are_never_answered_by_the_script(self):
        self.assertNotIn("QA_100", self.plan()["answers"])

    def test_checkbox_group_is_never_answered_by_the_script(self):
        data = profile()
        data["answers"].append({"question": "Which locations work for you?", **fact("Remote")})
        plan = self.plan(data)
        self.assertNotIn("QA_103", plan["answers"])
        self.assertIn("QA_103", unknown_by_id(plan))

    def test_consent_box_is_never_ticked_without_an_exact_answer(self):
        self.assertNotIn("gdpr", self.plan()["answers"])

    def test_names_map_by_their_workable_id_not_label(self):
        fields = scan()
        field(fields, "firstname")["label"] = "Given"
        field(fields, "lastname")["label"] = "Surname"
        planned = self.plan(fields=fields)["answers"]
        self.assertEqual((planned["firstname"], planned["lastname"]), ("Alex", "Rivera"))

    def test_unconfirmed_contact_facts_are_never_planned(self):
        data = profile()
        data["contact"]["phone"] = fact("+1 555 0100", "unknown")
        plan = self.plan(data)
        self.assertNotIn("phone", plan["answers"])
        self.assertEqual(unknown_by_id(plan)["phone"]["reason"], "no_confirmed_fact")

    def test_resume_field_gets_the_selected_pdf_and_is_not_unknown(self):
        plan = self.plan(pdf=PDF)
        self.assertEqual(plan["answers"]["resume"], PDF)
        self.assertEqual(plan["resume"], "resume")
        self.assertNotIn("resume", unknown_by_id(plan))
        self.assertNotIn("resume", self.plan()["answers"])

    def test_resume_is_found_by_workable_id_not_label(self):
        fields = scan()
        field(fields, "resume")["label"] = "Attach your file"
        self.assertEqual(self.plan(fields=fields, pdf=PDF)["resume"], "resume")

    def test_cover_letter_file_is_never_given_the_resume(self):
        fields = scan()
        field(fields, "cover_letter").update(type="file", label="Resume or cover letter")
        plan = self.plan(fields=fields, pdf=PDF)
        self.assertNotIn("cover_letter", plan["answers"])
        self.assertEqual(unknown_by_id(plan)["cover_letter"]["reason"], "no_confirmed_fact")

    def test_workable_parses_the_resume_so_the_upload_goes_before_the_fill(self):
        self.assertIs(WorkableDriver.upload_before_fill, True)

    def test_widget_types_of_other_drivers_are_unchanged(self):
        self.assertEqual(script_driver.ScriptDriver.widget_types, {"combobox", "listbox", "checkbox-group"})


class WorkableRunTests(unittest.TestCase):
    def test_run_scans_fills_planned_answers_and_reports_unknown_for_the_engine(self):
        client = Mock(spec=["evaluate_script"])
        client.evaluate_script.side_effect = [{"fields": scan()}, {"filled": list(answers()), "blocked": [], "uploads": []}]
        report = WorkableDriver().run(client, "tab-1", profile(), JOB)
        (_, audit_script), audit_kw = client.evaluate_script.call_args_list[0]
        (_, fill_script), fill_kw = client.evaluate_script.call_args_list[1]
        self.assertEqual((audit_kw["operation"], fill_kw["operation"]), ("audit", "fill"))
        self.assertIn("AutoApplyWorkable.audit(document)", audit_script)
        sent = {key: {"value": value, "status": "confirmed"} for key, value in answers().items()}
        self.assertIn(json.dumps(sent, ensure_ascii=False), fill_script)
        self.assertEqual(report["driver"], "workable")
        self.assertEqual(report["filled"], list(answers()))
        self.assertEqual(report["status"], "needs_engine")
        self.assertEqual(
            [item["id"] for item in report["unknown"]],
            [
                "headline",
                "address",
                "education",
                "experience",
                "summary",
                "cover_letter",
                "QA_100",
                "QA_101",
                "QA_102",
                "QA_103",
                "QA_104",
                "gdpr",
            ],
        )

    def test_closed_posting_or_captcha_holds_the_tab_without_filling(self):
        client = Mock(spec=["evaluate_script"])
        client.evaluate_script.return_value = {"blocked": [{"id": "page", "reason": "captcha"}]}
        report = WorkableDriver().run(client, "tab-1", profile(), JOB, pdf=PDF)
        client.evaluate_script.assert_called_once()
        self.assertEqual(report["status"], "needs_manual")
        self.assertEqual(report["uploads"], [])


class WorkableSubmitGuardTests(unittest.TestCase):
    def test_workable_scripts_contain_no_click_or_submit_call(self):
        driver = WorkableDriver()
        for script in (driver.script("audit"), driver.script("fill", answers())):
            self.assertIn("AutoApplyWorkable", script)
            self.assertNotRegex(script, r"\.click\s*\(|requestSubmit|\.submit\s*\(")


class WorkableRegistryAndBridgeTests(unittest.TestCase):
    def test_registry_returns_the_workable_driver(self):
        self.assertIsInstance(script_driver.driver_for("workable"), WorkableDriver)

    def test_bridge_fills_workable_through_the_script_driver(self):
        client = Mock(spec=["new_page", "list_pages", "evaluate_script", "upload_file", "close_page"])
        client.list_pages.return_value = [{"id": "tab-1", "url": JOB["url"]}]
        client.evaluate_script.side_effect = [
            {"fields": scan()},
            {"filled": list(answers()), "blocked": [], "uploads": []},
        ]
        result = browser.BrowserBridge(client).fill("tab-1", profile(), JOB)
        self.assertEqual(result["driver"], "workable")
        self.assertIn("QA_104", [item["id"] for item in result["unknown"]])
        client.close_page.assert_not_called()
        client.upload_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
