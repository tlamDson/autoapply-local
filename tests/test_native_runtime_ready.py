"""Offline synthetic native-runtime contracts; no model, browser, network or Gmail.

Giả định của người thực thi: record assumptions/RED in test docstrings and the
final report because only test files may change. Runtime roots are temporary;
two lanes and three jobs exercise both runner creation and runner reuse.
"""

import copy
import hashlib
import io
import json
import re
import subprocess
import tomllib
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, call, patch

import test_native_readback as readback
from test_support import RELEASE, ReleaseCase, candidate, job, snapshot


TOOLS = [readback.PREFIX + name for name in ("list_pages", "take_snapshot")]
SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
    "additionalProperties": False,
}
RESULT = {"status": "completed"}


def task(kind="browser_evaluate", tools=None):
    return {
        "kind": kind,
        "allowed_tools": list(TOOLS if tools is None else tools),
        "schema": copy.deepcopy(SCHEMA),
        "prompt": "Synthetic runtime request",
    }


def overrides(argv):
    return {
        key: tomllib.loads("value=" + raw)["value"]
        for item in readback.flag_values(argv, "-c")
        for key, raw in [item.split("=", 1)]
    }


class NativeRuntimeReadyTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.runtime = self.module("runtime")
        self.browser, self.engines = self.module("browser"), self.module("engines")
        for target, attribute in ((subprocess, "run"), (subprocess, "Popen"), (self.browser, "urlopen")):
            guard = patch.object(target, attribute, side_effect=AssertionError("Offline synthetic test boundary"))
            guard.start()
            self.addCleanup(guard.stop)
        self.sources = patch.object(self.engines, "configuration_sources", return_value=([], False)).start()
        self.addCleanup(patch.stopall)
        patch.object(self.engines, "resolve_executable", return_value=["synthetic-engine"]).start()

    def invoke(self, arguments):
        names = ("apply", "review", "submit", "fetch", "doctor", "tailor", "mail_check", "status")
        services = {name: Mock(return_value={}) for name in names}
        output, error = io.StringIO(), io.StringIO()
        code = self.module("cli").main(
            ["--user-dir", str(self.root), *arguments], services=services, stdout=output, stderr=error
        )
        return code, services, error.getvalue()

    def test_cli_opt_in_dispatches_true_only_to_apply_review_submit(self):
        cases = [
            (["apply", "codex", "--quick", "2"], "apply", {"engine": "codex", "selection": 2}),
            (["review", "all"], "review", {"selection": "all"}),
            (["submit", "all-reviewed"], "submit", {"selection": "all-reviewed"}),
        ]
        for arguments, name, kwargs in cases:
            with self.subTest(command=name):
                code, services, error = self.invoke([*arguments, "--approve-browser-tools"])
                self.assertEqual(code, 0, error)
                services[name].assert_called_once_with(root=self.root, **kwargs, approve_browser_tools=True)
                for other in services.keys() - {name}:
                    services[other].assert_not_called()

    def test_cli_default_kwargs_are_unchanged(self):
        cases = [
            (
                ["apply", "codex", "--quick", "first,second"],
                "apply",
                {"engine": "codex", "selection": ["first", "second"]},
            ),
            (["review", "all"], "review", {"selection": "all"}),
            (["submit", "first"], "submit", {"selection": ["first"]}),
        ]
        for arguments, name, kwargs in cases:
            with self.subTest(command=name):
                code, services, error = self.invoke(arguments)
                self.assertEqual(code, 0, error)
                services[name].assert_called_once_with(root=self.root, **kwargs)

    def test_cli_rejects_flag_for_other_commands_without_dispatch(self):
        for arguments in (["fetch", "codex"], ["doctor"], ["tailor", "synthetic-jd.txt"], ["mail-check"], ["status"]):
            with self.subTest(command=arguments[0]):
                code, services, error = self.invoke([*arguments, "--approve-browser-tools"])
                self.assertEqual(code, 2, error)
                self.assertIn("--approve-browser-tools", error)
                for service in services.values():
                    service.assert_not_called()

    def runtime_fixture(self, operation):
        pdf = self.root / "synthetic.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% synthetic runtime\n%%EOF")
        self.write_json("profile.json", candidate())
        self.write_json(
            "config.json",
            {
                "runtime": {"apply_engine": "codex"},
                "resources": {"lanes": 2, "multiple_lanes_opt_in": True, "held_tab_cap": 5},
                "resumes": {"main_pdf": str(pdf)},
            },
        )
        lanes = [
            {
                "lane": index,
                "debug_url": f"http://127.0.0.1:{9330 + index}",
                "profile_dir": str(self.root / "browser" / f"lane-{index}"),
            }
            for index in (1, 2)
        ]
        self.write_json("browser/active-lanes.json", lanes)
        state = {"apply": "pending", "review": "filled", "submit": "reviewed"}[operation]
        records = {
            name: {
                **job(name),
                "state": state,
                "lane": owner["lane"],
                "debug_url": owner["debug_url"],
                **({"tab_id": "tab-" + name} if operation != "apply" else {}),
            }
            for name, owner in zip(("first", "second", "third"), (lanes[0], lanes[1], lanes[0]))
        }
        base = Mock(spec=["_selection", "store", "_profile", "status"])
        base._selection.return_value = list(records)
        base.store.get.side_effect = records.__getitem__
        base.store.jobs.return_value = list(records.values())
        base._profile.return_value = candidate()
        base.status.return_value = {"held_tabs": []}
        runners = [Mock(spec=["apply", "review", "submit"]) for _ in lanes]
        for runner in runners:
            runner.apply.return_value = {"jobs": []}
            runner.review.return_value = {"reviews": []}
            runner.submit.return_value = {"jobs": []}
        return pdf, lanes, records, base, runners

    def assert_no_persisted_approval(self):
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"approve", path.read_bytes().lower(), str(path.relative_to(self.root)))

    def runtime_lane_case(self, operation, opt_in):
        pdf, lanes, records, base, runners = self.runtime_fixture(operation)
        kwargs = {} if opt_in is None else {"approve_browser_tools": opt_in}
        with (
            patch("autoapply_local.workflow.Workflow", return_value=base),
            patch.object(
                self.runtime, "_workflow", side_effect=[(runner, {}, "codex") for runner in runners]
            ) as workflow,
            patch.object(self.browser, "launch_chrome", return_value=lanes) as launch,
            patch.object(self.browser, "available_memory_mb", return_value=16384),
            redirect_stderr(io.StringIO()),
        ):
            getattr(self.runtime, operation)(root=self.root, selection=list(records), **kwargs)
        expected = []
        for lane in lanes:
            options = {"lane": lane, **({"approve_browser_tools": True} if opt_in else {})}
            expected.append(call(self.root, None, **options) if operation == "apply" else call(self.root, **options))
        self.assertEqual(workflow.call_args_list, expected)
        for index, runner in enumerate(runners):
            names = ("first", "third") if index == 0 else ("second",)
            calls = [call([name], pdf=pdf, engine="codex") if operation == "apply" else call([name]) for name in names]
            self.assertEqual(getattr(runner, operation).call_args_list, calls)
            for other in {"apply", "review", "submit"} - {operation}:
                getattr(runner, other).assert_not_called()
        self.assert_no_persisted_approval()
        if operation == "apply":
            launch.assert_called_once()
        else:
            launch.assert_not_called()

    def test_runtime_apply_propagates_opt_in_to_both_lanes_and_reused_runner(self):
        self.runtime_lane_case("apply", True)

    def test_runtime_review_propagates_opt_in_to_both_lanes(self):
        self.runtime_lane_case("review", True)

    def test_runtime_submit_propagates_opt_in_to_both_lanes(self):
        self.runtime_lane_case("submit", True)

    def test_runtime_default_and_false_preserve_old_workflow_calls(self):
        for operation in ("apply", "review", "submit"):
            for opt_in in (None, False):
                with self.subTest(operation=operation, flag=opt_in):
                    self.runtime_lane_case(operation, opt_in)

    def test_runtime_rejects_non_bool_before_any_side_effect(self):
        for operation in ("apply", "review", "submit", "_workflow"):
            for invalid in ("true", 1, None):
                with self.subTest(operation=operation, value=invalid):
                    with (
                        patch.object(self.runtime, "configuration") as config,
                        patch.object(self.runtime, "Store") as store,
                        patch("autoapply_local.workflow.Workflow") as workflow,
                        patch.object(self.browser, "launch_chrome") as launch,
                        patch.object(self.engines, "Engine") as engine,
                        patch.object(self.browser, "BrowserBridge") as bridge,
                    ):
                        options = {"approve_browser_tools": invalid}
                        if operation != "_workflow":
                            options["selection"] = ["synthetic-1"]
                        with self.assertRaises(ValueError):
                            getattr(self.runtime, operation)(root=self.root, **options)
                        for boundary in (config, store, workflow, launch, engine, bridge):
                            boundary.assert_not_called()
                    self.assertEqual(list(self.root.iterdir()), [])

    def test_workflow_forwards_true_and_omits_default_engine_kwarg(self):
        self.write_json("config.json", {"runtime": {"apply_engine": "codex"}})
        owner = {"lane": 2, "debug_url": readback.ENDPOINT}
        for options in ({}, {"approve_browser_tools": False}, {"approve_browser_tools": True}):
            with self.subTest(options=options):
                with (
                    patch.object(self.engines, "Engine") as constructor,
                    patch.object(self.browser, "BrowserBridge") as bridge,
                    patch.object(self.browser, "launch_chrome") as launch,
                ):
                    workflow, _, selected = self.runtime._workflow(self.root, lane=owner, **options)
                expected = {"cwd": self.root, "debug_url": readback.ENDPOINT}
                if options.get("approve_browser_tools"):
                    expected["approve_browser_tools"] = True
                constructor.assert_called_once_with("codex", **expected)
                bridge.assert_called_once_with(
                    root=self.root, engine=constructor.return_value, debug_url=readback.ENDPOINT
                )
                launch.assert_not_called()
                self.assertEqual(selected, "codex")
                self.assertIs(workflow.engines["codex"], constructor.return_value)
                self.assert_no_persisted_approval()

    def test_opt_in_real_workflow_keeps_review_gate_and_never_persists_flag(self):
        pdf, lanes, _, _, _ = self.runtime_fixture("apply")
        store = self.module("storage").Store(self.root)
        store.enqueue(job())
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        live = snapshot(resume_sha256=digest)
        bridge = Mock(spec=["restore", "open", "fill", "upload_file", "snapshot", "submit"])
        bridge.open.return_value = "tab-1"
        bridge.fill.return_value = {"status": "filled"}
        bridge.snapshot.side_effect = lambda *args, **kwargs: copy.deepcopy(live)
        with (
            patch.object(self.engines, "Engine", return_value=self.fake_engine()),
            patch.object(self.browser, "BrowserBridge", return_value=bridge),
            patch.object(self.browser, "launch_chrome", return_value=lanes),
            patch.object(self.browser, "available_memory_mb", return_value=16384),
            redirect_stderr(io.StringIO()),
        ):
            self.runtime.apply(root=self.root, selection=[job()["id"]], approve_browser_tools=True)
            self.assertEqual(store.get(job()["id"])["state"], "filled")
            bridge.submit.assert_not_called()
            with self.assertRaisesRegex(ValueError, "previously reviewed"):
                self.runtime.submit(root=self.root, selection=[job()["id"]], approve_browser_tools=True)
            bridge.submit.assert_not_called()
            self.runtime.review(root=self.root, selection=[job()["id"]], approve_browser_tools=True)
            self.assertEqual(store.get(job()["id"])["state"], "reviewed")
            bridge.submit.assert_not_called()
            # Editing a reviewed observation must still invalidate explicit Submit.
            live["fields"][0]["extension"] = "changed after review"
            with self.assertRaisesRegex(ValueError, "changed after review"):
                self.runtime.submit(root=self.root, selection=[job()["id"]], approve_browser_tools=True)
            bridge.submit.assert_not_called()
        self.assert_no_persisted_approval()

    def engine_case(self, name="codex", payload=None, options=None, inventory=None):
        raw = json.dumps(readback.envelope(RESULT) if name == "claude" else RESULT)
        runner = readback.SyntheticRunner(raw, inventory=inventory)
        engine = self.engines.Engine(name, runner=runner, cwd=self.root, **(options or {}))
        result = engine.run(task() if payload is None else payload)
        self.assertEqual(result, RESULT)
        self.assertEqual(len(runner.dispatches), 1)
        return runner.dispatches[0]

    def assert_no_grants(self, dispatch):
        self.assertNotIn("approval_mode", json.dumps(dispatch))
        for settings in readback.flag_values(dispatch["argv"], "--settings"):
            self.assertNotIn("permissions", json.loads(settings))

    def assert_no_bypass_or_model(self, argv):
        for forbidden in (
            "--yolo",
            "--full-auto",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-skip-permissions",
            "--permission-mode",
            "--allowedTools",
            "--model",
            "-m",
        ):
            self.assertNotIn(forbidden, argv)

    def normalized_argv(self, argv):
        normalized = list(argv)
        for flag in ("--output-schema", "--output-last-message"):
            if flag in normalized:
                normalized[normalized.index(flag) + 1] = "<temporary-file>"
        return normalized

    def test_engine_codex_grants_only_requested_browser_tools_in_invocation(self):
        for kind in ("browser_evaluate", "submit"):
            with self.subTest(kind=kind):
                default = self.engine_case(payload=task(kind), options={"debug_url": readback.ENDPOINT})
                granted = self.engine_case(
                    payload=task(kind), options={"debug_url": readback.ENDPOINT, "approve_browser_tools": True}
                )
                config = overrides(granted["argv"])
                server = config["mcp_servers"][self.engines.BROWSER_SERVER]
                self.assertEqual(
                    server["tools"],
                    {name.removeprefix(readback.PREFIX): {"approval_mode": "approve"} for name in TOOLS},
                )
                self.assertEqual(server["enabled_tools"], [name.removeprefix(readback.PREFIX) for name in TOOLS])
                self.assertEqual(
                    set(server), set(overrides(default["argv"])["mcp_servers"][self.engines.BROWSER_SERVER]) | {"tools"}
                )
                without_grant = copy.deepcopy(config)
                del without_grant["mcp_servers"][self.engines.BROWSER_SERVER]["tools"]
                self.assertEqual(without_grant, overrides(default["argv"]))
                self.assertEqual(
                    [item.split("=", 1)[0] for item in readback.flag_values(granted["argv"], "-c")],
                    [item.split("=", 1)[0] for item in readback.flag_values(default["argv"], "-c")],
                )
                self.assertEqual(readback.flag_values(granted["argv"], "--sandbox"), ["read-only"])
                self.assertEqual(granted["prompt"], default["prompt"])
                self.assert_no_bypass_or_model(granted["argv"])
                self.assert_no_persisted_approval()

    def test_engine_false_preserves_default_argv_for_both_engines(self):
        for name in ("codex", "claude"):
            with self.subTest(engine=name):
                default = self.engine_case(name, options={"debug_url": readback.ENDPOINT})
                disabled = self.engine_case(
                    name, options={"debug_url": readback.ENDPOINT, "approve_browser_tools": False}
                )
                self.assertEqual(self.normalized_argv(default["argv"]), self.normalized_argv(disabled["argv"]))
                self.assertEqual(default["prompt"], disabled["prompt"])
                self.assert_no_grants(disabled)

    def test_engine_other_provider_permissions_keep_hooks_and_denials(self):
        default = self.engine_case("claude", options={"debug_url": readback.ENDPOINT})
        granted = self.engine_case("claude", options={"debug_url": readback.ENDPOINT, "approve_browser_tools": True})
        settings = json.loads(readback.flag_values(granted["argv"], "--settings")[0])
        baseline = json.loads(readback.flag_values(default["argv"], "--settings")[0])
        self.assertEqual(settings, {**baseline, "permissions": {"allow": TOOLS}})
        self.assertIn("PreToolUse", settings["hooks"])
        self.assertIn("--strict-mcp-config", granted["argv"])
        self.assertEqual(
            readback.flag_values(granted["argv"], "--disallowedTools"),
            readback.flag_values(default["argv"], "--disallowedTools"),
        )
        expected_denied = [
            readback.PREFIX + name for name in sorted(self.engines.BROWSER_TOOLS) if readback.PREFIX + name not in TOOLS
        ]
        self.assertEqual(readback.flag_values(granted["argv"], "--disallowedTools"), [",".join(expected_denied)])
        normalized = list(granted["argv"])
        normalized[normalized.index("--settings") + 1] = json.dumps(baseline)
        self.assertEqual(normalized, default["argv"])
        self.assert_no_bypass_or_model(granted["argv"])

    def test_engine_opt_in_does_not_grant_mail_analysis_tailor_empty_or_arbitrary_kind(self):
        for name in ("codex", "claude"):
            for payload, endpoint in (
                (task(), None),
                (task("mail_discovery", ["mcp__gmail__get_profile"]), readback.ENDPOINT),
                (task("browser_evaluate", []), readback.ENDPOINT),
                (task("arbitrary_kind"), readback.ENDPOINT),
            ):
                with self.subTest(engine=name, kind=payload["kind"], endpoint=endpoint):
                    dispatch = self.engine_case(name, payload, {"debug_url": endpoint, "approve_browser_tools": True})
                    self.assert_no_grants(dispatch)
            runner = readback.SyntheticRunner(json.dumps(readback.envelope(RESULT) if name == "claude" else RESULT))
            engine = self.engines.Engine(
                name, runner=runner, cwd=self.root, debug_url=readback.ENDPOINT, approve_browser_tools=True
            )
            for kind in ("apply_analysis", "tailor"):
                with self.subTest(engine=name, kind=kind):
                    before = len(runner.dispatches)
                    self.assertEqual(engine.run(task(kind, [])), RESULT)
                    self.assertEqual(len(runner.dispatches), before + 1)
                    self.assert_no_grants(runner.dispatches[-1])

    def test_engine_invalid_tools_are_denied_before_grant_or_inference(self):
        for name in ("codex", "claude"):
            for tool in ("mcp__other__take_snapshot", readback.PREFIX + "unknown_tool"):
                with self.subTest(engine=name, tool=tool):
                    runner = readback.SyntheticRunner("{}")
                    engine = self.engines.Engine(
                        name, runner=runner, cwd=self.root, debug_url=readback.ENDPOINT, approve_browser_tools=True
                    )
                    with self.assertRaises(PermissionError):
                        engine.run(task(tools=[tool]))
                    self.assertEqual(runner.dispatches, [])

    def test_engine_invalid_debug_url_cannot_receive_grant(self):
        for name in ("codex", "claude"):
            for endpoint in ("https://127.0.0.1:9333", "http://example.invalid:9333", "http://127.0.0.1:9333/?x=1"):
                with self.subTest(engine=name, endpoint=endpoint):
                    runner = readback.SyntheticRunner("{}")
                    engine = self.engines.Engine(
                        name, runner=runner, cwd=self.root, debug_url=endpoint, approve_browser_tools=True
                    )
                    with self.assertRaises(ValueError):
                        engine.run(task())
                    self.assertEqual(runner.dispatches, [])

    def test_engine_managed_policy_and_duplicate_server_still_fail_configuration(self):
        for managed, inventory in (
            (True, []),
            (False, [{"name": self.engines.BROWSER_SERVER, "enabled": True}]),
            (False, [{"name": self.engines.BROWSER_SERVER, "enabled": False}]),
        ):
            with self.subTest(managed=managed, inventory=inventory):
                self.sources.return_value = ([], managed)
                runner = readback.SyntheticRunner("{}", inventory=inventory)
                engine = self.engines.Engine(
                    "codex", runner=runner, cwd=self.root, debug_url=readback.ENDPOINT, approve_browser_tools=True
                )
                with self.assertRaises(self.engines.EngineError) as caught:
                    engine.run(task())
                self.assertEqual(caught.exception.category, "configuration")
                self.assertEqual(runner.dispatches, [])

    def test_engine_opt_in_preserves_permission_denial(self):
        denied = {**readback.envelope(RESULT), "permission_denials": [{"tool_name": TOOLS[0]}]}
        runner = readback.SyntheticRunner(json.dumps(denied))
        engine = self.engines.Engine(
            "claude", runner=runner, cwd=self.root, debug_url=readback.ENDPOINT, approve_browser_tools=True
        )
        with self.assertRaises(self.engines.EngineError) as caught:
            engine.run(task())
        self.assertEqual(caught.exception.category, "permission_required")
        self.assertEqual(len(runner.dispatches), 1)

    def test_engine_requires_strict_bool(self):
        for name in ("codex", "claude"):
            for invalid in ("yes", 1, None):
                with self.subTest(engine=name, value=invalid):
                    runner = Mock()
                    with self.assertRaises(ValueError):
                        self.engines.Engine(name, runner=runner, approve_browser_tools=invalid)
                    runner.assert_not_called()
        self.sources.assert_not_called()

    def test_manual_dispatch_trigger_preserves_push_pr_and_eight_steps(self):
        text = (RELEASE / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        lines = text.splitlines()
        index = next(index for index, line in enumerate(lines) if line.startswith("on:"))
        head = lines[index][3:].strip()
        if head:
            self.assertRegex(head, r"^\[[a-z_, ]+\]$")
            triggers = [item.strip() for item in head[1:-1].split(",")]
        else:
            block = []
            for line in lines[index + 1 :]:
                if line and not line[0].isspace():
                    break
                if line.strip():
                    block.append(line)
            for line in block:
                self.assertRegex(line, r"^  [a-z_]+:\s*$", "Triggers must have no filters or inputs")
            triggers = [line.strip().removesuffix(":") for line in block]
        steps = re.findall(r"^      - (.+)$", text, re.MULTILINE)
        self.assertEqual(
            steps,
            [
                "uses: actions/checkout@v4",
                "uses: actions/setup-python@v5",
                "uses: actions/setup-node@v4",
                "run: python -m pip install setuptools==84.1.0 wheel==0.48.0 build==1.3.0 -r requirements.txt",
                "name: Export a clean source tree into a new directory",
                "name: Test and audit the exported source",
                "name: Build and audit current wheel, sdist and source ZIP",
                "name: Install final artifacts in isolated Unicode directories",
            ],
        )
        self.assertEqual(set(triggers), {"push", "pull_request", "workflow_dispatch"})
        self.assertEqual(len(triggers), 3)

    def test_readmes_preserve_experimental_8gb_and_live_ats_limits_and_document_flag(self):
        cases = {
            "README.md": [
                "experimental",
                "an 8 GB machine have\n**not** been validated",
                "Live ATS,",
                "needs a live ATS gate before it can be considered stable",
            ],
            "README.vi.md": [
                "bản thử nghiệm",
                "máy 8 GB",
                "Chưa xác\nminh ATS thật",
                "cần qua cổng ATS live sau và chưa được coi là ổn định",
            ],
        }
        for name, claims in cases.items():
            with self.subTest(readme=name):
                text = (RELEASE / name).read_text(encoding="utf-8")
                for claim in claims:
                    self.assertIn(claim, text)
                self.assertIn("--approve-browser-tools", text)
