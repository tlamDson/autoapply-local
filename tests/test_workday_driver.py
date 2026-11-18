"""Workday script driver (plan aa-12): each form step, the resume upload and the Review stop.

tests/workday_flow.json holds what ``workday_steps.where()`` and the Workday
adapter's audit return on each synthetic page; workday.test.cjs proves the
adapters produce those shapes. ``FakeWorkday`` plays the held tab: it answers
the scripts the driver sends and moves one step on each Next. The candidate,
employer and resume are invented.
"""

import copy
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from autoapply_local import browser, script_driver, workday_steps  # noqa: E402
from autoapply_local.profile import fact  # noqa: E402
from autoapply_local.workday_driver import WorkdayDriver  # noqa: E402

FLOW = json.loads((RELEASE / "tests" / "workday_flow.json").read_text(encoding="utf-8"))
JOB = {
    "id": "wd-1",
    "ats": "workday",
    "url": "https://exampleco.wd5.myworkdayjobs.com/en-US/careers/job/Software-Engineer-Intern_R-0001/apply",
    "company": "Example Co",
    "country": "US",
    "level": "internship",
    "start_date": "2027-06-01",
}
PREVIOUS = "Have you previously worked for Example Co?"
SPONSOR = "Will you now or in the future require sponsorship for employment visa status?"
PDF = "C:/synthetic/Alex_Rivera_SWE.pdf"
CLICK = re.compile(r"\.click\s*\(|requestSubmit|\.submit\s*\(")
STEP_CALL = re.compile(r"\nreturn await globalThis\.AutoApplyWorkdaySteps\.(\w+)\(document(?:, (.*))?\);\n\}$", re.S)
FILL_CALL = re.compile(r"\nreturn await globalThis\.AutoApplyWorkday\.fill\(document, (.*)\);\n\}$", re.S)


def scoped(question, value):
    scope = {
        "country": "US",
        "job_type": "internship",
        "employer": "Example Co",
        "question": question,
        "effective_at": "2027-06-01",
    }
    return {"question": question, **fact(value, scope=scope)}


def profile():
    return {
        "schema_version": 1,
        "revision": 1,
        "contact": {
            "legal_name": fact("Alex Rivera"),
            "first_name": fact("Alex"),
            "last_name": fact("Rivera"),
            "application_email": fact("alex.rivera@example.com"),
            "phone": fact("555 0100"),
            "street": fact("1 Example Way"),
            "city": fact("Springfield"),
            "postcode": fact("00001"),
            "linkedin": fact("https://www.linkedin.com/in/alex-rivera-example"),
        },
        "answers": [scoped(PREVIOUS, "No"), scoped(SPONSOR, "Yes")],
    }


def steps():
    return copy.deepcopy(FLOW["steps"])


def page(name):
    return copy.deepcopy(FLOW["pages"][name])


class FakeWorkday:
    """A held Workday tab that answers the driver's scripts and moves on Next."""

    def __init__(self, pages=None, typed=True, next_errors=None, upload=True):
        self.pages = pages if pages is not None else steps() + [{"where": page("review"), "fields": []}]
        self.index = 0
        self.typed = typed
        self.next_errors = next_errors or {}
        self.calls, self.fills, self.uploads, self.order = [], [], [], []
        if upload:
            self.upload_file = self._upload

    @property
    def current(self):
        return self.pages[self.index]

    def _upload(self, tab_id, path):
        self.uploads.append((tab_id, path))
        self.order.append(("upload", self.current["where"]["step"]["title"]))

    def evaluate_script(self, tab_id, script, operation="audit"):
        self.calls.append(operation)
        step = STEP_CALL.search(script)
        if step:
            self.assert_no_submit(script)
            name, args = step.group(1), json.loads("[" + (step.group(2) or "") + "]")
            self.order.append((name, *args))
            if operation != "step":
                raise AssertionError("Workday step scripts use the step operation")
            if name == "where":
                return copy.deepcopy(self.current["where"])
            if name == "typeText":
                ids = list(args[0])
                return {"typed": ids, "failed": []} if self.typed else {"typed": [], "failed": ids}
            if name == "press" and args[0] == "next":
                title = self.current["where"]["step"]["title"]
                if title in self.next_errors:
                    where = copy.deepcopy(self.current["where"])
                    where["errors"] = self.next_errors[title]
                    return {"pressed": True, "moved": False, "where": where}
                self.index += 1
                return {"pressed": True, "moved": True, "where": copy.deepcopy(self.current["where"])}
            raise AssertionError(f"unexpected step call {name}")
        if "globalThis.AutoApplyWorkday.audit(document)" in script:
            self.order.append(("audit",))
            return {"fields": copy.deepcopy(self.current["fields"])}
        fill = FILL_CALL.search(script)
        if fill:
            answers = json.loads(fill.group(1))
            self.fills.append(answers)
            self.order.append(("fill",))
            fields = {item["id"]: item for item in self.current["fields"]}
            result = {"filled": [], "blocked": [], "uploads": []}
            for key, supplied in answers.items():
                if key.startswith("name--"):
                    result["blocked"].append({"id": key, "reason": "agent_interaction_required"})
                elif fields[key]["type"] == "file":
                    result["uploads"].append({"id": key, "path": supplied["value"]})
                else:
                    result["filled"].append(key)
            return result
        raise AssertionError("unexpected script")

    @staticmethod
    def assert_no_submit(script):
        if re.search(r"requestSubmit|\.submit\s*\(", script):
            raise AssertionError("a Workday script may never submit")


