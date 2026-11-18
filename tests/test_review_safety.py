"""Offline behavior contracts for autoapply-local-review-safety, groups A–E.

Giả định của người thực thi: dùng API hiện có, fixture tổng hợp và subTest cho
ma trận đầu vào; chỉ được sửa test nên ghi giả định ở đây thay vì sửa plan.
RED được báo trong kết quả chạy; không ghi receipt ngoài phạm vi file test.
"""

import copy
import io
import json
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from test_support import ReleaseCase, candidate, fact, job


ACCOUNT = "alex@example.invalid"
SCHEMA = {
    "type": "object",
    "required": ["status"],
    "additionalProperties": False,
    "properties": {"status": {"type": "string", "enum": ["completed"]}},
}
RESULT = {"status": "completed"}


def mail_task(kind, account=ACCOUNT):
    operation = {"mail_search": "search_email_ids", "mail_read": "read_email", "mail_discovery": "get_profile"}
    return {
        "kind": kind,
        "allowed_tools": ["mcp__gmail__" + operation[kind]],
        "schema": copy.deepcopy(SCHEMA),
        "prompt": json.dumps({"account": account}),
    }


class MailGateSafetyTests(ReleaseCase):
    def assert_gate(self, policy, event, expected):
        gate = self.module("tool_gate")
        try:
            actual = gate.permitted(policy, event)
        except Exception as error:
            self.fail("permitted must deny malformed input without raising: " + type(error).__name__)
        self.assertIs(actual, expected)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = gate.main([json.dumps(policy)], stdin=io.StringIO(json.dumps(event)))
        self.assertEqual(code, 0 if expected else 2)
        self.assertEqual(stdout.getvalue(), "", "A permitted call must not emit an allow verdict")
        self.assertNotIn("allow", stderr.getvalue().casefold())

    def test_search_and_read_require_exact_string_event_account_on_both_servers(self):
        for server in ("gmail", "claude_ai_Gmail"):
            for kind, operation in (("mail_search", "search_email_ids"), ("mail_read", "read_email")):
                tool = "mcp__" + server + "__" + operation
                policy = {"kind": kind, "account": ACCOUNT, "tools": [tool]}
                inputs = [
                    {},
                    *(
                        {"account": value}
                        for value in (None, "", ACCOUNT.upper(), "other@example.invalid", [], {}, 7, True)
                    ),
                ]
                for tool_input in inputs:
                    with self.subTest(server=server, kind=kind, tool_input=tool_input):
                        self.assert_gate(policy, {"tool_name": tool, "tool_input": tool_input}, False)
                with self.subTest(server=server, kind=kind, account="exact"):
                    self.assert_gate(policy, {"tool_name": tool, "tool_input": {"account": ACCOUNT}}, True)

    def test_missing_event_input_is_denied(self):
        for kind in ("mail_search", "mail_read"):
            task = mail_task(kind)
            tool = task["allowed_tools"][0]
            self.assert_gate({"kind": kind, "account": ACCOUNT, "tools": [tool]}, {"tool_name": tool}, False)

    def test_non_dict_event_inputs_are_denied(self):
        for kind in ("mail_search", "mail_read"):
            tool = mail_task(kind)["allowed_tools"][0]
            for tool_input in (None, [], [ACCOUNT], ACCOUNT, 7, True):
                with self.subTest(kind=kind, tool_input=tool_input):
                    self.assert_gate(
                        {"kind": kind, "account": ACCOUNT, "tools": [tool]},
                        {"tool_name": tool, "tool_input": tool_input},
                        False,
                    )

    def test_invalid_policy_account_denies_even_matching_or_null_event_account(self):
        for kind in ("mail_search", "mail_read"):
            tool = mail_task(kind)["allowed_tools"][0]
            for account in (None, "", [], {}, 7, True):
                for event_account in (None, ACCOUNT, account):
                    with self.subTest(kind=kind, account=account, event_account=event_account):
                        self.assert_gate(
                            {"kind": kind, "account": account, "tools": [tool]},
                            {"tool_name": tool, "tool_input": {"account": event_account}},
                            False,
                        )
            self.assert_gate({"kind": kind, "tools": [tool]}, {"tool_name": tool, "tool_input": {}}, False)

    def test_tools_list_cannot_expand_search_or_read_action(self):
        for kind, opposite in (("mail_search", "read_email"), ("mail_read", "search_email_ids")):
            for server in ("gmail", "claude_ai_Gmail"):
                for operation in (
                    opposite,
                    "get_profile",
                    "send_email",
                    "create_draft",
                    "modify_labels",
                    "delete",
                    "archive",
                ):
                    tool = "mcp__" + server + "__" + operation
                    with self.subTest(kind=kind, tool=tool):
                        self.assert_gate(
                            {"kind": kind, "account": ACCOUNT, "tools": [tool]},
                            {"tool_name": tool, "tool_input": {"account": ACCOUNT}},
                            False,
                        )

    def test_matching_action_still_requires_membership_in_tools(self):
        for kind in ("mail_search", "mail_read"):
            tool = mail_task(kind)["allowed_tools"][0]
            self.assert_gate(
                {"kind": kind, "account": ACCOUNT, "tools": []},
                {"tool_name": tool, "tool_input": {"account": ACCOUNT}},
                False,
            )

    def test_discovery_only_permits_gmail_discovery_queries_and_profile_tools(self):
        policy = {"kind": "mail_discovery", "tools": []}
        for tool in ("ToolSearch", "search_tool_bm25"):
            for query, expected in (
                ("discover GMAIL tools", True),
                ("calendar", False),
                ("gmailish", False),
                (None, False),
                (7, False),
            ):
                with self.subTest(tool=tool, query=query):
                    self.assert_gate(policy, {"tool_name": tool, "tool_input": {"query": query}}, expected)
        for server in ("gmail", "claude_ai_Gmail"):
            for operation in (
                "get_profile",
                "get_account_profile",
                "get_user_profile",
                "list_accounts",
                "get_accounts",
            ):
                self.assert_gate(policy, {"tool_name": "mcp__" + server + "__" + operation, "tool_input": {}}, True)
            for operation in (
                "search_email_ids",
                "read_email",
                "send_email",
                "create_draft",
                "modify_labels",
                "delete",
                "archive",
            ):
                tool = "mcp__" + server + "__" + operation
                with self.subTest(tool=tool):
                    self.assert_gate(
                        {**policy, "tools": [tool]}, {"tool_name": tool, "tool_input": {"account": ACCOUNT}}, False
                    )

    def test_non_mail_task_cannot_allowlist_gmail_but_keeps_browser_capability(self):
        browser = "mcp__autoapply-local-browser__take_snapshot"
        for kind in ("apply", "review", "submit", "browser_evaluate", "tailor", "apply_analysis"):
            for server in ("gmail", "claude_ai_Gmail"):
                for operation in (
                    "get_profile",
                    "search_email_ids",
                    "read_email",
                    "send_email",
                    "create_draft",
                    "modify_labels",
                    "delete",
                    "archive",
                ):
                    tool = "mcp__" + server + "__" + operation
                    with self.subTest(kind=kind, tool=tool):
                        self.assert_gate(
                            {"kind": kind, "tools": [tool, browser]},
                            {"tool_name": tool, "tool_input": {"account": ACCOUNT}},
                            False,
                        )
            self.assert_gate({"kind": kind, "tools": [browser]}, {"tool_name": browser, "tool_input": {}}, True)


class ProfileBindingSafetyTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.profile = self.module("profile")
        self.data = candidate()
        for kind, key in (("education", "school"), ("experience", "title")):
            self.data[kind].append({"id": kind + ":2", key: fact("Second " + kind)})

    def resolve(self, kind, **binding):
        label = "School" if kind == "education" else "Job title"
        return self.profile.resolve_field(self.data, {"label": label, "record": {"kind": kind, **binding}}, job())

    def assert_unknown(self, answer):
        self.assertEqual(answer["status"], "unknown")
        self.assertIsNone(answer["value"])

    def test_matching_index_and_id_resolve_with_provenance(self):
        for kind, key in (("education", "school"), ("experience", "title")):
            for index in (0, 1):
                with self.subTest(kind=kind, index=index):
                    record = self.data[kind][index]
                    self.assertEqual(self.resolve(kind, index=index, id=record["id"]), record[key])
                    self.assertEqual(self.resolve(kind, index=index), record[key])

    def test_index_and_conflicting_id_are_unknown(self):
        for kind in ("education", "experience"):
            for identifier in (kind + ":2", "missing"):
                with self.subTest(kind=kind, identifier=identifier):
                    self.assert_unknown(self.resolve(kind, index=0, id=identifier))

    def test_invalid_present_index_never_falls_back_to_valid_id(self):
        for kind in ("education", "experience"):
            for index in (True, False, "0", 0.0, -1, 2, 999, None):
                with self.subTest(kind=kind, index=index):
                    self.assert_unknown(self.resolve(kind, index=index, id=kind + ":1"))

    def test_id_only_requires_exactly_one_match(self):
        for kind, key in (("education", "school"), ("experience", "title")):
            with self.subTest(kind=kind):
                self.assertEqual(self.resolve(kind, id=kind + ":1"), self.data[kind][0][key])
                self.assert_unknown(self.resolve(kind, id="missing"))
                self.assert_unknown(self.resolve(kind))
                self.data[kind].append(copy.deepcopy(self.data[kind][0]))
                self.assert_unknown(self.resolve(kind, id=kind + ":1"))

    def test_conflicting_record_scope_stays_unknown(self):
        for kind, key in (("education", "school"), ("experience", "title")):
            with self.subTest(kind=kind):
                self.data[kind][0][key]["scope"] = {kind + "_id": kind + ":2"}
                self.assert_unknown(self.resolve(kind, index=0, id=kind + ":1"))


class PdfSourceSafetyTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.pdf = self.root / "synthetic résumé.pdf"
        self.pdf.touch()

    def test_pdf_uses_explicit_utf8_and_keeps_extracted_facts_unknown(self):
        text = "Kỹ sư phần mềm\n主导 API\n"
        completed = subprocess.CompletedProcess([], 0, stdout=text.encode("utf-8"), stderr=b"")
        with patch.object(subprocess, "run", return_value=completed) as runner:
            result = self.module("extraction").extract_source(self.pdf)
        argv = runner.call_args.args[0]
        self.assertEqual(argv, ["pdftotext", "-enc", "UTF-8", "-layout", str(self.pdf), "-"])
        self.assertEqual(runner.call_args.kwargs["timeout"], 30)
        self.assertEqual(result["text"], text)
        self.assertEqual(result["status"], "draft")
        self.assertIs(result["requires_confirmation"], True)
        self.assertEqual([item["text"] for item in result["facts"]], text.splitlines())
        self.assertTrue(result["facts"])
        self.assertTrue(all(item["status"] == "unknown" for item in result["facts"]))

    def test_pdf_invalid_utf8_nonzero_oserror_and_timeout_have_clear_value_errors(self):
        failures = (
            subprocess.CompletedProcess([], 0, stdout=b"\xff\xfe", stderr=b""),
            subprocess.CompletedProcess([], 2, stdout=b"partial", stderr=b"synthetic failure"),
            OSError("Synthetic missing extractor"),
            subprocess.TimeoutExpired(["pdftotext"], 30),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__, returncode=getattr(failure, "returncode", None)):
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch.object(subprocess, "run", **kwargs) as runner:
                    try:
                        self.module("extraction").extract_source(self.pdf)
                    except ValueError as error:
                        self.assertTrue(str(error).strip(), "Failure needs an actionable diagnostic")
                        self.assertNotIn("Traceback", str(error))
                    except Exception as error:
                        self.fail("Extraction must raise ValueError, got " + type(error).__name__)
                    else:
                        self.fail("Extraction must reject invalid or unavailable PDF text")
                runner.assert_called_once()
