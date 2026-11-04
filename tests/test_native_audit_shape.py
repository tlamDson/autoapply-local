"""Offline contracts for the exact adapter audit output and native validation.

Giả định của người thực thi: use a mock engine invoking the production validator
and a mocked native tab inventory; Node vm is the only real process boundary.
Record RED in the final report; this test-only turn leaves the plan untouched.
"""

import copy
import json
import shutil
import socket
import subprocess
from unittest.mock import Mock, patch

from test_support import ReleaseCase


URL = "https://jobs.example.invalid/synthetic-audit"
ENDPOINT = "http://127.0.0.1:9333"
ENGINES = ("codex", "claude")
ATS = ("greenhouse", "rippling", "workday")
NODE_AUDIT = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
(async () => {
  const outputs = {};
  for (const [ats, source] of Object.entries(input.scripts)) {
    const document = {
      querySelectorAll: () => [], querySelector: () => null,
      body: {innerText: input.text, getAttribute: () => null}, title: input.title
    };
    const context = vm.createContext({document, location: {href: input.url}});
    if (vm.runInContext('typeof module !== "undefined" || typeof require !== "undefined"', context)) {
      throw new Error('Adapter vm must not have CommonJS globals');
    }
    outputs[ats] = await vm.runInContext('(' + source + ')()', context, {timeout: 1000});
  }
  process.stdout.write(JSON.stringify(outputs));
})().catch(error => {console.error(error); process.exitCode = 1;});
"""


class NativeAuditShapeTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.browser = self.module("browser")
        self.engines = self.module("engines")
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is a required offline adapter test dependency")
        self.node_command = [node, "-e", NODE_AUDIT]
        real_run, real_popen = subprocess.run, subprocess.Popen

        def node_only_run(argv, **kwargs):
            self.assertEqual(argv, self.node_command, "Only the offline Node vm may execute")
            self.assertIs(kwargs.get("shell"), False)
            return real_run(argv, **kwargs)

        def node_only_popen(argv, **kwargs):
            self.assertEqual(argv, self.node_command, "Live engine/browser processes are forbidden")
            self.assertIs(kwargs.get("shell"), False)
            return real_popen(argv, **kwargs)

        guards = [
            patch.object(subprocess, "run", side_effect=node_only_run),
            patch.object(subprocess, "Popen", side_effect=node_only_popen),
            patch.object(socket, "socket", side_effect=AssertionError("No network in audit shape tests")),
            patch.object(socket, "create_connection", side_effect=AssertionError("No network")),
            patch.object(self.browser, "urlopen", side_effect=AssertionError("No native browser requests")),
            patch.object(self.engines.Engine, "run", side_effect=AssertionError("No live engines")),
            patch.object(self.engines, "resolve_executable", side_effect=AssertionError("No engine probes")),
        ]
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)

        self.audit = {
            "fields": [
                {
                    "id": "missing-field-1",
                    "type": "text",
                    "required": True,
                    "label": "Synthetic unknown question",
                    "value": "",
                    "status": "unknown",
                    "observed": True,
                },
                {
                    "id": "resume",
                    "type": "file",
                    "observed": True,
                    "files": [{"name": "synthetic.pdf", "size": 123, "sha256": "a" * 64}],
                },
            ],
            "page": {"index": 1, "total": 2, "title": "Synthetic review", "extension": {"observed": True}},
            "text": "Resume: synthetic.pdf",
            "errors": ["Synthetic validation error"],
            "attachments": [
                {
                    "name": "synthetic.pdf",
                    "observed": True,
                    "evidence": {"start": 0, "end": 21, "quote": "Resume: synthetic.pdf"},
                }
            ],
            "url": URL,
        }

    def evaluate(self, name, payload, function="async () => ({})", *, expect_accepted=False):
        """Validate precisely the schema produced by EngineMCPClient, without inference."""
        engine = Mock(spec=["run"])
        inventory = Mock(return_value=[{"id": "synthetic-tab", "url": URL}])

        def run(task):
            self.assertEqual(task["kind"], "browser_evaluate")
            request = json.loads(task["prompt"][task["prompt"].index("{") :])
            self.assertEqual(request["function"], function)
            self.assertEqual(request["readback"], "audit")
            self.assertEqual(request["target_url"], URL)
            value = copy.deepcopy(payload)
            if name == "claude":
                value = {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "permission_denials": [],
                    "structured_output": value,
                }
            return self.engines._validated_result(json.dumps(value), task["schema"], name)

        engine.run.side_effect = run
        client = self.browser.EngineMCPClient(engine, ENDPOINT)
        client.list_pages = inventory
        before = copy.deepcopy(payload)
        try:
            return client.evaluate_script("synthetic-tab", function, operation="audit")
        except self.engines.InvalidResponse:
            if not expect_accepted:
                raise
            # Diagnose the actual task schema: InvalidResponse deliberately hides
            # validate_schema's reason. Never change the schema or the dispatched
            # output; the metadata-free copy is only a diagnostic control.
            schema = engine.run.call_args.args[0]["schema"]
            try:
                self.engines.validate_schema(payload, schema)
            except ValueError as error:
                audit_schema = schema["properties"]["result"]
                self.assertIs(audit_schema["additionalProperties"], False)
                self.assertEqual(
                    set(payload["result"]) - set(audit_schema["properties"]),
                    {"required_unknown"},
                    f"Audit was rejected for a different reason: {error}",
                )
                self.assertEqual(str(error), "response.result contains unexpected properties")
                control = copy.deepcopy(payload)
                control["result"].pop("required_unknown")
                self.assertEqual(self.engines.validate_schema(control, schema), control)
                self.fail(
                    "Valid audit rejected: required_unknown is missing from audit schema properties; "
                    f"production validator: {error}. The same payload without only required_unknown "
                    "passes that exact schema."
                )
            self.fail("Engine rejected an audit that passes the production task schema")
        finally:
            engine.run.assert_called_once()
            inventory.assert_called_once_with()
            self.assertEqual(payload, before, "Validation must not rewrite unknown facts or observations")

    def test_exact_generated_audits_pass_both_engine_validators_unchanged(self):
        scripts = {ats: self.browser.BrowserBridge(object())._script("audit", ats) for ats in ATS}
        completed = subprocess.run(
            self.node_command,
            input=json.dumps({"scripts": scripts, "url": URL, "title": "Synthetic review", "text": "Synthetic page"}),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        outputs = json.loads(completed.stdout)
        self.assertEqual(set(outputs), set(ATS))
        for ats in ATS:
            observed = outputs[ats]
            self.assertEqual(
                set(observed), {"fields", "page", "text", "required_unknown", "errors", "attachments", "url"}
            )
            self.assertEqual(observed["required_unknown"], [])
            for key in ("fields", "errors", "attachments"):
                self.assertIsInstance(observed[key], list)
                self.assertEqual(observed[key], [])
            self.assertEqual(observed["page"], {"index": 1, "total": 1, "title": "Synthetic review"})
            self.assertEqual(observed["text"], "Synthetic page")
            self.assertEqual(observed["url"], URL)
            for name in ENGINES:
                with self.subTest(ats=ats, engine=name):
                    self.assertEqual(
                        self.evaluate(name, {"result": observed}, scripts[ats], expect_accepted=True), observed
                    )

    def test_nonempty_required_unknown_preserves_fields_page_and_attachments(self):
        audit = {**copy.deepcopy(self.audit), "required_unknown": ["missing-field-1"]}
        for name in ENGINES:
            with self.subTest(engine=name):
                result = self.evaluate(name, {"result": audit}, expect_accepted=True)
                self.assertEqual(result, audit)
                self.assertEqual(result["required_unknown"], ["missing-field-1"])
                self.assertEqual(result["fields"][0]["status"], "unknown")
                self.assertEqual(result["fields"][0]["value"], "")
                self.assertIs(result["fields"][0]["required"], True)

    def test_required_unknown_rejects_wrong_container_and_item_types_once(self):
        for name in ENGINES:
            for invalid in ("missing-field-1", None, [1], [{"id": "missing-field-1"}], ["missing-field-1", False]):
                with self.subTest(engine=name, metadata=invalid):
                    audit = {**copy.deepcopy(self.audit), "required_unknown": invalid}
                    with self.assertRaises(self.engines.InvalidResponse):
                        self.evaluate(name, {"result": audit})

    def test_extra_properties_and_nested_wrappers_still_fail_closed_once(self):
        audit = {**copy.deepcopy(self.audit), "required_unknown": []}
        invalid = [
            {"result": {**audit, "unexpected": True}},
            {"result": {"result": audit}},
            {"result": {**audit, "result": audit}},
            {"result": audit, "unexpected": True},
        ]
        for name in ENGINES:
            for index, payload in enumerate(invalid):
                with self.subTest(engine=name, payload=index):
                    with self.assertRaises(self.engines.InvalidResponse):
                        self.evaluate(name, payload)

    def test_legacy_audit_without_optional_metadata_remains_accepted(self):
        for name in ENGINES:
            for with_page in (False, True):
                with self.subTest(engine=name, page=with_page):
                    audit = copy.deepcopy(self.audit)
                    if not with_page:
                        audit.pop("page")
                    self.assertNotIn("required_unknown", audit)
                    self.assertEqual(self.evaluate(name, {"result": audit}, expect_accepted=True), audit)