def titles(report):
    return [item["title"] for item in report["steps"]]


def by_id(items):
    return {item["id"]: item for item in items}


class WorkdayPlanTests(unittest.TestCase):
    def plan(self, index=0, data=None, pdf=None):
        return WorkdayDriver().plan(steps()[index]["fields"], data or profile(), JOB, pdf=pdf)

    def test_my_information_fields_are_planned_from_confirmed_facts(self):
        self.assertEqual(
            self.plan()["answers"],
            {
                "candidateIsPreviousWorker": "false",
                "name--legalName--firstName": "Alex",
                "name--legalName--lastName": "Rivera",
                "address--addressLine1": "1 Example Way",
                "address--city": "Springfield",
                "address--postalCode": "00001",
                "phoneNumber--phoneNumber": "555 0100",
            },
        )
        self.assertEqual(self.plan()["unknown"], [])

    def test_unconfirmed_contact_facts_are_reported_not_filled(self):
        data = profile()
        data["contact"]["street"] = fact(None, "unknown")
        data["contact"]["postcode"] = fact("00001", "declined")
        plan = self.plan(data=data)
        self.assertNotIn("address--addressLine1", plan["answers"])
        self.assertEqual(
            {key: item["reason"] for key, item in by_id(plan["unknown"]).items()},
            {"address--addressLine1": "no_confirmed_fact", "address--postalCode": "no_confirmed_fact"},
        )

    def test_an_empty_source_list_needs_the_engine(self):
        fields = steps()[0]["fields"]
        fields[0]["value"] = []
        plan = WorkdayDriver().plan(fields, profile(), JOB)
        self.assertEqual(by_id(plan["unknown"])["source--source"]["reason"], "no_confirmed_fact")

    def test_the_only_file_input_is_the_resume_and_gets_the_selected_pdf(self):
        plan = self.plan(1, pdf=PDF)
        self.assertEqual(plan["resume"], "autoapply-field-1")
        self.assertEqual(plan["answers"]["autoapply-field-1"], PDF)
        self.assertEqual(plan["answers"]["socialNetworkAccounts--linkedInAccount"], "https://www.linkedin.com/in/alex-rivera-example")

    def test_a_cover_letter_upload_is_not_the_resume(self):
        fields = [{"id": "f", "label": "Cover Letter", "type": "file", "required": False, "value": "", "options": []}]
        plan = WorkdayDriver().plan(fields, profile(), JOB, pdf=PDF)
        self.assertIsNone(plan["resume"])
        self.assertNotIn("f", plan["answers"])

    def test_sponsorship_is_answered_only_from_the_exact_scoped_answer(self):
        self.assertEqual(self.plan(2)["answers"], {"autoapply-field-3": "yes"})
        data = profile()
        data["answers"] = [scoped(PREVIOUS, "No")]
        plan = self.plan(2, data=data)
        self.assertEqual(plan["answers"], {})
        self.assertEqual(by_id(plan["unknown"])["autoapply-field-3"]["reason"], "no_confirmed_fact")


