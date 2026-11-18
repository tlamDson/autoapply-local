"""Rippling script driver: resume first, Rippling parse, confirmed facts on top, form held.

Offline: a fake MCP client answers each adapter call from a synthetic form
model; no Chrome, ATS, engine or network is used. People and links are invented.
"""

import hashlib
import json
import re
from unittest.mock import Mock

from test_support import ReleaseCase, candidate, fact, job

from autoapply_local.browser import EVALUATE_SCHEMAS
from autoapply_local.engines import validate_schema

ACTION = re.compile(r"globalThis\.AutoApplyRippling\.(\w+)\(document(?:, (.*))?\);\s*\}\s*$", re.S)


def field(name, label, required=False, value="", kind="text", options=()):
    return {"id": name, "label": label, "required": required, "value": value, "type": kind, "options": list(options)}


SPONSOR = "customQuestions.bbb222.answer"
HEARD = "customQuestions.aaa111.answer"


def form_fields(**values):
    fields = [
        field("first_name", "First name", True),
        field("last_name", "Last name", True),
        field("email", "Email", True),
        field("phone_number", "Phone number", True),
        field("linkedin_link", "LinkedIn"),
        field("website_link", "Website"),
        field("current_company", "Current company"),
        field("resume", "Resume", True, [], "file"),
        field("location", "Location", True),
        field(HEARD, "How did you hear about us?"),
        field(SPONSOR, "Will you need visa sponsorship?", True, "", "choice", [{"value": "y", "label": "Yes"}]),
    ]
    for item in fields:
        if item["id"] in values:
            item["value"] = values[item["id"]]
    return fields


class FakeForm:
    """Answers adapter calls the way the page would, and records their order."""

    def __init__(self, ready_after=1, formsy=True, location=None, keep_empty=(), late_parse=0, scan_overwrite=None, check_overwrite=False):
        self.calls, self.ready_polls, self.ready_after = [], 0, ready_after
        # How many fills a slow parse still overwrites afterwards (-1: every fill).
        self.late_parse = late_parse
        # The parse lands just before the n-th scan, or right after the first clean check.
        self.scan_overwrite, self.scans, self.check_overwrite = scan_overwrite, 0, check_overwrite
        self.formsy, self.location_result, self.keep_empty = formsy, location, set(keep_empty)
        self.values = {"resume": []}
        self.client = Mock(spec=["evaluate_script", "upload_file", "list_pages"])
        self.client.evaluate_script.side_effect = self.evaluate
        self.client.upload_file.side_effect = self.upload

    def upload(self, tab, path):
        self.calls.append(("upload", path))
        self.values["resume"] = [path.replace("\\", "/").rsplit("/", 1)[-1]]
        # Rippling's own parse writes what it read from the PDF, links included.
        self.values.update(first_name="Parsed", linkedin_link="https://parsed.example/wrong")

    def evaluate(self, tab, function, *, operation="audit"):
        match = ACTION.search(function)
        assert match, "every script ends in one adapter call"
        name, payload = match.group(1), json.loads(match.group(2) or "null")
        assert "AutoApplyDOM" in function, "common.cjs is loaded before the Rippling adapter"
        self.calls.append((name, payload))
        expected = "audit" if name == "modelScan" else "fill"
        assert operation == expected, f"{name} must be read back as {expected}, not {operation}"
        result = self.answer(name, payload)
        # The real MCP client rejects any readback outside its contract.
        validate_schema(result, EVALUATE_SCHEMAS[operation])
        return result

    def answer(self, name, payload):
        fill = lambda filled=(), blocked=(): {"filled": list(filled), "blocked": list(blocked), "uploads": []}
        if name == "modelReady":
            self.ready_polls += 1
            if self.ready_polls >= self.ready_after:
                return fill(["resume"])
            return fill(blocked=[{"id": "resume", "reason": "resume_parse_pending"}])
        if name == "modelScan":
            self.scans += 1
            if self.scans == self.scan_overwrite:
                self.values["linkedin_link"] = "https://parsed.example/wrong"
            fields = form_fields(**self.values) if self.formsy else []
            page = {"index": 1, "total": 1, "title": "Example", "formsy": self.formsy, "valid": not self.missing()}
            return {"fields": fields, "errors": [], "text": "", "url": "https://ats.rippling.com/x", "attachments": [], "page": page, "required_unknown": []}
        if name == "modelFill":
            filled = [key for key in payload if key not in self.keep_empty]
            for key in filled:
                self.values[key] = self.stored(key, payload[key]["value"])
            if self.late_parse:
                self.late_parse -= 1
                self.values["linkedin_link"] = "https://parsed.example/wrong"
            return fill(filled)
        if name == "modelCheck":
            same = [key for key in payload if self.values.get(key) == self.stored(key, payload[key]["value"])]
            changed = [{"id": key, "reason": "value_changed"} for key in payload if key not in same]
            if self.check_overwrite and not changed:
                self.check_overwrite = False
                self.values["linkedin_link"] = "https://parsed.example/wrong"
            return fill(same, changed)
        if name == "location":
            if self.location_result:
                return fill(blocked=[{"id": "location", "reason": self.location_result}])
            self.values["location"] = payload["query"] + ", USA"
            return fill(["location"])
        raise AssertionError("unexpected adapter call " + name)

    @staticmethod
    def stored(key, value):
        """What the page model holds for an answer: a choice keeps its option value."""
        options = next((item["options"] for item in form_fields() if item["id"] == key), [])
        return next((o["value"] for o in options if value in (o["value"], o["label"])), value)

    def missing(self):
        return [item["id"] for item in form_fields(**self.values) if item["required"] and not item["value"]]

    def names(self):
        return [call[0] for call in self.calls]


