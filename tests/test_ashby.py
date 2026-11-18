"""Ashby driver: posting questions from a recorded GraphQL response, offline only."""

import copy
import json
import unittest
from pathlib import Path

import test_support  # noqa: F401  (puts the release root on sys.path)
from autoapply_local import ashby
from autoapply_local.profile import fact

FIXTURE = Path(__file__).with_name("ashby_posting.json")
URL = "https://jobs.ashbyhq.com/example-labs/0b7c1d4e-2f3a-4b5c-9d6e-7f8091a2b3c4"
JOB = {"url": URL, "company": "Example Labs", "country": "US", "level": "intern", "start_date": "2027-06"}


def recorded():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def profile(*answers):
    return {
        "schema_version": 1,
        "revision": 1,
        "contact": {"legal_name": fact("Alex Rivera"), "application_email": fact("alex.rivera@example.com")},
        "answers": list(answers),
    }


def scoped(question, value):
    scope = {
        "country": "US",
        "job_type": "intern",
        "employer": "Example Labs",
        "question": question,
        "effective_at": "2027-06",
    }
    return {"question": question, **fact(value, scope=scope)}


class JobReference(unittest.TestCase):
    def test_posting_url_and_application_url_share_the_reference(self):
        self.assertEqual(ashby.job_ref(URL), ("example-labs", "0b7c1d4e-2f3a-4b5c-9d6e-7f8091a2b3c4"))
        self.assertEqual(ashby.job_ref(URL + "/application?utm_source=x"), ashby.job_ref(URL))
        self.assertEqual(ashby.form_url(URL), URL + "/application")

    def test_other_hosts_and_incomplete_paths_are_rejected(self):
        for url in ("https://example.test/example-labs/abc", "https://jobs.ashbyhq.com/example-labs", "ftp://jobs.ashbyhq.com/a/b"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ashby.job_ref(url)

    def test_request_is_the_public_posting_query_without_sending_it(self):
        request = ashby.posting_request(URL)
        self.assertEqual(request["url"], "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting")
        body = json.loads(request["body"].decode("utf-8"))
        self.assertEqual(body["operationName"], "ApiJobPosting")
        self.assertEqual(
            body["variables"],
            {"organizationHostedJobsPageName": "example-labs", "jobPostingId": "0b7c1d4e-2f3a-4b5c-9d6e-7f8091a2b3c4"},
        )
        self.assertIn("applicationForm", body["query"])
        self.assertNotIn("Submit", body["query"] + request["url"])
        self.assertEqual(request["headers"], {"Content-Type": "application/json"})


class PostingFields(unittest.TestCase):
    def test_fields_keep_paths_kinds_required_flags_and_live_options(self):
        posting = ashby.parse_posting(recorded())
        self.assertEqual(posting["title"], "Software Engineer Intern, Summer 2027")
        self.assertIn("Build tools for Example Labs.", posting["description"])
        self.assertIn("- Python", posting["description"])
        by_id = {item["id"]: item for item in posting["fields"]}
        self.assertEqual(
            [item["id"] for item in posting["fields"]],
            ["_systemfield_name", "_systemfield_email", "resume", "9a1b", "7c2d", "5e3f", "3a4b", "1c5d", "8f7a"],
        )
        self.assertEqual(by_id["_systemfield_name"]["kind"], "text")
        self.assertTrue(by_id["_systemfield_name"]["required"])
        self.assertFalse(by_id["9a1b"]["required"])
        self.assertEqual(by_id["resume"]["kind"], "file")
        self.assertEqual(by_id["7c2d"]["kind"], "yesno")
        self.assertEqual(by_id["7c2d"]["options"], ["Yes", "No"])
        self.assertEqual(by_id["5e3f"]["options"], ["Remote", "Austin"])
        self.assertEqual(by_id["5e3f"]["desc"], "Pick one.")
        self.assertEqual(by_id["3a4b"]["kind"], "multi")
        self.assertEqual(by_id["1c5d"]["section"], "form")
        self.assertEqual(by_id["8f7a"]["section"], "survey")

    def test_length_limits_come_from_the_description_and_ignore_minimums(self):
        by_id = {item["id"]: item for item in ashby.parse_posting(recorded())["fields"]}
        self.assertEqual(by_id["1c5d"]["max_words"], 150)
        self.assertNotIn("max", by_id["1c5d"])
        self.assertEqual(by_id["8f7a"]["max"], 500)
        self.assertEqual(ashby.length_limits("150-300 words"), {"max_words": 300})
        self.assertEqual(ashby.length_limits("at least 50 words, under 200 words"), {"max_words": 200})
        self.assertEqual(ashby.length_limits("no limit"), {})

    def test_unknown_field_type_fails_closed(self):
        raw = recorded()
        raw["data"]["jobPosting"]["applicationForm"]["sections"][0]["fieldEntries"][0]["field"]["type"] = "Signature"
        with self.assertRaisesRegex(ValueError, "Signature"):
            ashby.parse_posting(raw)

    def test_closed_posting_and_graphql_errors_are_distinct(self):
        with self.assertRaises(LookupError):
            ashby.parse_posting({"data": {"jobPosting": None}})
        with self.assertRaisesRegex(ValueError, "rate"):
            ashby.parse_posting({"errors": [{"message": "rate limited"}]})


class AnswerPlan(unittest.TestCase):
    def test_known_fields_come_from_confirmed_facts_and_the_rest_are_reported(self):
        fields = ashby.parse_posting(recorded())["fields"]
        plan = ashby.plan_answers(fields, profile(), JOB)
        self.assertEqual(plan["known"]["_systemfield_name"]["value"], "Alex Rivera")
        self.assertEqual(plan["known"]["_systemfield_email"]["value"], "alex.rivera@example.com")
        self.assertEqual(plan["uploads"], ["resume"])
        self.assertEqual(plan["unknown"], ["7c2d", "5e3f", "1c5d"])
        self.assertNotIn("9a1b", plan["known"])

    def test_yes_no_answer_needs_an_exact_question_scoped_confirmation(self):
        fields = ashby.parse_posting(recorded())["fields"]
        question = "Will you now or in the future require visa sponsorship?"
        plan = ashby.plan_answers(fields, profile(scoped(question, True)), JOB)
        self.assertEqual(plan["known"]["7c2d"]["value"], "Yes")
        self.assertNotIn("7c2d", plan["unknown"])
        other_employer = dict(JOB, company="Another Co")
        self.assertIn("7c2d", ashby.plan_answers(fields, profile(scoped(question, True)), other_employer)["unknown"])

    def test_choice_answers_outside_the_live_options_stay_unknown(self):
        fields = ashby.parse_posting(recorded())["fields"]
        plan = ashby.plan_answers(fields, profile(scoped("Which office do you prefer?", "Old office")), JOB)
        self.assertNotIn("5e3f", plan["known"])
        self.assertIn("5e3f", plan["unknown"])
        plan = ashby.plan_answers(fields, profile(scoped("Which office do you prefer?", "Austin")), JOB)
        self.assertEqual(plan["known"]["5e3f"]["value"], "Austin")
        question = "Will you now or in the future require visa sponsorship?"
        bad = ashby.plan_answers(fields, profile(scoped(question, "Maybe")), JOB)
        self.assertIn("7c2d", bad["unknown"])


class BrowserScript(unittest.TestCase):
    def test_bridge_loads_the_ashby_adapter_after_the_common_primitives(self):
        from unittest.mock import Mock

        from autoapply_local.browser import BrowserBridge

        bridge = BrowserBridge(Mock(spec=["new_page", "list_pages", "evaluate_script", "upload_file", "close_page"]))
        script = bridge._script("fill", "ashby", {"_systemfield_name": {"value": "Alex Rivera", "status": "confirmed"}})
        self.assertLess(script.index("root.AutoApplyDOM = api"), script.index("root.AutoApplyAshby = api"))
        self.assertIn("globalThis.AutoApplyAshby.fill(document,", script)
        audit = bridge._script("audit", "ashby")
        self.assertIn("globalThis.AutoApplyAshby.audit(document)", audit)
        # Readback binds each field through the adapter, so Yes/No groups count as observed.
        self.assertIn("globalThis.AutoApplyAshby.resolve(document, field.id)", audit)


class Driver(unittest.TestCase):
    SCAN = {
        "fields": [
            {"id": "_systemfield_name", "label": "Name", "type": "text", "required": True, "value": "", "options": []},
            {"id": "_systemfield_email", "label": "Email", "type": "email", "required": True, "value": "", "options": []},
            {"id": "_systemfield_resume", "label": "Resume", "type": "file", "required": True, "value": "", "options": []},
            {"id": "yesno:1", "label": "Will you now or in the future require visa sponsorship?", "type": "yesno",
             "required": True, "value": "", "options": [{"value": "Yes", "label": "Yes"}, {"value": "No", "label": "No"}]},
            {"id": "yesno:2", "label": "Are you open to relocation?", "type": "yesno", "required": False, "value": "",
             "options": [{"value": "Yes", "label": "Yes"}, {"value": "No", "label": "No"}]},
        ],
        "errors": [],
    }

    def test_driver_is_registered_and_its_scripts_cannot_click(self):
        from autoapply_local import script_driver
        from autoapply_local.ashby_driver import AshbyDriver

        driver = script_driver.driver_for("ashby")
        self.assertIsInstance(driver, AshbyDriver)
        for operation in ("audit", "fill"):
            self.assertIsNone(script_driver.CLICKS.search(driver.script(operation, {})))

    def test_run_fills_system_fields_and_hands_yes_no_to_a_native_click(self):
        from unittest.mock import Mock

        from autoapply_local.ashby_driver import AshbyDriver

        question = "Will you now or in the future require visa sponsorship?"
        client = Mock(spec=["evaluate_script"])
        client.evaluate_script.side_effect = [
            copy.deepcopy(self.SCAN),
            {
                "filled": ["_systemfield_name", "_systemfield_email"],
                "blocked": [{"id": "yesno:1", "reason": "native_click_required", "option": "Yes"}],
                "uploads": [{"id": "_systemfield_resume", "path": "resume.pdf"}],
            },
        ]
        result = AshbyDriver().run(client, "tab-1", profile(scoped(question, True)), JOB, pdf="resume.pdf")
        planned = json.loads(
            client.evaluate_script.call_args_list[1].args[1].split("globalThis.AutoApplyAshby.fill(document, ", 1)[1].rsplit(");", 1)[0]
        )
        self.assertEqual(planned["_systemfield_name"]["value"], "Alex Rivera")
        self.assertEqual(planned["_systemfield_email"]["value"], "alex.rivera@example.com")
        self.assertIs(planned["yesno:1"]["value"], True)
        self.assertNotIn("yesno:2", planned)
        self.assertEqual(result["resume_field"], "_systemfield_resume")
        unknown = {item["id"]: item for item in result["unknown"]}
        self.assertEqual(unknown["yesno:1"]["reason"], "native_click_required")
        self.assertEqual(unknown["yesno:1"]["option"], "Yes")
        self.assertEqual(unknown["yesno:2"]["reason"], "no_confirmed_fact")
        self.assertFalse(unknown["yesno:2"]["required"])
        self.assertEqual(result["status"], "needs_engine")


    def test_run_never_hands_a_submit_control_to_the_engine(self):
        from unittest.mock import Mock

        from autoapply_local.ashby_driver import AshbyDriver

        question = "Will you now or in the future require visa sponsorship?"
        client = Mock(spec=["evaluate_script"])
        client.evaluate_script.side_effect = [
            copy.deepcopy(self.SCAN),
            {
                "filled": ["_systemfield_name", "_systemfield_email"],
                "blocked": [{"id": "yesno:1", "reason": "submit_control"}],
                "uploads": [],
            },
        ]
        result = AshbyDriver().run(client, "tab-1", profile(scoped(question, True)), JOB, pdf="resume.pdf")
        self.assertNotIn("yesno:1", {item["id"] for item in result["unknown"]})
        self.assertIn({"id": "yesno:1", "reason": "submit_control"}, result["blocked"])
        self.assertEqual(result["status"], "needs_manual")

if __name__ == "__main__":
    unittest.main()
