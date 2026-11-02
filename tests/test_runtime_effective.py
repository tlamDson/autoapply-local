"""Offline regressions for autoapply-local-runtime-effective; no live CLI calls.

Giả định của người thực thi: giữ Engine.run và runner hiện có; native inventory
là JSON array của `mcp list --json`, chỉ name/enabled được dùng từ metadata.
Giả định của người thực thi: probe không dùng được phải trả configuration trước
inference; giữ context qua cwd/CODEX_HOME, cho phép probe kiểm lại sau override.
Chỉ được sửa test nên ghi giả định tại đây thay vì sửa plan.
"""

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, call, patch

from test_runtime_live import RESULT, effective_config, task, values
from test_support import ReleaseCase


class RuntimeEffectiveTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.engines = self.module("engines")
        self.original_sources = self.engines.configuration_sources
        self.home = self.root / "synthetic home tiếng Việt"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text("", encoding="utf-8")
        self.paths = {name: str(self.root / (name + ".exe")) for name in ("codex", "claude", "node")}
        self.inherited = {}
        self.inventory = []
        self.inventory_raw = None
        self.inventory_failure = None
        self.output = None
        self.probes, self.dispatches = [], []
        self.managed = False
        self.runner = Mock(side_effect=self.process)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, CODEX_HOME=str(self.home), HOME=str(self.root), USERPROFILE=str(self.root)).start()
        patch.object(Path, "home", return_value=self.root).start()
        patch.object(shutil, "which", side_effect=lambda name: self.paths.get(name)).start()
        patch.object(subprocess, "run", self.runner).start()
        patch.object(subprocess, "Popen", side_effect=AssertionError("No live processes in offline tests")).start()
        # Inject known config paths, not a claim that platform implies policy.
        patch.object(
            self.engines, "configuration_sources", side_effect=lambda cwd: ([self.config], self.managed)
        ).start()
        original_open = Path.open

        def guarded_open(path, *args, **kwargs):
            if path.name.casefold() in {"auth.json", "credentials.json", ".credentials.json"}:
                raise AssertionError("No auth or credential file reads")
            if path.name == "config.toml" and not path.is_relative_to(self.root):
                raise AssertionError("No workstation configuration reads")
            return original_open(path, *args, **kwargs)

        patch.object(Path, "open", guarded_open).start()

    def process(self, argv, **kwargs):
        self.assertIsInstance(argv, list)
        self.assertIs(kwargs.get("shell"), False)
        self.assertEqual(kwargs.get("cwd"), self.root)
        self.assertEqual(kwargs.get("encoding"), "utf-8")
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, "2.1.312", "")
        if "exec" not in argv and "--print" not in argv:
            self.assertIn("mcp", argv)
            self.assertIn("list", argv)
            self.assertIn("--json", argv)
            self.probes.append((list(argv), dict(kwargs)))
            self.assertGreater(kwargs.get("timeout", 0), 0)
            self.assertLessEqual(kwargs["timeout"], 30)
            self.assertNotIn("input", kwargs, "Inventory must not invoke model inference")
            if isinstance(self.inventory_failure, BaseException):
                raise self.inventory_failure
            if self.inventory_failure:
                return subprocess.CompletedProcess(argv, 1, "", self.inventory_failure)
            inventory = copy.deepcopy(self.inventory)
            if values(argv, "-c"):
                config = effective_config(call(argv, **kwargs), self.inherited, {})
                for entry in inventory:
                    entry["enabled"] = (
                        config.get("mcp_servers", {}).get(entry["name"], {}).get("enabled", entry["enabled"])
                    )
            raw = self.inventory_raw if self.inventory_raw is not None else json.dumps(inventory)
            return subprocess.CompletedProcess(argv, 0, raw, "")
        self.dispatches.append((list(argv), dict(kwargs)))
        raw = self.output
        if raw is None:
            result = RESULT
            if "--print" in argv:
                result = {"type": "result", "subtype": "success", "is_error": False, "structured_output": result}
            raw = json.dumps(result)
        for destination in values(argv, "--output-last-message"):
            Path(destination).write_text(raw, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, raw, "")

    def run_engine(self, name="codex", payload=None):
        return self.engines.Engine(name, runner=self.runner, cwd=self.root).run(payload or task())

    def assert_error(self, category, action):
        with self.assertRaises((self.engines.EngineError, self.engines.InvalidResponse)) as caught:
            action()
        self.assertEqual(caught.exception.category, category)
        self.assertLess(len(str(caught.exception)), 600)
        return str(caught.exception)

    def test_native_inventory_disables_effective_system_ids_on_supported_platforms(self):
        names = ("system.server", "app@org/path:read", "system-only")
        self.inventory = [
            {
                "name": name,
                "enabled": True,
                "transport": {
                    "type": "stdio",
                    "command": "synthetic-private-transport",
                    "env": {"PRIVATE_METADATA": "synthetic-private-env"},
                },
                "future_native_field": {"ignored": True},
            }
            for name in names
        ]
        self.inherited = {"mcp_servers": {name: {"enabled": True} for name in names}}
        before = self.config.read_bytes()

        def fixture_sources(cwd):
            # Exercise actual platform classification with every policy source
            # absent. Registry and filesystem policy lookups are synthetic too.
            registry = Mock(KEY_READ=1, KEY_WOW64_64KEY=2)
            registry.OpenKey.side_effect = FileNotFoundError
            with (
                patch.object(Path, "stat", side_effect=FileNotFoundError),
                patch.dict(sys.modules, {"winreg": registry}),
            ):
                _, managed = self.original_sources(cwd)
            return [self.config], managed

        for platform in ("darwin", "win32", "linux"):
            with (
                self.subTest(platform=platform),
                patch.object(sys, "platform", platform),
                patch.object(self.engines, "configuration_sources", side_effect=fixture_sources),
            ):
                self.probes.clear()
                self.dispatches.clear()
                try:
                    result = self.run_engine()
                except self.engines.EngineError as error:
                    self.fail(f"Verified native inventory must support {platform}: {error}")
                self.assertEqual(result, RESULT)
                self.assertTrue(self.probes, "File-only discovery is not effective MCP inventory")
                argv, kwargs = self.dispatches[-1]
                config = effective_config(call(argv, **kwargs), self.inherited, {})
                self.assertEqual(set(config["mcp_servers"]), set(names), "IDs must remain literal map keys")
                for name in names:
                    self.assertIs(config["mcp_servers"][name]["enabled"], False)
                for probe_argv, probe_kwargs in self.probes:
                    self.assertEqual(probe_argv[0], argv[0])
                    context = probe_kwargs.get("env") or os.environ
                    self.assertEqual(context["CODEX_HOME"], str(self.home))
                serialized = json.dumps([argv, kwargs], default=str)
                for marker in ("synthetic-private-transport", "synthetic-private-env", "future_native_field"):
                    self.assertNotIn(marker, serialized, "Only names/flags may cross into model dispatch")
                self.assertEqual(values(argv, "--sandbox"), ["read-only"])
                for flag in ("--yolo", "--full-auto", "--dangerously-bypass-approvals-and-sandbox", "--model"):
                    self.assertNotIn(flag, argv)
                self.assertEqual(self.config.read_bytes(), before)

    def test_invalid_inventory_fails_closed_before_model_dispatch(self):
        for raw in (
            "not JSON",
            "null",
            "{}",
            '[{"enabled":true}]',
            '[{"name":"system-only","enabled":"true"}]',
            '[{"name":42,"enabled":true}]',
        ):
            with self.subTest(raw=raw):
                self.probes.clear()
                self.dispatches.clear()
                self.inventory_raw = raw
                self.assert_error("configuration", self.run_engine)
                self.assertTrue(self.probes)
                self.assertEqual(self.dispatches, [], "No inference with unverified inventory")

    def test_unavailable_inventory_fails_closed_without_sensitive_output(self):
        for failure in (
            "configuration unavailable synthetic-private-diagnostic",
            FileNotFoundError("synthetic-private-diagnostic"),
            subprocess.TimeoutExpired(["synthetic-probe"], 10, output="synthetic-private-diagnostic"),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.probes.clear()
                self.dispatches.clear()
                self.inventory_failure = failure
                message = self.assert_error("configuration", self.run_engine)
                self.assertTrue(self.probes)
                self.assertEqual(self.dispatches, [])
                self.assertNotIn("synthetic-private-diagnostic", message)

    def test_managed_restriction_is_actionable_and_never_bypassed(self):
        self.managed = True
        self.inventory = [{"name": "required-system", "enabled": True}]
        self.inventory_failure = "configuration requirements prohibit disabling MCP synthetic-private-diagnostic"
        message = self.assert_error("configuration", self.run_engine)
        self.assertRegex(message.casefold(), "manual_required|manual|configuration")
        self.assertNotIn("synthetic-private-diagnostic", message)
        self.assertEqual(self.dispatches, [])
        self.assertEqual(self.config.read_text(encoding="utf-8"), "")

    def test_all_observed_capability_flags_disabled_without_unsupported_aliases(self):
        features = (
            "shell_tool",
            "apps",
            "multi_agent",
            "browser_use",
            "computer_use",
            "remote_plugin",
            "code_mode",
            "view_image",
            "workspace_dependencies",
            "code_mode_host",
        )
        inherited = {"features": {name: True for name in features}}
        for kind in ("apply_analysis", "tailor"):
            with self.subTest(kind=kind):
                self.assertEqual(self.run_engine(payload=task(kind)), RESULT)
                argv, kwargs = self.dispatches[-1]
                config = effective_config(call(argv, **kwargs), inherited, {})
                for feature in features:
                    with self.subTest(feature=feature):
                        self.assertIs(config["features"][feature], False)
                override_keys = {item.split("=", 1)[0] for item in values(argv, "-c")}
                for alias in ("features.shell", "features.python", "tools.view_image"):
                    with self.subTest(alias=alias):
                        self.assertNotIn(alias, override_keys)

    def test_native_permission_denials_precede_structured_output_validation(self):
        for structured in (RESULT, {"wrong_schema": True}):
            with self.subTest(structured=structured):
                self.output = json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "structured_output": structured,
                        "permission_denials": [
                            {
                                "tool_name": "mcp__autoapply-local-browser__list_pages",
                                "tool_input": {"account": "alex@example.invalid", "marker": "synthetic-private-input"},
                            }
                        ],
                    }
                )
                with patch.object(self.engines, "validate_schema", wraps=self.engines.validate_schema) as validate:
                    message = self.assert_error(
                        "permission_required",
                        lambda: self.run_engine(
                            "claude", task("browser_list", ["mcp__autoapply-local-browser__list_pages"])
                        ),
                    )
                validate.assert_not_called()
                self.assertRegex(message.casefold(), "approv|permission")
                for marker in ("alex@example.invalid", "synthetic-private-input", "tool_input"):
                    self.assertNotIn(marker, message)
                argv, _ = self.dispatches[-1]
                for flag in ("--allowedTools", "--dangerously-skip-permissions", "--permission-mode"):
                    self.assertNotIn(flag, argv)

    def test_empty_permission_denials_preserve_valid_success_envelope(self):
        self.output = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": RESULT,
                "permission_denials": [],
            }
        )
        self.assertEqual(self.run_engine("claude"), RESULT)

    def npm_local(self, name, relative, *, metadata_name=None, string_bin=False):
        modules = self.root / "npm local tiếng Việt & tools" / "node_modules"
        shim = modules / ".bin" / (name + ".cmd")
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_text("rem synthetic shim; never execute", encoding="utf-8")
        package_name = {"codex": "@openai/codex", "claude": "@anthropic-ai/claude-code"}[name]
        package = modules / package_name
        package.mkdir(parents=True, exist_ok=True)
        (package / "package.json").write_text(
            json.dumps(
                {
                    "name": metadata_name or package_name,
                    "bin": relative if string_bin else {name: relative},
                }
            ),
            encoding="utf-8",
        )
        entry = package / relative
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(b"synthetic entry; never execute")
        self.paths[name] = str(shim)
        return entry.resolve()

    def test_npm_local_bin_js_uses_native_node_and_preserves_unicode_argv(self):
        for name in ("codex", "claude"):
            with self.subTest(engine=name):
                self.probes.clear()
                entry = self.npm_local(name, "bin/entry.mjs")
                payload = task()
                payload["prompt"] = 'Tiếng Việt & | $ ` ; "quoted" \\path\nsecond line'
                try:
                    result = self.run_engine(name, payload)
                except self.engines.EngineError as error:
                    self.fail(f"Supported npm local .bin JavaScript fixture rejected: {error}")
                self.assertEqual(result, RESULT)
                argv, kwargs = self.dispatches[-1]
                self.assertEqual(argv[:2], [self.paths["node"], str(entry)])
                self.assertIs(kwargs["shell"], False)
                self.assertEqual(json.loads(kwargs["input"].split("\n", 1)[1])["prompt"], payload["prompt"])
                for probe_argv, _ in self.probes:
                    self.assertEqual(probe_argv[:2], argv[:2])

    def test_npm_local_native_bin_metadata_does_not_require_node(self):
        self.paths.pop("node")
        for name in ("codex", "claude"):
            with self.subTest(engine=name):
                entry = self.npm_local(name, "bin/entry.exe", string_bin=True)
                try:
                    result = self.run_engine(name)
                except self.engines.EngineError as error:
                    self.fail(f"Supported npm local .bin native fixture rejected: {error}")
                self.assertEqual(result, RESULT)
                argv, kwargs = self.dispatches[-1]
                self.assertEqual(argv[0], str(entry))
                self.assertIs(kwargs["shell"], False)

    def test_npm_local_metadata_and_traversal_checks_fail_before_processes(self):
        for relative, metadata_name in (("../../escape.js", None), ("entry.js", "unknown-package")):
            with self.subTest(relative=relative):
                self.npm_local("codex", relative, metadata_name=metadata_name)
                self.runner.reset_mock()
                self.assert_error("configuration", self.run_engine)
                self.runner.assert_not_called()