class Base(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.rp = self.module("rippling_driver")
        self.profile = candidate()
        self.profile["contact"].update(
            first_name=fact("Alex"),
            last_name=fact("Rivera"),
            phone=fact("+1 202 555 0100"),
            linkedin=fact("https://www.linkedin.com/in/alex-rivera-example"),
            portfolio=fact(None, "unknown"),
            city=fact("Example City"),
            state=fact("CA"),
        )
        self.profile["answers"].append(
            {
                "question": "Will you need visa sponsorship?",
                **fact(
                    "Yes",
                    scope={
                        "country": "US",
                        "job_type": "internship",
                        "employer": "Example Labs",
                        "question": "Will you need visa sponsorship?",
                        "effective_at": None,
                    },
                ),
            }
        )
        self.job = job(ats="rippling", url="https://ats.rippling.com/example/jobs/0000-1111/apply")
        self.pdf = self.root / "Alex_Rivera_Resume.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n% synthetic\n%%EOF")


class PlanTests(Base):
    def test_basic_fields_come_from_confirmed_contact_facts_only(self):
        plan = self.rp.plan(self.profile, form_fields(), self.job)
        values = {key: answer["value"] for key, answer in plan["answers"].items()}
        self.assertEqual(
            values,
            {
                "first_name": "Alex",
                "last_name": "Rivera",
                "email": "alex@example.invalid",
                "phone_number": "+1 202 555 0100",
                "linkedin_link": "https://www.linkedin.com/in/alex-rivera-example",
                SPONSOR: "Yes",
            },
        )
        self.assertTrue(all(answer["status"] == "confirmed" for answer in plan["answers"].values()))
        unknown = {item["id"]: item for item in plan["unknown"]}
        self.assertEqual(set(unknown), {"website_link", "current_company", HEARD})
        self.assertFalse(unknown[HEARD]["required"])
        self.assertEqual(unknown[HEARD]["label"], "How did you hear about us?")
        self.assertEqual(plan["location"], "Example City, CA")
        self.assertNotIn("resume", plan["answers"])

    def test_portfolio_then_github_fill_the_website_field(self):
        self.profile["contact"]["github"] = fact("https://github.com/alex-rivera-example")
        plan = self.rp.plan(self.profile, form_fields(), self.job)
        self.assertEqual(plan["answers"]["website_link"]["value"], "https://github.com/alex-rivera-example")
        self.profile["contact"]["portfolio"] = fact("https://alex.example.invalid")
        plan = self.rp.plan(self.profile, form_fields(), self.job)
        self.assertEqual(plan["answers"]["website_link"]["value"], "https://alex.example.invalid")

    def test_custom_question_needs_an_exact_scoped_answer(self):
        self.profile["answers"][0]["scope"]["employer"] = "Another Company"
        plan = self.rp.plan(self.profile, form_fields(), self.job)
        self.assertNotIn(SPONSOR, plan["answers"])
        self.assertIn(SPONSOR, {item["id"] for item in plan["unknown"]})

    def test_custom_question_never_takes_a_contact_fact_by_its_label(self):
        # An employer's own question titled "Phone" or "City" is not the contact
        # field; only an exact scoped answer may fill it.
        fields = form_fields() + [
            field("customQuestions.ddd444.answer", "Phone"),
            field("customQuestions.eee555.answer", "City"),
            field("eeoc.firstName", "First name"),
        ]
        plan = self.rp.plan(self.profile, fields, self.job)
        for name in ("customQuestions.ddd444.answer", "customQuestions.eee555.answer", "eeoc.firstName"):
            self.assertNotIn(name, plan["answers"])
            self.assertIn(name, {item["id"] for item in plan["unknown"]})
        question = "Phone"
        scope = {"country": "US", "job_type": "internship", "employer": "Example Labs", "question": question, "effective_at": None}
        self.profile["answers"].append({"question": question, **fact("+1 202 555 0111", scope=scope)})
        plan = self.rp.plan(self.profile, fields, self.job)
        self.assertEqual(plan["answers"]["customQuestions.ddd444.answer"]["value"], "+1 202 555 0111")
        self.assertEqual(plan["answers"]["phone_number"]["value"], "+1 202 555 0100")

    def test_location_without_confirmed_city_and_state_is_unknown(self):
        self.profile["contact"]["state"] = fact(None, "unknown")
        plan = self.rp.plan(self.profile, form_fields(), self.job)
        self.assertIsNone(plan["location"])
        self.assertIn("location", {item["id"] for item in plan["unknown"]})

    def test_declined_or_unconfirmed_contact_is_never_written(self):
        self.profile["contact"]["phone"] = fact("+1 202 555 0199", "declined")
        plan = self.rp.plan(self.profile, form_fields(), self.job)
        self.assertNotIn("phone_number", plan["answers"])
        self.assertIn("phone_number", {item["id"] for item in plan["unknown"]})


