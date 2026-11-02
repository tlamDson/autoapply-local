"""Offline behavior contracts for autoapply-local-review-safety, groups A–E.

Giả định của người thực thi: dùng API hiện có, fixture tổng hợp và subTest cho
ma trận đầu vào; chỉ được sửa test nên ghi giả định ở đây thay vì sửa plan.
RED được báo trong kết quả chạy; không ghi receipt ngoài phạm vi file test.
"""

import copy
import io
import json
import subprocess
import unicodedata
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

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


class EngineMailSafetyTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.engines = self.module("engines")
        self.original_resolver = self.engines.resolve_executable
        self.runner = Mock(side_effect=self.process)
        for boundary in (
            patch.object(self.engines, "resolve_executable", side_effect=lambda name: ["synthetic-" + name]),
            patch.object(self.engines, "_app_plugin_names", return_value=({}, set())),
            patch.object(subprocess, "run", self.runner),
            patch.object(subprocess, "Popen", side_effect=AssertionError("No live subprocesses")),
        ):
            boundary.start()
            self.addCleanup(boundary.stop)

    def process(self, argv, **kwargs):
        if "--version" in argv:
            result = "2.1.312"
        elif "mcp" in argv and "list" in argv:
            result = "[]"
        elif "--print" in argv:
            result = json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "structured_output": RESULT}
            )
        else:
            result = json.dumps(RESULT)
        return subprocess.CompletedProcess(argv, 0, result, "")

    def test_codex_search_read_fail_before_resolution_probe_inventory_or_execution(self):
        for kind in ("mail_search", "mail_read"):
            with self.subTest(kind=kind):
                self.runner.reset_mock()
                with patch.object(self.engines, "resolve_executable", return_value=["synthetic"]) as resolve:
                    with self.assertRaises(self.engines.EngineError) as caught:
                        self.engines.Engine("codex", runner=self.runner).run(mail_task(kind))
                self.assertEqual(caught.exception.category, "configuration")
                self.assertIn("manual_required", str(caught.exception))
                self.assertNotIn(ACCOUNT, str(caught.exception))
                self.assertNotIn("@", str(caught.exception))
                resolve.assert_not_called()
                self.runner.assert_not_called()

    def test_codex_mail_refusal_does_not_depend_on_executable_path(self):
        for kind in ("mail_search", "mail_read"):
            with self.subTest(kind=kind):
                with patch.object(self.engines, "resolve_executable", self.original_resolver):
                    with patch.object(self.engines.shutil, "which", return_value=None):
                        with self.assertRaises(self.engines.EngineError) as caught:
                            self.engines.Engine("codex", runner=self.runner).run(mail_task(kind))
                self.assertEqual(caught.exception.category, "configuration")
                self.assertIn("manual_required", str(caught.exception))
                self.runner.assert_not_called()

    def test_codex_discovery_browser_and_tool_free_tasks_still_dispatch(self):
        browser = "mcp__autoapply-local-browser__take_snapshot"
        payloads = [mail_task("mail_discovery")]
        payloads.extend(
            {"kind": kind, "schema": SCHEMA, "allowed_tools": [], "prompt": "Synthetic"}
            for kind in ("tailor", "apply_analysis")
        )
        payloads.append(
            {"kind": "browser_evaluate", "schema": SCHEMA, "allowed_tools": [browser], "prompt": "Synthetic"}
        )
        for payload in payloads:
            with self.subTest(kind=payload["kind"]):
                self.runner.reset_mock()
                engine = self.engines.Engine("codex", runner=self.runner, debug_url="http://127.0.0.1:9333")
                self.assertEqual(engine.run(payload), RESULT)
                dispatches = [call for call in self.runner.call_args_list if "exec" in call.args[0]]
                self.assertEqual(len(dispatches), 1)

    def test_claude_mail_hook_carries_exact_account_for_search_and_read(self):
        for kind in ("mail_search", "mail_read"):
            with self.subTest(kind=kind):
                self.runner.reset_mock()
                self.assertEqual(self.engines.Engine("claude", runner=self.runner).run(mail_task(kind)), RESULT)
                dispatches = [call.args[0] for call in self.runner.call_args_list if "--print" in call.args[0]]
                self.assertEqual(len(dispatches), 1)
                argv = dispatches[0]
                settings = json.loads(argv[argv.index("--settings") + 1])
                hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
                policy = json.loads(hook["args"][-1])
                self.assertEqual(policy["account"], ACCOUNT)
                self.assertEqual(policy["kind"], kind)
                self.assertEqual(policy["tools"], mail_task(kind)["allowed_tools"])

    def test_claude_invalid_or_missing_account_rejects_before_runner(self):
        for kind in ("mail_search", "mail_read"):
            prompts = [
                "{}",
                *(
                    json.dumps({"account": value})
                    for value in (None, "", [], {}, 7, True, [ACCOUNT], {"address": ACCOUNT})
                ),
            ]
            for prompt in prompts:
                with self.subTest(kind=kind, prompt=prompt):
                    self.runner.reset_mock()
                    with self.assertRaises(ValueError):
                        self.engines.Engine("claude", runner=self.runner).run({**mail_task(kind), "prompt": prompt})
                    self.runner.assert_not_called()

    def test_mailchecker_returns_manual_required_for_engine_configuration_refusal(self):
        engine = Mock(spec=["name", "run"])
        engine.name = "codex"
        capabilities = {
            "accounts": [ACCOUNT],
            "permissions": ["search", "read"],
            "search_tool": "mcp__gmail__search_email_ids",
            "read_tool": "mcp__gmail__read_email",
        }
        engine.run.side_effect = [
            {"gmail": capabilities},
            self.engines.EngineError("configuration", "manual_required: account binding unavailable"),
        ]
        native = self.module("runtime").NativeMailRuntime(engine)
        report = (
            self.module("gmail")
            .MailChecker(self.root, native)
            .check({"gmail_opt_in": True, "gmail_account": ACCOUNT, "gmail_permissions": ["search", "read"]})
        )
        self.assertEqual(report["status"], "manual_required")
        self.assertEqual(report["events"], [])
        self.assertEqual(
            [call.args[0]["kind"] for call in engine.run.call_args_list], ["mail_discovery", "mail_search"]
        )


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


