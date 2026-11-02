"""Real CLI/core integration and selected-runtime safety, without live engines.

Giả định của người thực thi: engines.Engine(name, runner=subprocess.run).run(task)
accepts prompt/schema/allowed_tools and parses a strictly schema-bound JSON object.
The subprocess boundary is injectable; a CLI's default services use the same core
modules tested elsewhere, with Engine/BrowserBridge/Transport as mocked boundaries.
No callback service mocks are supplied to CLI.main in this file. API details absent
from the plan are recorded here because this turn permits edits to tests only.
"""

import copy
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

from test_support import RELEASE, ReleaseCase, candidate, job, snapshot


def analysis_task():
    return {
        "kind": "apply_analysis",
        "prompt": "Analyze this synthetic job; no browser actions.",
        "allowed_tools": [],
        "schema": {
            "type": "object",
            "required": ["status"],
            "additionalProperties": False,
            "properties": {"status": {"type": "string", "enum": ["completed"]}},
        },
    }


class EngineRuntimeTests(ReleaseCase):
    def engine(self, name="codex", stdout='{"status":"completed"}'):
        if name == "claude":
            stdout = json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "structured_output": json.loads(stdout)}
            )
        self.runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=stdout, stderr=""))

        def process(argv, **kwargs):
            if "mcp" in argv and "list" in argv and "--json" in argv:
                # This fixture has no inherited connectors. The explicit native
                # probe must not reuse a model response as its MCP inventory.
                return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")
            return self.runner.return_value

        self.runner.side_effect = process
        engines = self.module("engines")
        resolver = patch.object(engines, "resolve_executable", return_value=[name])
        resolver.start()
        self.addCleanup(resolver.stop)
        return engines.Engine(name, runner=self.runner)

    def model_dispatch(self):
        dispatches = [
            call for call in self.runner.call_args_list if "exec" in call.args[0] or "--print" in call.args[0]
        ]
        self.assertEqual(len(dispatches), 1)
        return dispatches[0]

    def test_selected_engine_is_only_subprocess_and_never_bypasses_approvals_or_pins_model(self):
        for selected in ("codex", "claude"):
            with self.subTest(engine=selected):
                engine = self.engine(selected)
                self.assertEqual(engine.run(analysis_task()), {"status": "completed"})
                args, kwargs = self.model_dispatch()
                argv = args[0]
                self.assertIsInstance(argv, list)
                self.assertEqual(Path(argv[0]).stem, selected)
                self.assertIs(kwargs["shell"], False)
                self.assertGreater(kwargs["timeout"], 0)
                self.assertLessEqual(kwargs["timeout"], 300)
                serialized = " ".join(map(str, argv))
                for forbidden in (
                    "bypassPermissions",
                    "dangerously-skip-permissions",
                    "dangerously-bypass-approvals",
                    "--yolo",
                    "--model",
                ):
                    self.assertNotIn(forbidden, serialized)
                self.assertNotIn("-m", argv)

    def test_invalid_json_and_schema_mismatch_fail_closed(self):
        for stdout in ("not json", "[]", '{"status":"clicked"}', '{"status":"completed","submit":true}'):
            with self.subTest(stdout=stdout):
                engine = self.engine(stdout=stdout)
                with self.assertRaises(ValueError):
                    engine.run(analysis_task())
                self.model_dispatch()

    def test_failed_selected_executable_does_not_fall_back_to_other_engine(self):
        engine = self.engine()
        self.runner.side_effect = FileNotFoundError("Synthetic selected engine unavailable")
        with self.assertRaisesRegex(RuntimeError, "configuration"):
            engine.run(analysis_task())
        self.runner.assert_called_once()

    def test_nonzero_process_exit_cannot_be_accepted_as_completed_json(self):
        engine = self.engine()
        self.runner.return_value.returncode = 2
        self.runner.return_value.stderr = "Synthetic login required"
        with self.assertRaisesRegex(RuntimeError, "login|exit|failed|engine"):
            engine.run(analysis_task())
        self.model_dispatch()

    def test_analysis_task_cannot_request_browser_or_submit_tool_capability(self):
        for tool in ("mcp__chrome-devtools__new_page", "mcp__chrome-devtools__click", "click_submit"):
            with self.subTest(tool=tool):
                engine = self.engine()
                task = {**analysis_task(), "allowed_tools": [tool]}
                with self.assertRaises(ValueError):
                    engine.run(task)
                self.runner.assert_not_called()

    def test_tailor_task_cannot_request_browser_tools(self):
        engine = self.engine()
        task = {**analysis_task(), "kind": "tailor", "allowed_tools": ["mcp__chrome-devtools__new_page"]}
        with self.assertRaises(ValueError):
            engine.run(task)
        self.runner.assert_not_called()

    def test_apply_and_review_cannot_request_submit_capability(self):
        for kind in ("apply", "review"):
            with self.subTest(kind=kind):
                engine = self.engine()
                task = {**analysis_task(), "kind": kind, "allowed_tools": ["click_submit"]}
                with self.assertRaises(ValueError):
                    engine.run(task)
                self.runner.assert_not_called()