class DriverTests(Base):
    def driver(self, form, **options):
        return self.rp.RipplingDriver(form.client, sleep=Mock(), **options)

    def test_resume_uploads_first_and_confirmed_facts_overwrite_the_parse(self):
        form = FakeForm(ready_after=3)
        result = self.driver(form).fill("tab-1", self.profile, self.job, pdf=self.pdf)
        names = form.names()
        self.assertEqual(names[0], "upload")
        self.assertEqual(names[1:4], ["modelReady"] * 3)
        self.assertLess(names.index("modelReady"), names.index("modelFill"))
        self.assertEqual(form.values["linkedin_link"], "https://www.linkedin.com/in/alex-rivera-example")
        self.assertEqual(form.values["first_name"], "Alex")
        location = [call for call in form.calls if call[0] == "location"]
        self.assertEqual(location, [("location", {"query": "Example City, CA"})])
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["blocked"], [])
        self.assertEqual(
            result["upload_receipt"],
            {
                "name": self.pdf.name,
                "size": self.pdf.stat().st_size,
                "sha256": hashlib.sha256(self.pdf.read_bytes()).hexdigest(),
            },
        )
        self.assertIn(HEARD, {item["id"] for item in result["unknown"]})
        self.assertTrue(result["form_valid"])

    def test_ready_poll_names_the_uploaded_file(self):
        form = FakeForm()
        self.driver(form).fill("tab-1", self.profile, self.job, pdf=self.pdf)
        ready = [call[1] for call in form.calls if call[0] == "modelReady"]
        self.assertEqual(ready, [{"name": self.pdf.name}])

    def test_late_parse_overwrite_is_rewritten_and_checked_again(self):
        form = FakeForm(late_parse=1)
        result = self.driver(form).fill("tab-1", self.profile, self.job, pdf=self.pdf)
        self.assertEqual(form.values["linkedin_link"], "https://www.linkedin.com/in/alex-rivera-example")
        fills = [call[1] for call in form.calls if call[0] == "modelFill"]
        self.assertEqual(len(fills), 2)
        self.assertEqual(set(fills[1]), {"linkedin_link"}, "only the overwritten field is written again")
        names = form.names()
        self.assertEqual(names[-2:], ["modelCheck", "modelScan"], "the last check passes before the final scan")
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["blocked"], [])

    def test_overwrite_seen_only_by_the_final_scan_is_reported(self):
        # The parse lands after every check passed: the final scan is the proof.
        form = FakeForm(scan_overwrite=2)
        result = self.driver(form).fill("tab-1", self.profile, self.job, pdf=self.pdf)
        self.assertEqual(form.names()[-1], "modelScan")
        self.assertEqual(result["status"], "needs_manual")
        self.assertIn({"id": "linkedin_link", "reason": "value_overwritten"}, result["blocked"])
        self.assertNotIn("linkedin_link", result["filled"])
        self.assertIn(SPONSOR, result["filled"], "a choice answer matches its option value")

    def test_parse_landing_after_one_clean_check_is_rewritten(self):
        form = FakeForm(check_overwrite=True)
        result = self.driver(form).fill("tab-1", self.profile, self.job, pdf=self.pdf)
        self.assertEqual(form.values["linkedin_link"], "https://www.linkedin.com/in/alex-rivera-example")
        self.assertEqual(result["status"], "filled", result["blocked"])
        self.assertEqual([call[1] for call in form.calls if call[0] == "modelFill"][1:], [{"linkedin_link": {"value": "https://www.linkedin.com/in/alex-rivera-example", "status": "confirmed"}}])

    def test_parse_that_keeps_overwriting_a_fact_holds_the_tab(self):
        form = FakeForm(late_parse=-1)
        result = self.driver(form).fill("tab-1", self.profile, self.job, pdf=self.pdf)
        self.assertEqual(result["status"], "needs_manual")
        self.assertIn({"id": "linkedin_link", "reason": "value_overwritten"}, result["blocked"])
        self.assertNotIn("linkedin_link", result["filled"])
        self.assertLessEqual(form.names().count("modelFill"), 3, "rewrites are bounded")

    def test_parse_that_never_finishes_holds_the_tab_for_the_user(self):
        form = FakeForm(ready_after=99)
        result = self.driver(form, parse_wait_s=2, poll_s=0.5).fill("tab-1", self.profile, self.job, pdf=self.pdf)
        self.assertEqual(result["status"], "needs_manual")
        self.assertEqual(result["blocked"], [{"id": "resume", "reason": "resume_parse_timeout"}])
        self.assertEqual(form.names().count("modelReady"), 4)
        self.assertNotIn("modelFill", form.names())

    def test_page_without_a_form_model_is_handed_back(self):
        form = FakeForm(formsy=False)
        result = self.driver(form).fill("tab-1", self.profile, self.job)
        self.assertEqual(result["status"], "needs_manual")
        self.assertEqual(result["blocked"], [{"id": "form", "reason": "form_model_missing"}])
        form.client.upload_file.assert_not_called()

    def test_required_field_left_empty_is_reported_blocked(self):
        form = FakeForm(keep_empty={"phone_number"})
        result = self.driver(form).fill("tab-1", self.profile, self.job, pdf=self.pdf)
        self.assertEqual(result["status"], "needs_manual")
        self.assertIn({"id": "phone_number", "reason": "required_empty"}, result["blocked"])

    def test_location_suggestion_failure_is_blocked(self):
        form = FakeForm(location="ambiguous_suggestion")
        result = self.driver(form).fill("tab-1", self.profile, self.job, pdf=self.pdf)
        self.assertIn({"id": "location", "reason": "ambiguous_suggestion"}, result["blocked"])

    def test_resume_name_missing_from_the_form_is_blocked(self):
        form = FakeForm()
        form.upload = lambda tab, path: form.calls.append(("upload", path))
        form.client.upload_file.side_effect = form.upload
        form.values.update(first_name="Parsed")
        result = self.driver(form).fill("tab-1", self.profile, self.job, pdf=self.pdf)
        self.assertIn({"id": "resume", "reason": "resume_not_attached"}, result["blocked"])

    def test_non_pdf_upload_is_refused_before_touching_the_page(self):
        bad = self.root / "notes.txt"
        bad.write_text("not a pdf", encoding="utf-8")
        form = FakeForm()
        with self.assertRaises(ValueError):
            self.driver(form).fill("tab-1", self.profile, self.job, pdf=bad)
        self.assertEqual(form.calls, [])

    def test_driver_only_calls_fill_side_adapter_actions(self):
        form = FakeForm()
        self.driver(form).fill("tab-1", self.profile, self.job, pdf=self.pdf)
        allowed = {"upload", "modelReady", "modelScan", "modelFill", "modelCheck", "location"}
        self.assertTrue(set(form.names()) <= allowed, form.names())
        for call in form.client.evaluate_script.call_args_list:
            self.assertNotRegex(call.args[1][-300:], r"(?i)submit")

    def test_script_refuses_unknown_actions(self):
        with self.assertRaises(ValueError):
            self.rp.script("submit")
        with self.assertRaises(ValueError):
            self.rp.script("navigate")

    def test_rippling_apply_urls_are_recognized(self):
        self.assertTrue(self.rp.matches("https://ats.rippling.com/example/jobs/0000-1111/apply"))
        self.assertTrue(self.rp.matches("https://ats.rippling.com/en-US/example/jobs/0000-1111"))
        self.assertFalse(self.rp.matches("https://ats.rippling.com.evil.example/x/jobs/1"))
        self.assertFalse(self.rp.matches("https://boards.example.invalid/jobs/1"))
