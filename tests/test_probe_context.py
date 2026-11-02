"""Offline regression for native MCP discovery under the task's baseline policy.

Giả định của người thực thi: dùng runner giả với bridge ảo phụ thuộc policy;
chỉ được sửa test nên ghi giả định tại đây thay vì sửa plan.
"""

import json
import subprocess
from unittest.mock import Mock, call, patch

from test_runtime_live import BROWSER_TOOL, RESULT, effective_config, task, values
from test_support import ReleaseCase


FEATURES = (
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
    "workspace_dependencies",
    "code_mode_host",
    "plugins",
    "enable_mcp_apps",
    "apps",
)
REAL_SERVER = "system.server@org/path:read"
BRIDGES = ("codex_app", "cua_repl", "virtual.bridge@org/path:read")
BROWSER_SERVER = "autoapply-local-browser"


class ProbeContextTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.engines = self.module("engines")
        self.executable = str(self.root / "synthetic-codex.exe")
        self.inherited = {
            "features": dict.fromkeys(FEATURES, True),
            "mcp_servers": {REAL_SERVER: {"command": "synthetic-inherited", "enabled": True}},
            "apps": {"synthetic.app": {"enabled": True}},
            "plugins": {"synthetic.plugin": {"enabled": True}},
            "model": "synthetic-user-model",
            "model_provider": "synthetic-user-provider",
            "approval_policy": "on-request",
        }
        self.probes, self.dispatches, self.phantoms = [], [], []
        self.runner = Mock(side_effect=self.process)
        for boundary in (
            patch.object(self.engines, "resolve_executable", return_value=[self.executable]),
            patch.object(
                self.engines, "_app_plugin_names", return_value=({"synthetic.app": set()}, {"synthetic.plugin"})
            ),
            patch.object(subprocess, "run", self.runner),
            patch.object(subprocess, "Popen", side_effect=AssertionError("No live processes")),
        ):
            boundary.start()
            self.addCleanup(boundary.stop)

    def virtual_bridges(self, config):
        features = config["features"]
        restricted = all(features.get(name) is False for name in ("apps", "browser_use", "computer_use"))
        return () if restricted else BRIDGES

    def process(self, argv, **kwargs):
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], self.executable, "Never call a different engine")
        self.assertIs(kwargs["shell"], False)
        config = effective_config(call(argv, **kwargs), self.inherited, {})
        if argv[1:4] == ["mcp", "list", "--json"]:
            self.probes.append((list(argv), dict(kwargs), config))
            self.assertNotIn("input", kwargs, "Inventory must not perform inference")
            inventory = [{"name": name, "enabled": True} for name in (REAL_SERVER, *self.virtual_bridges(config))]
            return subprocess.CompletedProcess(argv, 0, json.dumps(inventory), "")
        self.assertEqual(argv[1], "exec", "Only native inventory and one selected-engine exec are allowed")
        self.dispatches.append((list(argv), dict(kwargs), config))
        # Native exec validates transports even for disabled phantom entries.
        # Effective inherited transports disappear with their dynamic bridges.
        available = {REAL_SERVER, *self.virtual_bridges(config)}
        self.phantoms = [
            name
            for name, entry in config["mcp_servers"].items()
            if name not in available and not any(key in entry for key in ("command", "url"))
        ]
        if self.phantoms:
            return subprocess.CompletedProcess(argv, 1, "", "invalid transport in mcp_servers." + self.phantoms[0])
        return subprocess.CompletedProcess(argv, 0, json.dumps(RESULT), "")

    def assert_task_context(self, payload):
        engine = self.engines.Engine("codex", runner=self.runner, cwd=self.root, debug_url="http://127.0.0.1:9333")
        try:
            result = engine.run(payload)
        except self.engines.EngineError as error:
            self.fail(
                f"Native exec must succeed under the probe policy; category={error.category}, phantom IDs={self.phantoms}"
            )
        self.assertEqual(result, RESULT)
        self.assertEqual(len(self.probes), 1)
        self.assertEqual(len(self.dispatches), 1, "Exactly one inference, without retry/fallback")
        self.assertEqual(self.runner.call_count, 2)
        probe_argv, probe_kwargs, probe_config = self.probes[0]
        exec_argv, exec_kwargs, exec_config = self.dispatches[0]
        self.assertEqual(probe_kwargs["cwd"], self.root)
        self.assertEqual(exec_kwargs["cwd"], self.root)
        self.assertEqual(probe_kwargs.get("env"), exec_kwargs.get("env"))
        self.assertEqual(json.loads(exec_kwargs["input"].split("\n", 1)[1]), payload)
        self.assertNotIn("mcp_servers", {item.split("=", 1)[0] for item in values(probe_argv, "-c")})
        for flag in ("--output-schema", "--output-last-message", "--sandbox", "--skip-git-repo-check"):
            self.assertNotIn(flag, probe_argv, "Exec-only options cannot be passed to native inventory")
        baseline = [item for item in values(exec_argv, "-c") if item.split("=", 1)[0] != "mcp_servers"]
        self.assertEqual(values(probe_argv, "-c"), baseline, "Probe must use all baseline policy overrides")
        for config in (probe_config, exec_config):
            for feature in FEATURES:
                broker = feature in {"code_mode_host", "workspace_dependencies"}
                self.assertIs(config["features"][feature], broker and bool(payload["allowed_tools"]), feature)
            self.assertIs(config["agents"]["enabled"], False)
            self.assertEqual(config["web_search"], "disabled")
            self.assertIs(config["features"]["view_image"], False)
            self.assertEqual(config["browser_use"], {"default_origin_policy": {"access": "deny"}, "origins": {}})
            self.assertEqual(config["computer_use"], {"default_app_access": "deny"})
            for name in ("synthetic.app", "_default"):
                self.assertIs(config["apps"][name]["enabled"], False)
            self.assertIs(config["plugins"]["synthetic.plugin"]["enabled"], False)
            for key in ("model", "model_provider", "approval_policy"):
                self.assertEqual(config[key], self.inherited[key])
        servers = exec_config["mcp_servers"]
        expected = {REAL_SERVER, BROWSER_SERVER} if payload["allowed_tools"] else {REAL_SERVER}
        self.assertEqual(set(servers), expected, "Keep literal inventory IDs; never inject absent bridges")
        self.assertIs(servers[REAL_SERVER]["enabled"], False)
        self.assertEqual(servers[REAL_SERVER]["command"], "synthetic-inherited")
        if payload["allowed_tools"]:
            self.assertIs(servers[BROWSER_SERVER]["enabled"], True)
            self.assertEqual(servers[BROWSER_SERVER]["enabled_tools"], ["take_snapshot"])
        for flag in ("--yolo", "--full-auto", "--dangerously-bypass-approvals-and-sandbox", "--model", "-m"):
            self.assertNotIn(flag, exec_argv)
        self.assertEqual(values(exec_argv, "--sandbox"), ["read-only"])
        for argv in (probe_argv, exec_argv):
            self.assertNotIn("tools.view_image", {item.split("=", 1)[0] for item in values(argv, "-c")})

    def test_analysis_probes_with_baseline_before_disabling_inherited_servers(self):
        self.assert_task_context(task())

    def test_browser_probes_with_baseline_and_retains_only_selected_tool(self):
        self.assert_task_context(task("browser_snapshot", [BROWSER_TOOL]))