class DefaultCliIntegrationTests(ReleaseCase):
    def invoke(self, *argv):
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        return self.module("cli").main(
            ["--user-dir", str(self.root), *argv],
            stdout=self.stdout,
            stderr=self.stderr,
        )

    def test_real_module_setup_works_outside_source_without_service_callbacks(self):
        env = {**os.environ, "PYTHONPATH": str(RELEASE), "AUTOAPPLY_LOCAL_HOME": str(self.root)}
        # Setup is local and must not require an installed engine to run.
        env["PATH"] = ""
        result = subprocess.run(
            [sys.executable, "-m", "autoapply_local", "--user-dir", str(self.root), "setup"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in (
            "user/profile-intake.md",
            "templates/profile-intake.md",
            "profile.json",
            "config.json",
            "tracker.xlsx",
        ):
            with self.subTest(name=name):
                self.assertTrue((self.root / name).is_file(), name)
        profile = json.loads((self.root / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["contact"], {})

    def test_default_profile_update_previews_then_confirms_real_intake(self):
        cli = self.module("cli")
        self.module("intake").setup(self.root)
        path = self.root / "user/profile-intake.md"
        document = self.module("intake").read_intake(path)
        document["groups"]["contact"].update(legal_name="Alex Example", application_email="alex@example.invalid")
        path.write_text("```json\n" + json.dumps(document) + "\n```\n", encoding="utf-8")
        original = (self.root / "profile.json").read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("Local profile update must not launch an engine")):
            self.assertEqual(self.invoke("profile", "update", str(path)), 0, self.stderr.getvalue())
            self.assertEqual((self.root / "profile.json").read_bytes(), original)
            self.assertEqual(self.invoke("profile", "update", str(path), "--confirm"), 0, self.stderr.getvalue())
        profile = json.loads((self.root / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["contact"]["legal_name"]["value"], "Alex Example")
        self.assertEqual(profile["contact"]["legal_name"]["status"], "confirmed")

    def test_real_apply_and_review_use_mocked_boundaries_and_keep_one_tab(self):
        self.module("cli")  # Missing product module is a genuine RED import.
        engines, browser = self.module("engines"), self.module("browser")
        self.module("intake").setup(self.root)
        self.write_json("profile.json", candidate("authorized"))
        pdf = self.root / "synthetic.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% synthetic resume\n%%EOF")
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config["runtime"].update(apply_engine="codex", tailor_engine="codex")
        config["resumes"] = {"main_pdf": str(pdf), "alternatives": [], "role_mapping": {}}
        self.write_json("config.json", config)
        store = self.module("storage").Store(self.root)
        store.enqueue(job())
        import hashlib

        live = snapshot(resume_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest())
        live["fields"][0]["value"] = "Morgan Example"
        bridge = Mock(spec=["open", "fill", "upload_file", "snapshot", "submit", "close"])
        bridge.open.return_value = "tab-1"
        bridge.fill.return_value = {"status": "filled"}
        bridge.snapshot.side_effect = lambda *args, **kwargs: copy.deepcopy(live)
        with (
            patch.object(engines, "Engine", return_value=self.fake_engine()),
            patch.object(browser, "BrowserBridge", return_value=bridge),
            patch.object(browser, "available_memory_mb", return_value=16384),
            patch.object(
                browser,
                "launch_chrome",
                return_value=[
                    {
                        "lane": 1,
                        "profile_dir": str(self.root / "browser/lane-1"),
                        "debug_url": "http://127.0.0.1:9222",
                        "pid": 1234,
                    }
                ],
            ),
            patch("subprocess.Popen", side_effect=AssertionError("Only mocked runtime/browser allowed")),
        ):
            self.assertEqual(self.invoke("apply", "codex", "--quick", "synthetic-1"), 0, self.stderr.getvalue())
            self.assertEqual(store.get("synthetic-1")["state"], "filled")
            self.assertEqual(self.invoke("review", "synthetic-1"), 0, self.stderr.getvalue())
            self.assertEqual(store.get("synthetic-1")["state"], "reviewed")
            self.assertEqual(self.invoke("status"), 0, self.stderr.getvalue())
            self.assertIn("synthetic-1", self.stdout.getvalue())
        bridge.open.assert_called_once()
        bridge.fill.assert_called_once()
        bridge.upload_file.assert_called_once()
        bridge.submit.assert_not_called()
        bridge.close.assert_not_called()
        self.assertEqual(store.tracker_rows(), [])

    def test_real_fetch_command_uses_mocked_transport_and_enqueues_filtered_jobs(self):
        self.module("cli")
        fetch = self.module("fetch")
        self.module("intake").setup(self.root)
        self.write_json("profile.json", candidate())
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config["runtime"]["apply_engine"] = "codex"
        config["boards"] = [{"ats": "greenhouse", "board": "synthetic", "company": "Example Labs"}]
        self.write_json("config.json", config)
        transport = Mock(spec=["get_json", "post_json"])
        transport.get_json.return_value = {
            "jobs": [
                {
                    "id": 123,
                    "title": "Backend Intern Summer 2030",
                    "location": {"name": "US"},
                    "absolute_url": "https://jobs.example.invalid/123",
                    "content": "Build Python services.",
                }
            ]
        }
        with (
            patch.object(fetch, "Transport", return_value=transport),
            patch("subprocess.Popen", side_effect=AssertionError("Public API fetch must not launch engines")),
        ):
            self.assertEqual(self.invoke("fetch", "codex"), 0, self.stderr.getvalue())
        transport.get_json.assert_called_once()
        transport.post_json.assert_not_called()
        jobs = self.module("storage").Store(self.root).jobs()
        self.assertEqual([item["id"] for item in jobs], ["greenhouse:synthetic:123"])
        self.assertEqual(jobs[0]["eligibility"], "unknown")