class WorkdayRunTests(unittest.TestCase):
    def test_every_step_is_filled_and_the_run_stops_on_review(self):
        tab = FakeWorkday()
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF)
        self.assertEqual(report["driver"], "workday")
        self.assertEqual(report["status"], "filled")
        self.assertEqual(titles(report), ["My Information", "My Experience", "Application Questions", "Voluntary Disclosures"])
        self.assertEqual(report["review"], {"index": 6, "total": 6, "title": "Review"})
        self.assertEqual([entry for entry in tab.order if entry[0] == "press"], [("press", "next")] * 4)
        self.assertEqual(tab.order[-1], ("where",))
        self.assertEqual(tab.index, 4)

    def test_names_blocked_by_the_setter_are_typed_with_insert_text(self):
        tab = FakeWorkday()
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF)
        typed = [entry for entry in tab.order if entry[0] == "typeText"]
        self.assertEqual(typed, [("typeText", {"name--legalName--firstName": "Alex", "name--legalName--lastName": "Rivera"})])
        self.assertIn("name--legalName--firstName", report["filled"])
        self.assertNotIn("name--legalName--firstName", by_id(report["unknown"]))
        self.assertNotIn("name--legalName--firstName", by_id(report["blocked"]))

    def test_names_that_do_not_take_stop_the_run_for_the_engine(self):
        tab = FakeWorkday(typed=False)
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF)
        self.assertEqual(report["status"], "needs_engine")
        self.assertEqual(titles(report), ["My Information"])
        self.assertEqual(by_id(report["unknown"])["name--legalName--lastName"]["reason"], "agent_interaction_required")
        self.assertNotIn(("press", "next"), tab.order)

    def test_the_resume_is_uploaded_on_my_experience_before_next(self):
        tab = FakeWorkday()
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF)
        self.assertEqual(tab.uploads, [("tab-1", PDF)])
        at = tab.order.index(("upload", "My Experience"))
        self.assertEqual(tab.order[at + 1], ("press", "next"))
        self.assertEqual(report["uploads"], [{"id": "autoapply-field-1", "path": PDF, "status": "uploaded"}])
        self.assertEqual(report["resume_field"], "autoapply-field-1")

    def test_the_same_resume_already_attached_is_not_uploaded_again(self):
        pages = steps() + [{"where": page("review"), "fields": []}]
        pages[1]["fields"][0]["value"] = "Alex_Rivera_SWE.pdf"
        tab = FakeWorkday(pages=pages)
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF)
        self.assertEqual(tab.uploads, [])
        self.assertEqual(report["status"], "filled")

    def test_another_attached_resume_holds_the_tab(self):
        pages = steps() + [{"where": page("review"), "fields": []}]
        pages[1]["fields"][0]["value"] = "Old_Resume.pdf"
        tab = FakeWorkday(pages=pages)
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF)
        self.assertEqual(report["status"], "needs_manual")
        self.assertIn({"id": "autoapply-field-1", "reason": "other_resume_attached"}, report["blocked"])
        self.assertEqual(tab.uploads, [])
        self.assertEqual(tab.index, 1)

    def test_a_client_that_cannot_upload_holds_the_tab_on_my_experience(self):
        tab = FakeWorkday(upload=False)
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF)
        self.assertEqual(report["status"], "needs_manual")
        self.assertIn({"id": "autoapply-field-1", "reason": "resume_upload_required"}, report["blocked"])
        self.assertEqual(tab.index, 1)

    def test_a_required_unknown_question_stops_on_its_step_for_the_engine(self):
        data = profile()
        data["answers"] = [scoped(PREVIOUS, "No")]
        tab = FakeWorkday()
        report = WorkdayDriver().run(tab, "tab-1", data, JOB, pdf=PDF)
        self.assertEqual(report["status"], "needs_engine")
        self.assertEqual(titles(report), ["My Information", "My Experience", "Application Questions"])
        self.assertEqual(tab.index, 2)
        unknown = by_id(report["unknown"])["autoapply-field-3"]
        self.assertEqual((unknown["required"], unknown["step"]), (True, "Application Questions"))

    def test_validation_errors_after_next_stop_the_run(self):
        tab = FakeWorkday(next_errors={"My Information": ["Error: City is required"]})
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF)
        self.assertEqual(report["status"], "needs_engine")
        self.assertIn({"id": "page", "reason": "validation_error", "errors": ["Error: City is required"]}, report["blocked"])
        self.assertEqual(tab.index, 0)

    def test_a_held_tab_on_review_is_not_touched(self):
        tab = FakeWorkday(pages=[{"where": page("review"), "fields": []}])
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF)
        self.assertEqual(report["status"], "filled")
        self.assertEqual(tab.order, [("where",)])

    def test_sign_in_pages_without_an_account_helper_hold_the_tab_before_any_fill(self):
        for name, reason in (
            ("sign_in", "sign_in_required"),
            ("create_account", "sign_in_required"),
            ("verify_code", "sign_in_required"),
            ("job", "sign_in_required"),
            ("captcha", "captcha"),
            ("applied", "already_applied"),
            ("unknown", "form_not_readable"),
        ):
            with self.subTest(page=name):
                tab = FakeWorkday(pages=[{"where": page(name), "fields": []}])
                report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF)
                self.assertEqual(report["status"], "needs_manual")
                self.assertEqual(report["blocked"], [{"id": "page", "reason": reason}])
                self.assertEqual(tab.order, [("where",)])

    def test_the_account_helper_signs_in_before_the_first_step(self):
        tab = FakeWorkday()
        account = Mock()
        account.ensure_signed_in.side_effect = lambda job: tab.order.append(("account",)) or {"status": "signed_in", "page": "form"}
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF, account=account)
        account.ensure_signed_in.assert_called_once_with(JOB)
        self.assertEqual(tab.order[0], ("account",))
        self.assertEqual(report["account"], {"status": "signed_in", "page": "form"})
        self.assertEqual(report["status"], "filled")

    def test_a_blocked_account_step_holds_the_tab_without_touching_the_form(self):
        tab = FakeWorkday()
        account = Mock()
        account.ensure_signed_in.return_value = {"status": "blocked", "reason": "verification_code_not_found", "page": "verify_code"}
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF, account=account)
        self.assertEqual(report["status"], "needs_manual")
        self.assertEqual(report["blocked"], [{"id": "page", "reason": "verification_code_not_found"}])
        self.assertEqual(tab.calls, [])

    def test_an_account_already_applied_holds_the_tab(self):
        tab = FakeWorkday()
        account = Mock()
        account.ensure_signed_in.return_value = {"status": "already_applied", "page": "applied"}
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB, account=account)
        self.assertEqual(report["blocked"], [{"id": "page", "reason": "already_applied"}])
        self.assertEqual(tab.calls, [])

    def test_a_run_that_never_reaches_review_stops_after_the_step_limit(self):
        looping = [steps()[3]] * 30
        tab = FakeWorkday(pages=looping)
        report = WorkdayDriver().run(tab, "tab-1", profile(), JOB)
        self.assertEqual(report["status"], "needs_manual")
        self.assertIn({"id": "page", "reason": "too_many_steps"}, report["blocked"])
        self.assertEqual(len(report["steps"]), WorkdayDriver.max_steps)