class TailorUnicodeSafetyTests(ReleaseCase):
    def source(self, text, **fields):
        return {
            "id": "experience:one",
            "entry_id": "entry:one",
            "type": "experience",
            "status": "confirmed",
            "text": text,
            **fields,
        }

    def claim(self, text, **fields):
        return {"type": "experience", "source_ids": ["experience:one"], "text": text, **fields}

    def validate(self, sources, claims):
        return self.module("tailor").validate_claims(sources, claims)

    def test_unsupported_cjk_accented_and_greek_additions_are_rejected(self):
        for added in ("主导", "résilient", "λύση"):
            with self.subTest(added=added):
                with self.assertRaises(ValueError):
                    self.validate([self.source("Built API")], [self.claim("Built API " + added)])

    def test_nfc_evidence_and_nfd_claim_are_equivalent(self):
        text = "Built Café λύση API"
        claims = self.validate([self.source(text)], [self.claim(unicodedata.normalize("NFD", text))])
        self.assertEqual(claims[0]["text"], unicodedata.normalize("NFD", text))

    def test_english_rewrites_keep_build_built_and_apis_api(self):
        claims = self.validate([self.source("Build APIs using Python")], [self.claim("Built API with Python")])
        self.assertEqual(claims[0]["text"], "Built API with Python")

    def test_ownership_order_cannot_be_swapped(self):
        with self.assertRaises(ValueError):
            self.validate([self.source("Used Python and led Java API")], [self.claim("Led Python and used Java API")])

    def test_numeric_claims_stay_bound_to_evidence(self):
        source = self.source("Reduced latency by 12%")
        self.assertEqual(self.validate([source], [self.claim(source["text"])])[0]["text"], source["text"])
        with self.assertRaises(ValueError):
            self.validate([source], [self.claim("Reduced latency by 15%")])

    def test_metric_qualifiers_cannot_be_dropped(self):
        for qualifier in ("approximately", "about", "estimated", "roughly", "up to", "at least"):
            with self.subTest(qualifier=qualifier):
                text = "Reduced latency by " + qualifier + " 12%"
                self.assertEqual(self.validate([self.source(text)], [self.claim(text)])[0]["text"], text)
                with self.assertRaises(ValueError):
                    self.validate([self.source(text)], [self.claim("Reduced latency by 12%")])

    def test_qualifier_detection_uses_same_unicode_casefold_as_evidence(self):
        # Long s casefolds to s: the evidence still says "estimated".
        text = "Reduced latency by eſtimated 12%"
        self.validate([self.source(text)], [self.claim("Reduced latency by estimated 12%")])
        with self.assertRaises(ValueError):
            self.validate([self.source(text)], [self.claim("Reduced latency by 12%")])

    def test_cpp_csharp_keywords_remain_distinct_from_c(self):
        tailor = self.module("tailor")
        for keyword in ("C++", "C#"):
            with self.subTest(keyword=keyword):
                text = "Built " + keyword + " API"
                self.validate([self.source(text)], [self.claim(text)])
                with self.assertRaises(ValueError):
                    self.validate([self.source("Built C API")], [self.claim(text)])
                self.assertEqual(tailor.classify_keywords([keyword], [self.source(text)])[keyword]["category"], "HAVE")
                self.assertEqual(
                    tailor.classify_keywords([keyword], [self.source("Built C API")])[keyword]["category"], "MISSING"
                )

    def test_secondary_source_cannot_replace_primary_owned_fields(self):
        originals = {"employer": "Example Systems", "title": "Engineering Intern", "start": "2029-06", "end": "2029-08"}
        changes = {"employer": "Other Systems", "title": "Senior Engineer", "start": "2028-06", "end": "2030-08"}
        for field in originals:
            with self.subTest(field=field):
                primary = self.source("Built API", **{field: originals[field]})
                secondary = {**self.source("Built API", **{field: changes[field]}), "id": "experience:two"}
                claim = self.claim("Built API", **{field: changes[field]})
                claim["source_ids"].append(secondary["id"])
                with self.assertRaises(ValueError):
                    self.validate([primary, secondary], [claim])

    def test_missing_primary_field_still_requires_secondary_owner_match(self):
        originals = {"employer": "Example Systems", "title": "Engineering Intern", "start": "2029-06", "end": "2029-08"}
        for field, value in originals.items():
            with self.subTest(field=field):
                primary = self.source("Built API")
                secondary = {**self.source("Built API", **{field: value}), "id": "experience:two"}
                claim = self.claim("Built API", **{field: "Unconfirmed"})
                claim["source_ids"].append(secondary["id"])
                with self.assertRaises(ValueError):
                    self.validate([primary, secondary], [claim])
                claim[field] = value
                self.assertEqual(self.validate([primary, secondary], [claim])[0][field], value)

    def test_immutable_strings_accept_nfc_equivalence_without_casefolding(self):
        for field in ("title", "employer"):
            for primary_has_field in (True, False):
                with self.subTest(field=field, primary_has_field=primary_has_field):
                    value = "Café Systems"
                    primary = self.source("Built API", **({field: value} if primary_has_field else {}))
                    secondary = {**self.source("Built API", **{field: value}), "id": "experience:two"}
                    claim = self.claim("Built API", **{field: unicodedata.normalize("NFD", value)})
                    claim["source_ids"].append(secondary["id"])
                    self.assertEqual(self.validate([primary, secondary], [claim])[0][field], claim[field])
                    claim[field] = value.lower()
                    with self.assertRaises(ValueError):
                        self.validate([primary, secondary], [claim])


class PdfSourceSafetyTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.pdf = self.root / "synthetic résumé.pdf"
        self.pdf.touch()

    def test_pdf_uses_explicit_utf8_and_keeps_extracted_facts_unknown(self):
        text = "Kỹ sư phần mềm\n主导 API\n"
        completed = subprocess.CompletedProcess([], 0, stdout=text.encode("utf-8"), stderr=b"")
        with patch.object(subprocess, "run", return_value=completed) as runner:
            result = self.module("tailor").extract_source(self.pdf)
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
                        self.module("tailor").extract_source(self.pdf)
                    except ValueError as error:
                        self.assertTrue(str(error).strip(), "Failure needs an actionable diagnostic")
                        self.assertNotIn("Traceback", str(error))
                    except Exception as error:
                        self.fail("Extraction must raise ValueError, got " + type(error).__name__)
                    else:
                        self.fail("Extraction must reject invalid or unavailable PDF text")
                runner.assert_called_once()
