"""Offline native-broker regression; no real CLI, browser or connector calls.

Giả định của người thực thi: dùng runner giả trả JSON observed/unknown theo
hai cờ broker và đúng operation được chọn; chỉ được sửa test nên ghi ở đây.
"""

import json
import subprocess
from unittest.mock import Mock, call, patch

from test_runtime_live import effective_config, values
from test_support import ReleaseCase


BROKER = ("code_mode_host", "workspace_dependencies")
DISABLED = (
    "shell_tool",
    "multi_agent",
    "remote_plugin",
    "hooks",
    "js_repl",
    "apply_patch_freeform",
    "image_generation",
    "remote_control",
    "skill_mcp_dependency_install",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "code_mode",
    "view_image",
    "plugins",
    "enable_mcp_apps",
)
SERVER = "autoapply-local-browser"
TOOL = "mcp__" + SERVER + "__list_pages"
MARKER = "synthetic owned page — tiếng Việt"
UNKNOWN = {"status": "unknown", "page": ""}
OBSERVED = {"status": "observed", "page": MARKER}
SCHEMA = {
    "type": "object",
    "required": ["status", "page"],
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["observed", "unknown"]},
        "page": {"type": "string"},
    },
}


class McpBrokerTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.engines = self.module("engines")
        self.executable = str(self.root / "synthetic-codex.exe")
        self.inherited = {
            "features": dict.fromkeys((*DISABLED, *BROKER, "apps"), True),
            "mcp_servers": {
                name: {"enabled": True, "command": "synthetic-unused-transport"}
                for name in ("unrelated@org/path:read", "gmail")
            },
            "model": "synthetic-user-model",
            "approval_policy": "on-request",
        }
        self.probes, self.dispatches, self.tool_calls = [], [], []
        self.unavailable = ()
        self.runner = Mock(side_effect=self.process)
        for boundary in (
            patch.object(self.engines, "resolve_executable", return_value=[self.executable]),
            patch.object(
                self.engines,
                "_app_plugin_names",
                return_value=({"gmail": {"read_email", "send_email"}, "unrelated": set()}, {"unrelated"}),
            ),
            patch.object(subprocess, "run", self.runner),
            patch.object(subprocess, "Popen", side_effect=AssertionError("No live processes")),
        ):
            boundary.start()
            self.addCleanup(boundary.stop)
        self.engine = self.engines.Engine("codex", runner=self.runner, cwd=self.root, debug_url="http://127.0.0.1:9333")

    def payload(self, kind="browser_list", tools=(TOOL,)):
        return {
            "kind": kind,
            "allowed_tools": list(tools),
            "schema": SCHEMA,
            "prompt": json.dumps({"account": "alex@example.invalid"}),
        }

    def process(self, argv, **kwargs):
        self.assertEqual(argv[0], self.executable, "No engine or model fallback")
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["cwd"], self.root)
        config = effective_config(call(argv, **kwargs), self.inherited, {})
        if argv[1:4] == ["mcp", "list", "--json"]:
            self.assertNotIn("input", kwargs)
            self.probes.append((list(argv), dict(kwargs), config))
            inventory = [{"name": name, "enabled": True} for name in self.inherited["mcp_servers"]]
            return subprocess.CompletedProcess(argv, 0, json.dumps(inventory), "")
        self.assertEqual(argv[1], "exec")
        self.dispatches.append((list(argv), dict(kwargs), config))
        requested = json.loads(kwargs["input"].split("\n", 1)[1])["allowed_tools"]
        ready = all(config["features"].get(name) is True and name not in self.unavailable for name in BROKER)
        server = config.get("mcp_servers", {}).get(SERVER, {})
        can_observe = (
            ready
            and requested == [TOOL]
            and server.get("enabled") is True
            and server.get("enabled_tools") == ["list_pages"]
        )
        if can_observe:
            self.tool_calls.append(TOOL)
        # Reproduce exit-zero/unknown with no MCP call when either prerequisite
        # is missing, rather than making a successful process imply tool access.
        return subprocess.CompletedProcess(argv, 0, json.dumps(OBSERVED if can_observe else UNKNOWN), "")

    def test_selected_mcp_operation_observes_owned_page(self):
        self.assertEqual(self.engine.run(self.payload()), OBSERVED)
        self.assertEqual(self.tool_calls, [TOOL])
        self.assertEqual(self.runner.call_count, 2, "One inventory and one inference, without retry")

    def test_missing_either_broker_prerequisite_returns_unknown_without_tool_call(self):
        for missing in ((BROKER[0],), (BROKER[1],), BROKER):
            with self.subTest(missing=missing):
                self.unavailable = missing
                self.tool_calls.clear()
                self.assertEqual(self.engine.run(self.payload()), UNKNOWN)
                self.assertEqual(self.tool_calls, [])

    def test_browser_inventory_and_exec_share_broker_policy_and_exact_capabilities(self):
        self.engine.run(self.payload())
        probe_argv, probe_kwargs, probe_config = self.probes[0]
        exec_argv, exec_kwargs, exec_config = self.dispatches[0]
        baseline = [item for item in values(exec_argv, "-c") if item.split("=", 1)[0] != "mcp_servers"]
        self.assertEqual(values(probe_argv, "-c"), baseline)
        self.assertEqual(probe_kwargs.get("env"), exec_kwargs.get("env"))
        for config in (probe_config, exec_config):
            for name in BROKER:
                with self.subTest(stage="probe" if config is probe_config else "exec", feature=name):
                    self.assertIs(config["features"].get(name), True)
            for name in (*DISABLED, "apps"):
                self.assertIs(config["features"].get(name), False, name)
            self.assertIs(config["agents"]["enabled"], False)
            self.assertEqual(config["web_search"], "disabled")
            self.assertEqual(config["browser_use"], {"default_origin_policy": {"access": "deny"}, "origins": {}})
            self.assertEqual(config["computer_use"], {"default_app_access": "deny"})
            self.assertTrue(all(entry["enabled"] is False for entry in config["apps"].values()))
            self.assertIs(config["plugins"]["unrelated"]["enabled"], False)
            for key in ("model", "approval_policy"):
                self.assertEqual(config[key], self.inherited[key])
        servers = exec_config["mcp_servers"]
        self.assertEqual(set(servers), {*self.inherited["mcp_servers"], SERVER})
        self.assertEqual({name for name, entry in servers.items() if entry["enabled"]}, {SERVER})
        self.assertEqual(servers[SERVER]["enabled_tools"], ["list_pages"])
        self.assertEqual(values(exec_argv, "--sandbox"), ["read-only"])
        for flag in ("--yolo", "--full-auto", "--dangerously-bypass-approvals-and-sandbox", "--model", "-m"):
            self.assertNotIn(flag, exec_argv)

    def test_analysis_tailor_and_empty_tool_tasks_keep_broker_off(self):
        for kind in ("apply_analysis", "tailor", "browser_list", "mail_discovery", "mail_search", "mail_read"):
            with self.subTest(kind=kind):
                self.probes.clear()
                self.dispatches.clear()
                self.runner.reset_mock()
                if kind in ("mail_search", "mail_read"):
                    with self.assertRaises(self.engines.EngineError) as caught:
                        self.engine.run(self.payload(kind, ()))
                    self.assertEqual(caught.exception.category, "configuration")
                    self.assertIn("manual_required", str(caught.exception))
                    self.runner.assert_not_called()
                    self.assertEqual(self.probes, [])
                    self.assertEqual(self.dispatches, [])
                    self.assertEqual(self.tool_calls, [])
                    continue
                self.assertEqual(self.engine.run(self.payload(kind, ())), UNKNOWN)
                for _, _, config in (*self.probes, *self.dispatches):
                    for name in BROKER:
                        self.assertIs(config["features"].get(name), False, name)
                servers = self.dispatches[0][2]["mcp_servers"]
                self.assertNotIn(SERVER, servers)
                self.assertEqual(self.tool_calls, [])

    def test_explicit_native_connector_tools_require_both_broker_flags(self):
        for kind, operation in (
            ("mail_discovery", "get_profile"),
            ("mail_search", "search_email_ids"),
            ("mail_read", "read_email"),
        ):
            with self.subTest(kind=kind):
                self.probes.clear()
                self.dispatches.clear()
                self.runner.reset_mock()
                if kind in ("mail_search", "mail_read"):
                    with self.assertRaises(self.engines.EngineError) as caught:
                        self.engine.run(self.payload(kind, ["mcp__gmail__" + operation]))
                    self.assertEqual(caught.exception.category, "configuration")
                    self.assertIn("manual_required", str(caught.exception))
                    self.runner.assert_not_called()
                    self.assertEqual(self.probes, [])
                    self.assertEqual(self.dispatches, [])
                    self.assertEqual(self.tool_calls, [])
                    continue
                self.engine.run(self.payload(kind, ["mcp__gmail__" + operation]))
                for _, _, config in (*self.probes, *self.dispatches):
                    for name in BROKER:
                        with self.subTest(feature=name):
                            self.assertIs(config["features"].get(name), True)
                servers = self.dispatches[0][2]["mcp_servers"]
                self.assertIs(servers["gmail"]["enabled"], True)
                if kind != "mail_discovery":
                    self.assertEqual(servers["gmail"]["enabled_tools"], [operation])
                self.assertIs(servers["unrelated@org/path:read"]["enabled"], False)

    def test_ignored_view_image_alias_is_absent_and_effective_gate_stays_off(self):
        for payload in (self.payload(), self.payload("apply_analysis", ())):
            with self.subTest(kind=payload["kind"]):
                self.probes.clear()
                self.dispatches.clear()
                self.engine.run(payload)
                for argv, _, config in (*self.probes, *self.dispatches):
                    self.assertIs(config["features"]["view_image"], False)
                    self.assertNotIn("tools.view_image", {item.split("=", 1)[0] for item in values(argv, "-c")})

    def test_unsupported_requests_are_rejected_before_model_dispatch(self):
        cases = (
            ("browser_list", "mcp__" + SERVER + "__unknown_operation", PermissionError),
            ("browser_list", "mcp__unrelated__list_pages", PermissionError),
            ("mail_read", "mcp__gmail__send_email", PermissionError),
            ("apply_analysis", TOOL, ValueError),
            ("tailor", TOOL, ValueError),
        )
        for kind, tool, error in cases:
            with self.subTest(kind=kind, tool=tool):
                self.dispatches.clear()
                with self.assertRaises(error):
                    self.engine.run(self.payload(kind, [tool]))
                self.assertEqual(self.dispatches, [])
                self.assertEqual(self.tool_calls, [])