class WorkdaySubmitGuardTests(unittest.TestCase):
    def test_fill_and_audit_scripts_contain_no_click_or_submit_call(self):
        driver = WorkdayDriver()
        for script in (driver.script("audit"), driver.script("fill", {"x": {"value": "y", "status": "confirmed"}})):
            self.assertIn("AutoApplyWorkday", script)
            self.assertNotRegex(script, CLICK)

    def test_step_scripts_never_submit(self):
        for script in (
            workday_steps.script("where"),
            workday_steps.press_script("sign_in"),
            workday_steps.press_script("next", FLOW["steps"][0]["where"]),
            workday_steps.script("typeText", {"a": "b"}),
        ):
            self.assertNotRegex(script, r"requestSubmit|\.submit\s*\(")
            self.assertRegex(script, STEP_CALL)

    def test_only_fixed_actions_can_be_pressed(self):
        self.assertEqual(
            set(workday_steps.ACTIONS),
            {"apply", "apply_manually", "sign_in_with_email", "create_account_link", "sign_in", "create_account", "verify", "next"},
        )
        for action in ("submit", "Submit", "review", "next; document.forms[0].submit()"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "action"):
                workday_steps.press_script(action)
        with self.assertRaisesRegex(ValueError, "call"):
            workday_steps.script("press", "submit")

    def test_next_is_refused_on_review_and_off_a_form_step(self):
        last = {"page": "form", "step": {"index": 6, "total": 6, "title": "Voluntary Disclosures"}}
        for where in (page("review"), last, page("sign_in"), None, {"page": "form", "step": None}):
            with self.subTest(where=where), self.assertRaisesRegex(ValueError, "Next"):
                workday_steps.press_script("next", where)

    def test_step_scripts_cannot_go_through_the_engine_readback(self):
        self.assertNotIn("step", browser.EVALUATE_SCHEMAS)

    def test_the_driver_never_presses_anything_but_next(self):
        tab = FakeWorkday()
        WorkdayDriver().run(tab, "tab-1", profile(), JOB, pdf=PDF)
        self.assertEqual({entry[1] for entry in tab.order if entry[0] == "press"}, {"next"})


class WorkdayRegistryAndBridgeTests(unittest.TestCase):
    def test_registry_returns_the_workday_driver(self):
        self.assertIsInstance(script_driver.driver_for("workday"), WorkdayDriver)

    def test_bridge_fills_workday_through_the_script_driver(self):
        tab = FakeWorkday(upload=False)
        client = Mock(spec=["new_page", "list_pages", "evaluate_script", "close_page"])
        client.list_pages.return_value = [{"id": "tab-1", "url": JOB["url"]}]
        client.evaluate_script.side_effect = tab.evaluate_script
        result = browser.BrowserBridge(client).fill("tab-1", profile(), JOB)
        self.assertEqual(result["driver"], "workday")
        self.assertEqual(result["status"], "filled")
        client.close_page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
