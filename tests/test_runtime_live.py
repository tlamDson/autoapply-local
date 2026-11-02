"""Offline regressions for the local-runtime-live plan; never invoke a model.

Giả định của người thực thi: giữ Engine.run(task) và CLI services hiện có;
probe phiên bản đi qua subprocess được mock, lỗi có mã ở category/code hoặc
thông báo, config không cô lập an toàn được thì phải từ chối trước inference.
Chỉ được sửa test nên ghi giả định ở đây thay vì sửa plan. Dotted override keys
được tách như CLI thực tế, không dùng TOML quoted-key parsing đã bị bác bỏ.

Giả định của người thực thi: fixture npm native dùng bin/claude.exe không cần
Node; MCP Windows dùng cmd /c npx với env hệ thống và timeout tối thiểu 20 giây
theo nguồn trong plan, chấp nhận đơn vị ms hoặc sec của client.
"""

import copy
import io
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import Mock, patch

from test_support import ReleaseCase


SCHEMA = {
    "type": "object",
    "required": ["status"],
    "additionalProperties": False,
    "properties": {"status": {"type": "string", "enum": ["completed"]}},
}
RESULT = {"status": "completed"}
BROWSER_TOOL = "mcp__autoapply-local-browser__take_snapshot"


def task(kind="apply_analysis", tools=()):
    return {"kind": kind, "allowed_tools": list(tools), "schema": copy.deepcopy(SCHEMA), "prompt": "Synthetic job"}


def values(argv, flag):
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == flag]


def effective_config(call, inherited, managed):
    """Emulate CLI dotted overrides: quotes in path components are literal.

    A fresh CODEX_HOME may supply invocation-local configuration instead. All
    paths inspected here are temporary fixtures or the runtime's temp config.
    """

    def merge(target, overlay):
        for key, value in overlay.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = copy.deepcopy(value)

    config = copy.deepcopy(inherited)
    home = call.kwargs.get("env", {}).get("CODEX_HOME")
    if home:
        path = Path(home) / "config.toml"
        config = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for section, entries in managed.items():
        config.setdefault(section, {}).update(copy.deepcopy(entries))
    for override in values(call.args[0], "-c"):
        key, raw = override.split("=", 1)
        value = tomllib.loads("value=" + raw)["value"]
        target = config
        parts = key.strip().split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        if isinstance(value, dict) and isinstance(target.get(parts[-1]), dict):
            merge(target[parts[-1]], value)
        else:
            target[parts[-1]] = value
    return config


class RuntimeLiveTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.engines = self.module("engines")
        self.home = self.root / "synthetic-home"
        self.home.mkdir()
        self.config_path = self.home / "config.toml"
        self.config_path.write_text("", encoding="utf-8")
        self.bin = self.root / "synthetic bin tiếng Việt & tools"
        self.bin.mkdir()
        self.paths = {}
        for name in ("codex", "claude", "node", "npx"):
            path = self.bin / (name + ".exe")
            path.write_bytes(b"synthetic executable; never run")
            self.paths[name] = str(path)
        self.version = "2.1.312"
        self.output = None
        self.failure = None
        self.inference_calls = []
        self.configs_at_dispatch = []
        self.managed_config = {}
        self.inventory_failure = None
        self.runner = Mock(side_effect=self.fake_process)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, CODEX_HOME=str(self.home), HOME=str(self.root), USERPROFILE=str(self.root)).start()
        patch.object(Path, "home", return_value=self.root).start()

        def which(name, *args, **kwargs):
            name = str(name)
            return self.paths.get(name.removesuffix(".exe").removesuffix(".cmd"))

        patch.object(shutil, "which", side_effect=which).start()
        patch.object(subprocess, "run", self.runner).start()
        patch.object(subprocess, "Popen", side_effect=AssertionError("Offline tests cannot launch processes")).start()

        def configuration_sources(cwd):
            return [self.config_path, self.root / ".codex/config.toml"], bool(self.managed_config)

        # Capability discovery is injected just like version/process probes;
        # managed fixtures model a layer whose precedence cannot be overridden.
        patch.object(self.engines, "configuration_sources", side_effect=configuration_sources, create=True).start()
        original_open = Path.open

        def guarded_open(path, *args, **kwargs):
            if path.name.casefold() in {"auth.json", "credentials.json", ".credentials.json"}:
                raise AssertionError("Runtime must not read or copy credentials")
            if path.name == "config.toml" and not path.is_relative_to(self.root):
                # Ancestor discovery must never read the workstation's config.
                mode = args[0] if args else kwargs.get("mode", "r")
                return io.BytesIO(b"") if "b" in mode else io.StringIO("")
            return original_open(path, *args, **kwargs)

        patch.object(Path, "open", guarded_open).start()

    def fake_process(self, argv, **kwargs):
        self.assertIsInstance(argv, list, "No string-built shell commands")
        self.assertIs(kwargs.get("shell", False), False)
        if "--version" in argv or "-V" in argv:
            return subprocess.CompletedProcess(argv, 0, self.version + " (synthetic)", "")
        if "status" in argv and "--print" not in argv and "exec" not in argv:
            return subprocess.CompletedProcess(argv, 1, "", "Not logged in")
        # Native inventory is metadata, not inference. Model failures below
        # must not accidentally turn valid inventory into a malformed envelope.
        if "mcp" in argv and "list" in argv and "--json" in argv:
            if self.inventory_failure:
                return subprocess.CompletedProcess(argv, 1, "", self.inventory_failure)
            config = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
            project = self.root / ".codex/config.toml"
            if project.exists():
                for section, entries in tomllib.loads(project.read_text(encoding="utf-8")).items():
                    config.setdefault(section, {}).update(entries)
            from unittest.mock import call

            config = effective_config(call(argv, **kwargs), config, self.managed_config)
            inventory = [
                {"name": name, "enabled": entry.get("enabled", True), "transport": {"type": "stdio"}}
                for name, entry in config.get("mcp_servers", {}).items()
            ]
            return subprocess.CompletedProcess(argv, 0, json.dumps(inventory), "")
        self.inference_calls.append((list(argv), dict(kwargs)))
        if "exec" in argv:
            inherited = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
            # Snapshot while invocation-local config still exists.
            from unittest.mock import call

            project = self.root / ".codex/config.toml"
            if project.exists():
                for section, entries in tomllib.loads(project.read_text(encoding="utf-8")).items():
                    inherited.setdefault(section, {}).update(entries)
            self.configs_at_dispatch.append(effective_config(call(argv, **kwargs), inherited, self.managed_config))
        if self.failure:
            if isinstance(self.failure, BaseException):
                raise self.failure
            code, stderr = self.failure
            return subprocess.CompletedProcess(argv, code, json.dumps(RESULT), stderr)
        raw = self.output
        if raw is None:
            raw = json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "structured_output": RESULT}
                if "--print" in argv
                else RESULT
            )
        destinations = values(argv, "--output-last-message")
        if destinations:
            Path(destinations[0]).write_text(raw, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, raw, "")

    def run_engine(self, name="codex", payload=None):
        return self.engines.Engine(name, runner=self.runner, cwd=self.root, debug_url="http://127.0.0.1:9333").run(
            payload or task()
        )

    def assert_category(self, category, action):
        with self.assertRaises((ValueError, RuntimeError)) as caught:
            action()
        error = caught.exception
        diagnostic = " ".join(
            str(value) for value in (getattr(error, "category", ""), getattr(error, "code", ""), error)
        )
        self.assertIn(category, diagnostic)
        return error

    def test_non_git_runtime_keeps_read_only_sandbox_and_normal_approvals(self):
        self.assertFalse((self.root / ".git").exists())
        self.assertEqual(self.run_engine(), RESULT)
        argv, _ = self.inference_calls[-1]
        self.assertIn("--skip-git-repo-check", argv)
        self.assertEqual(values(argv, "--sandbox"), ["read-only"])
        self.assertFalse((self.root / ".git").exists())
        for flag in ("--yolo", "--dangerously-bypass-approvals-and-sandbox", "--full-auto", "--model", "-m"):
            self.assertNotIn(flag, argv)

    def test_inherited_names_disable_real_entries_without_quote_literal_phantoms(self):
        names = ("chrome-devtools", "other.server", "app@org/name:read")
        self.config_path.write_text(
            "\n".join("[mcp_servers." + json.dumps(name) + ']\ncommand="unused"\nenabled=true' for name in names),
            encoding="utf-8",
        )
        before = self.config_path.read_bytes()
        try:
            result = self.run_engine()
        except (ValueError, RuntimeError) as error:
            self.assertIn("configuration", str(error).casefold())
            self.assertFalse(self.inference_calls, "Unsafe config must fail before inference")
        else:
            self.assertEqual(result, RESULT)
            servers = self.configs_at_dispatch[-1].get("mcp_servers", {})
            self.assertFalse(set(servers) - set(names), "Quoted identifiers must not create phantom entries")
            for name in names:
                self.assertIs(servers.get(name, {}).get("enabled", False), False, name)
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_project_connectors_and_builtin_capabilities_are_disabled_for_analysis_and_tailor(self):
        project = self.root / ".codex"
        project.mkdir()
        (project / "config.toml").write_text(
            '[mcp_servers.project_private]\ncommand="unused"\nenabled=true\n', encoding="utf-8"
        )
        self.config_path.write_text(
            # Runtime evidence disproved the shell alias; shell_tool is the
            # supported isolation flag and remains required below.
            "[features]\nshell_tool=true\napps=true\nmulti_agent=true\n"
            "[agents]\nenabled=true\n[apps.gmail]\nenabled=true\n"
            '[plugins."reader@org/path:v1"]\nenabled=true\n',
            encoding="utf-8",
        )
        for kind in ("apply_analysis", "tailor"):
            with self.subTest(kind=kind):
                self.assertEqual(self.run_engine(payload=task(kind)), RESULT)
                config = self.configs_at_dispatch[-1]
                for feature in ("shell_tool", "apps", "multi_agent"):
                    self.assertIs(config.get("features", {}).get(feature), False, feature)
                self.assertIs(config.get("agents", {}).get("enabled"), False)
                self.assertEqual(config.get("web_search"), "disabled")
                self.assertIs(config.get("apps", {}).get("gmail", {}).get("enabled", False), False)
                self.assertIs(config.get("plugins", {}).get("reader@org/path:v1", {}).get("enabled", False), False)
                self.assertIs(config.get("mcp_servers", {}).get("project_private", {}).get("enabled", False), False)

    def test_native_browser_computer_code_and_plugin_capabilities_are_disabled(self):
        features = ("browser_use", "browser_use_external", "computer_use", "code_mode", "plugins", "enable_mcp_apps")
        self.config_path.write_text("[features]\n" + "\n".join(name + "=true" for name in features), encoding="utf-8")
        for kind in ("apply_analysis", "tailor"):
            with self.subTest(kind=kind):
                self.assertEqual(self.run_engine(payload=task(kind)), RESULT)
                for name in features:
                    with self.subTest(feature=name):
                        self.assertIs(self.configs_at_dispatch[-1]["features"].get(name), False)

    def test_selected_native_mail_app_does_not_inherit_enabled_unselected_tools(self):
        self.config_path.write_text(
            "[apps.gmail]\nenabled=true\n"
            "[apps.gmail.tools.send_email]\nenabled=true\n"
            "[apps.gmail.tools.search_email_ids]\nenabled=true\n",
            encoding="utf-8",
        )
        payload = task("mail_read", ["mcp__gmail__read_email"])
        payload["prompt"] = json.dumps({"account": "alex@example.invalid"})
        error = self.assert_category("configuration", lambda: self.run_engine(payload=payload))
        self.assertIn("manual_required", str(error))
        self.runner.assert_not_called()
        self.assertEqual(self.configs_at_dispatch, [])
        self.assertEqual(self.inference_calls, [])

    def test_browser_server_collision_fails_before_inheriting_unknown_transport_settings(self):
        self.config_path.write_text(
            '[mcp_servers.autoapply-local-browser]\ncommand="unused"\n'
            '[mcp_servers.autoapply-local-browser.env]\nNODE_OPTIONS="--require=untrusted.js"\n',
            encoding="utf-8",
        )
        self.assert_category("configuration", lambda: self.run_engine(payload=task("browser_evaluate", [BROWSER_TOOL])))
        self.assertFalse(self.inference_calls)

    def test_browser_config_exposes_only_selected_mcp_operation(self):
        self.assertEqual(self.run_engine(payload=task("browser_evaluate", [BROWSER_TOOL])), RESULT)
        config = self.configs_at_dispatch[-1]
        server = config["mcp_servers"]["autoapply-local-browser"]
        self.assertIs(server["enabled"], True)
        self.assertEqual(server["enabled_tools"], ["take_snapshot"])
        self.assertIs(config.get("features", {}).get("multi_agent"), False)
        self.assertIs(config.get("agents", {}).get("enabled"), False)

    def test_windows_mcp_config_uses_fixed_command_env_and_startup_timeout(self):
        browser = self.module("browser")
        windows_env = {"SystemRoot": "C:\\Windows", "PROGRAMFILES": "C:\\Program Files"}
        debug_url = "http://127.0.0.1:9333"
        with patch("platform.system", return_value="Windows"), patch.dict(os.environ, windows_env):
            server = browser.mcp_config("codex", debug_url)["mcp_servers"]["autoapply-local-browser"]
        self.assertIn(Path(server["command"]).name.casefold(), ("cmd", "cmd.exe"))
        self.assertEqual(
            server["args"],
            ["/c", "npx", "-y", f"chrome-devtools-mcp@{browser.MCP_VERSION}", f"--browserUrl={debug_url}"],
        )
        environment = {key.casefold(): value for key, value in server.get("env", {}).items()}
        for key, value in windows_env.items():
            self.assertEqual(environment.get(key.casefold()), value)
        timeout_seconds = server.get("startup_timeout_sec", server.get("startup_timeout_ms", 0) / 1000)
        self.assertGreaterEqual(timeout_seconds, 20)

    def test_browser_dispatch_preserves_required_mcp_env_and_timeout(self):
        server = {
            "command": "cmd",
            "args": ["/c", "npx", "-y", "chrome-devtools-mcp@0.14.0", "--browserUrl=http://127.0.0.1:9333"],
            "env": {"SystemRoot": "C:\\Windows", "PROGRAMFILES": "C:\\Program Files"},
            "startup_timeout_sec": 20,
        }
        with patch(
            "autoapply_local.browser.mcp_config",
            return_value={"mcp_servers": {"autoapply-local-browser": copy.deepcopy(server)}},
        ):
            self.assertEqual(self.run_engine(payload=task("browser_evaluate", [BROWSER_TOOL])), RESULT)
        dispatched = self.configs_at_dispatch[-1]["mcp_servers"]["autoapply-local-browser"]
        for key, value in server.items():
            with self.subTest(setting=key):
                self.assertEqual(dispatched.get(key), value)
        self.assertEqual(dispatched["enabled_tools"], ["take_snapshot"])

    def test_windows_mcp_rejects_shell_text_in_debug_urls(self):
        browser = self.module("browser")
        for suffix in ("/&echo synthetic", "/|whoami", "/%26echo%20synthetic", "?x=synthetic", "#synthetic"):
            with self.subTest(suffix=suffix), patch("platform.system", return_value="Windows"):
                with self.assertRaises(ValueError):
                    browser.mcp_config("codex", "http://127.0.0.1:9333" + suffix)

    def test_unenumerated_managed_connectors_are_disabled_or_fail_closed_before_inference(self):
        # Effective layers may contain entries absent from user/project config.
        # Resetting CODEX_HOME alone does not prove these entries are isolated.
        self.managed_config = {
            "mcp_servers": {"managed-private": {"command": "unused", "enabled": True}},
            "apps": {"managed-app": {"enabled": True}},
            "plugins": {"managed-plugin": {"enabled": True}},
        }
        # Unknown managed inventory remains unsafe because the native probe is
        # unavailable, not merely because the host has a particular platform.
        self.inventory_failure = "configuration: effective inventory unavailable"
        try:
            self.run_engine()
        except (ValueError, RuntimeError) as error:
            self.assertIn("configuration", str(error).casefold())
            self.assertFalse(self.inference_calls)
        else:
            config = self.configs_at_dispatch[-1]
            for section, entries in self.managed_config.items():
                for name in entries:
                    with self.subTest(section=section, name=name):
                        self.assertIs(config.get(section, {}).get(name, {}).get("enabled", False), False)

    def test_structured_output_exact_name_is_allowed_by_invocation_gate(self):
        self.assertEqual(self.run_engine("claude", task("browser_evaluate", [BROWSER_TOOL])), RESULT)
        argv, _ = self.inference_calls[-1]
        settings = json.loads(values(argv, "--settings")[0])
        hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(hook["onFailure"], "block")
        policy = json.loads(hook["args"][-1])
        gate = self.module("tool_gate")
        self.assertTrue(gate.permitted(policy, {"tool_name": "StructuredOutput", "tool_input": RESULT}))
        for name in ("StructuredOutputExtra", "structuredoutput", "mcp__evil__StructuredOutput", "Bash", "Agent"):
            with self.subTest(tool=name):
                self.assertFalse(gate.permitted(policy, {"tool_name": name, "tool_input": {}}))
        self.assertTrue(gate.permitted(policy, {"tool_name": BROWSER_TOOL, "tool_input": {}}))
        self.assertNotIn("--allowedTools", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)

    def test_success_envelope_returns_only_validated_structured_output(self):
        self.assertEqual(self.run_engine("claude"), RESULT)

    def test_success_envelope_payload_still_requires_strict_schema_validation(self):
        for value in ({}, {"status": True}, {"status": "invented"}, {**RESULT, "unexpected": 1}, []):
            with self.subTest(payload=value):
                self.output = json.dumps(
                    {"type": "result", "subtype": "success", "is_error": False, "structured_output": value}
                )
                self.assert_category("invalid_response", lambda: self.run_engine("claude"))

    def test_missing_structured_output_never_salvages_result_prose_or_markdown(self):
        for result in ('{"status":"completed"}', '```json\n{"status":"completed"}\n```', "Completed"):
            with self.subTest(result=result):
                self.output = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result})
                self.assert_category("invalid_response", lambda: self.run_engine("claude"))

    def test_error_envelopes_rejected_even_with_exit_zero_and_valid_structured_output(self):
        for changes in ({"is_error": True}, {"subtype": "error_max_turns"}, {"type": "error"}):
            with self.subTest(envelope=changes):
                self.output = json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "structured_output": RESULT,
                        **changes,
                    }
                )
                self.assert_category("engine_failure", lambda: self.run_engine("claude"))

    def test_error_envelopes_preserve_specific_login_and_quota_categories_without_output_leaks(self):
        for category, message in (("login_required", "Not logged in"), ("quota_exhausted", "Usage limit reached")):
            with self.subTest(category=category):
                self.output = json.dumps(
                    {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "is_error": True,
                        "errors": [message + " synthetic-private-token alex@example.invalid"],
                        "structured_output": RESULT,
                    }
                )
                error = self.assert_category(category, lambda: self.run_engine("claude"))
                self.assertNotIn("synthetic-private-token", str(error))
                self.assertNotIn("alex@example.invalid", str(error))

    def test_invalid_json_and_schema_are_classified_before_using_result(self):
        for name in ("codex", "claude"):
            for value in ("[]", "not JSON", '{"status":true}', '{"status":"completed","unexpected":1}'):
                with self.subTest(engine=name, response=value):
                    self.output = value
                    self.assert_category("invalid_response", lambda: self.run_engine(name))

    def test_old_or_unparseable_version_cannot_dispatch_external_tools(self):
        for version in ("2.1.288", "2.1.294", "unknown"):
            with self.subTest(version=version):
                self.version = version
                self.inference_calls.clear()
                self.assert_category(
                    "unsupported_version", lambda: self.run_engine("claude", task("browser_evaluate", [BROWSER_TOOL]))
                )
                self.assertFalse(self.inference_calls, "Probe must reject unsupported hooks before inference")

    def test_supported_hook_version_is_probed_through_injected_boundary(self):
        self.version = "2.1.295"
        self.assertEqual(self.run_engine("claude", task("browser_evaluate", [BROWSER_TOOL])), RESULT)
        self.assertTrue(any("--version" in call.args[0] for call in self.runner.call_args_list))
        self.assertEqual(len(self.inference_calls), 1)

    def test_external_mail_gate_also_requires_supported_hook_version(self):
        self.version = "2.1.288"
        payload = task("mail_read", ["mcp__gmail__read_email"])
        payload["prompt"] = json.dumps({"account": "alex@example.invalid", "message_id": "synthetic-1"})
        self.assert_category("unsupported_version", lambda: self.run_engine("claude", payload))
        self.assertFalse(self.inference_calls)

    def test_structured_formatting_does_not_open_unrelated_mail_tools(self):
        payload = task("mail_read", ["mcp__gmail__read_email"])
        payload["prompt"] = json.dumps({"account": "alex@example.invalid"})
        self.assertEqual(self.run_engine("claude", payload), RESULT)
        argv, _ = self.inference_calls[-1]
        settings = json.loads(values(argv, "--settings")[0])
        hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
        policy = json.loads(hook["args"][-1])
        permitted = self.module("tool_gate").permitted
        self.assertTrue(permitted(policy, {"tool_name": "StructuredOutput", "tool_input": RESULT}))
        for name in ("StructuredOutputExtra", "mcp__gmail__send_email", "mcp__gmail__read_email_extra", "Bash"):
            with self.subTest(tool=name):
                self.assertFalse(permitted(policy, {"tool_name": name, "tool_input": {}}))

    def test_resolved_executable_and_utf8_preserve_unicode_json_and_metacharacters(self):
        prompt = 'Tên ứng viên & | < > ^ % ! $ ` ; "quoted" \\path\nsecond line'
        for name in ("codex", "claude"):
            with self.subTest(engine=name):
                payload = task()
                payload["prompt"] = prompt
                self.assertEqual(self.run_engine(name, payload), RESULT)
                argv, kwargs = self.inference_calls[-1]
                self.assertEqual(argv[0], self.paths[name])
                self.assertEqual(kwargs.get("encoding", "").replace("-", "").casefold(), "utf8")
                self.assertIs(kwargs["shell"], False)
                self.assertEqual(json.loads(kwargs["input"].split("\n", 1)[1])["prompt"], prompt)
                if name == "claude":
                    self.assertEqual(json.loads(values(argv, "--json-schema")[0]), SCHEMA)

    def npm_shim(self, supported=True, bin_entry="cli.js"):
        shim = self.bin / "claude.cmd"
        shim.write_text("@echo off\r\nrem synthetic npm shim; never execute\r\n", encoding="utf-8")
        self.paths["claude"] = str(shim)
        package = self.bin / "node_modules/@anthropic-ai/claude-code"
        package.mkdir(parents=True)
        (package / "package.json").write_text(
            json.dumps(
                {
                    "name": "@anthropic-ai/claude-code" if supported else "unknown-synthetic-package",
                    "bin": {"claude": bin_entry},
                }
            ),
            encoding="utf-8",
        )
        entry = package / bin_entry
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(b"synthetic entry, never execute")
        return entry

    def test_known_npm_shim_uses_node_and_actual_bin_js_with_exact_argv(self):
        entry = self.npm_shim()
        self.assertEqual(self.run_engine("claude"), RESULT)
        argv, kwargs = self.inference_calls[-1]
        self.assertEqual(argv[:2], [self.paths["node"], str(entry)])
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(json.loads(values(argv, "--json-schema")[0]), SCHEMA)
        for executable in ("cmd", "cmd.exe", "powershell", "powershell.exe"):
            self.assertNotEqual(Path(argv[0]).name.casefold(), executable)

    def test_known_npm_native_bin_runs_actual_executable_without_node_or_shell(self):
        entry = self.npm_shim(bin_entry="bin/claude.exe")
        self.paths.pop("node")
        payload = task("browser_evaluate", [BROWSER_TOOL])
        payload["prompt"] = 'Tiếng Việt & | < > ^ % ! $ ` ; "quoted" \\path\nsecond line'
        self.assertEqual(self.run_engine("claude", payload), RESULT)
        argv, kwargs = self.inference_calls[-1]
        self.assertEqual(argv[0], str(entry))
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs.get("encoding", "").replace("-", "").casefold(), "utf8")
        self.assertEqual(json.loads(values(argv, "--json-schema")[0]), SCHEMA)
        self.assertEqual(json.loads(kwargs["input"].split("\n", 1)[1])["prompt"], payload["prompt"])
        probes = [call for call in self.runner.call_args_list if "--version" in call.args[0]]
        self.assertTrue(probes, "Version guard must probe the resolved native entrypoint")
        for call in self.runner.call_args_list:
            self.assertEqual(call.args[0][0], str(entry), "Use the same entrypoint for probes and inference")

    def test_missing_npm_native_bin_fails_before_any_process_dispatch(self):
        entry = self.npm_shim(bin_entry="bin/claude.exe")
        entry.unlink()
        self.assert_category("configuration", lambda: self.run_engine("claude"))
        self.runner.assert_not_called()

    def test_unknown_npm_shim_is_rejected_before_dispatch(self):
        self.npm_shim(supported=False)
        self.assert_category("configuration", lambda: self.run_engine("claude"))
        self.assertFalse(self.inference_calls)

    def test_npm_shim_missing_node_reports_dependency_without_dispatch(self):
        self.npm_shim()
        self.paths.pop("node")
        error = self.assert_category("configuration", lambda: self.run_engine("claude"))
        self.assertIn("node", str(error).casefold())
        self.assertFalse(self.inference_calls)

    def test_missing_selected_executable_reports_configuration_without_fallback(self):
        self.paths.pop("codex")
        self.assert_category("configuration", self.run_engine)
        self.assertFalse(self.inference_calls)

    def test_failure_categories_are_specific_and_do_not_leak_sensitive_stderr(self):
        sensitive = ("synthetic-private-key", "synthetic-private-token", "alex@example.invalid")
        for name in ("codex", "claude"):
            for category, message in (
                ("login_required", "Not logged in. Please sign in"),
                ("quota_exhausted", "Quota exceeded; usage limit reached"),
                ("configuration", "Error loading config.toml: invalid transport"),
                ("engine_failure", "Unexpected process failure"),
            ):
                with self.subTest(engine=name, category=category):
                    self.failure = (
                        1,
                        message + " API key: " + sensitive[0] + " token: " + sensitive[1] + " " + sensitive[2],
                    )
                    self.inference_calls.clear()
                    error = self.assert_category(category, lambda: self.run_engine(name))
                    for value in sensitive:
                        self.assertNotIn(value, str(error))
                    self.assertEqual(len(self.inference_calls), 1, "No retry or cross-engine fallback")
                    self.assertNotIn(self.paths["claude" if name == "codex" else "codex"], self.inference_calls[0][0])

    def test_timeout_is_bounded_runtime_error_without_sensitive_output(self):
        self.failure = subprocess.TimeoutExpired(["synthetic-engine"], 300, output="synthetic-private-token")
        try:
            error = self.assert_category("timeout", self.run_engine)
        except subprocess.TimeoutExpired:
            self.fail("Engine leaked TimeoutExpired instead of a bounded runtime timeout error")
        self.assertIsInstance(error, RuntimeError)
        self.assertNotIn("synthetic-private-token", str(error))
        self.assertEqual(len(self.inference_calls), 1)
        self.assertLessEqual(self.inference_calls[0][1]["timeout"], 300)

    def test_cli_timeout_returns_runtime_exit_three_without_traceback_using_default_services(self):
        self.write_json("config.json", {"runtime": {"tailor_engine": "codex"}})
        self.write_json("profile.json", {})
        self.failure = subprocess.TimeoutExpired(["synthetic-engine"], 300)

        # Keep CLI/default runtime dispatch real; isolate only document generation.
        def run_tailor(instance, jd, profile, engine, config):
            return instance.engines[engine].run(task("tailor"))

        output, errors = io.StringIO(), io.StringIO()
        with patch("autoapply_local.tailor.Tailor.run", autospec=True, side_effect=run_tailor):
            try:
                code = self.module("cli").main(
                    ["--user-dir", str(self.root), "tailor", "synthetic-jd.txt"], stdout=output, stderr=errors
                )
            except subprocess.TimeoutExpired:
                self.fail("Default CLI dispatch leaked TimeoutExpired instead of returning runtime exit 3")
        self.assertEqual(code, 3)
        self.assertIn("timeout", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())
        self.assertEqual(len(self.inference_calls), 1)

    def test_engine_override_precedes_purpose_default_without_cross_engine_fallback(self):
        runtime = self.module("runtime")
        config = {"runtime": {"apply_engine": "codex", "tailor_engine": "claude"}}
        for purpose, default in (("apply", "codex"), ("tailor", "claude")):
            with self.subTest(purpose=purpose):
                self.assertEqual(runtime.selected_engine(config, purpose=purpose), default)
                other = "claude" if default == "codex" else "codex"
                selected = runtime.selected_engine(config, other, purpose)
                self.assertEqual(selected, other)
                self.inference_calls.clear()
                self.runner.reset_mock()
                self.assertEqual(self.run_engine(selected), RESULT)
                self.assertEqual(len(self.inference_calls), 1)
                self.assertNotIn(self.paths[default], self.inference_calls[0][0])
                for call in self.runner.call_args_list:
                    self.assertNotIn(self.paths[default], call.args[0], "Even probes must use only the selected engine")

    def test_doctor_reports_presence_and_missing_dependencies_without_claiming_login_from_version(self):
        self.write_json("config.json", {"runtime": {"apply_engine": "claude"}, "resources": {}})
        self.paths.pop("node")
        with patch(
            "autoapply_local.browser.discover_chrome", side_effect=FileNotFoundError("Synthetic missing Chrome")
        ):
            report = self.module("runtime").doctor(root=self.root)
        self.assertEqual(report["engine"], "claude")
        self.assertEqual(report["executable"], self.paths["claude"])
        self.assertIn(report["login"], ("not_verified", "login_required"))
        self.assertEqual(report["native_connectors"], "not_verified")
        self.assertIsNone(report["node"])
        self.assertIsNone(report["chrome"])
        self.assertFalse(self.inference_calls, "Doctor must not make inference calls")
