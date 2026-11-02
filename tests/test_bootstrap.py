"""Offline regressions for autoapply-local-bootstrap; tests only in this turn.

Giả định của người thực thi: the generated launcher is autoapply-local.py;
wrappers are identical packaged/source instructions and use that relative file.
Unspecified asset directories and preflight formatting remain implementation choices.
The plan is unchanged because the user's final instruction permits test files only.
All writes are synthetic fixtures in temporary roots; no real engines are run.
"""

import importlib.util
import io
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import zipfile
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from test_support import RELEASE, ReleaseCase, candidate, job, snapshot


LAUNCHER = "autoapply-local.py"
COMMANDS = (
    "setup",
    "doctor",
    "profile-update",
    "fetch",
    "apply",
    "tailor",
    "review",
    "submit",
    "mail-check",
    "status",
)
WRAPPERS = tuple(f".claude/commands/{name}.md" for name in COMMANDS) + (".codex/skills/autoapply-local/SKILL.md",)
SUPPORT = ("AGENTS.md", "README.md", "examples/boards.json")


class BootstrapTests(ReleaseCase):
    def checker(self):
        spec = importlib.util.spec_from_file_location("bootstrap_release_checker", RELEASE / "scripts/check_release.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_python(self, *arguments, env=None, cwd=None):
        return subprocess.run(
            [sys.executable, "-B", *map(str, arguments)],
            cwd=cwd or self.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    def test_clean_real_export_retains_ci_tests_and_passes_its_own_audit(self):
        destination = self.root / "clean public export"
        output = io.StringIO()
        with redirect_stdout(output):
            code = self.checker().main(["--export", str(destination)])
        self.assertEqual(code, 0, output.getvalue())
        allowed = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
        tests = [name for name in allowed if name.startswith("tests/")]
        self.assertIn("tests/test_bootstrap.py", tests)
        self.assertIn("tests/adapters.test.cjs", tests)
        for relative in tests:
            with self.subTest(path=relative):
                self.assertTrue((destination / relative).is_file(), relative)
                self.assertEqual((destination / relative).read_bytes(), (RELEASE / relative).read_bytes())
        workflow = (destination / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn('unittest discover -s tests -p "test_*.py"', workflow)
        self.assertIn("node --test tests/adapters.test.cjs", workflow)
        self.assertTrue(list((destination / "tests").glob("test_*.py")))
        env = {**os.environ, "PYTHONPATH": str(destination), "PYTHONDONTWRITEBYTECODE": "1"}
        checked = self.run_python("scripts/check_release.py", env=env, cwd=destination)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        node = subprocess.run(
            ["node", "--test", "tests/adapters.test.cjs"],
            cwd=destination,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(node.returncode, 0, node.stdout + node.stderr)
        exported = json.loads((destination / "export-allowlist.json").read_text(encoding="utf-8"))
        self.assertEqual(set(exported), set(allowed))

    def test_development_red_output_is_neither_source_nor_export_entry(self):
        allowed = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
        self.assertNotIn("tests/sources-red.txt", allowed)
        self.assertFalse((RELEASE / "tests/sources-red.txt").exists())

    def test_setup_installs_synchronized_wrappers_and_all_referenced_support(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Setup is local only")):
            self.module("runtime").setup(root=self.root)
        missing = [relative for relative in (*WRAPPERS, *SUPPORT, LAUNCHER) if not (self.root / relative).is_file()]
        self.assertEqual(missing, [], "Setup must install every runtime entry point and referenced support file")
        for relative in WRAPPERS:
            with self.subTest(wrapper=relative):
                text = (self.root / relative).read_text(encoding="utf-8")
                self.assertEqual(text, (RELEASE / relative).read_text(encoding="utf-8"))
                self.assertIn(LAUNCHER, text)
                self.assertNotIn("-m autoapply_local", text, "Runtime commands must use the root-bound launcher")
        for name in COMMANDS:
            command = "profile update" if name == "profile-update" else name
            text = (self.root / f".claude/commands/{name}.md").read_text(encoding="utf-8")
            self.assertRegex(text, re.escape(LAUNCHER) + r"[\"']?\s+" + re.escape(command) + r"\b")
            self.assertIn("$ARGUMENTS", text)
        launcher = (self.root / LAUNCHER).read_bytes()
        assets = (RELEASE / "autoapply_local").rglob("*.py")
        self.assertTrue(
            any(asset.read_bytes() == launcher for asset in assets), "Launcher source must be a package asset"
        )

    def test_launcher_dispatches_root_before_subcommand_and_preserves_selected_engine_and_arguments(self):
        self.module("runtime").setup(root=self.root)
        launcher = self.root / LAUNCHER
        self.assertTrue(launcher.is_file(), "Setup must create the runtime-bound launcher")
        args = ["apply", "codex", "--quick", "backend-job,frontend-job"]
        with (
            patch.object(sys, "argv", [str(launcher), *args]),
            patch.dict(os.environ, {"AUTOAPPLY_LOCAL_HOME": str(self.root / "wrong runtime")}),
            patch("autoapply_local.cli.main", return_value=0) as main,
        ):
            try:
                runpy.run_path(str(launcher), run_name="__main__")
            except SystemExit as error:
                self.assertEqual(error.code, 0)
        main.assert_called_once_with(["--user-dir", str(self.root), *args])

    def test_bilingual_quickstarts_show_venv_initial_root_and_generated_command(self):
        for relative in ("README.md", "README.vi.md"):
            with self.subTest(document=relative):
                text = (RELEASE / relative).read_text(encoding="utf-8")
                for fragment in ("-m venv", "Activate.ps1", "bin/activate", LAUNCHER):
                    self.assertTrue(fragment in text, f"{relative} must show {fragment}")
                self.assertTrue(
                    re.search(r"-m autoapply_local\s+--user-dir\s+[^\n]+\s+setup\b", text),
                    f"{relative} must put the initial --user-dir before setup",
                )
                self.assertNotIn("## Giả định của người thực thi", text)
                self.assertNotIn("## Câu hỏi của người thực thi", text)

    def test_setup_preserves_preexisting_user_owned_bootstrap_files(self):
        originals = {}
        for relative in (*WRAPPERS, *SUPPORT, LAUNCHER, ".mcp.json", "mcp-codex.json"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = b'{"synthetic_user_owned": true}\n' if path.suffix == ".json" else b"User-owned synthetic edits\n"
            path.write_bytes(payload)
            originals[relative] = payload
        with patch("subprocess.Popen", side_effect=AssertionError("Setup must not launch engines")):
            for _ in range(2):
                self.module("runtime").setup(root=self.root)
        for relative, payload in originals.items():
            self.assertEqual((self.root / relative).read_bytes(), payload, relative)
        self.assertTrue((self.root / "user/profile-intake.md").is_file())

    def test_installed_wheel_bootstraps_default_custom_and_environment_roots_without_checkout(self):
        source = self.root / "build-source"
        shutil.copytree(
            RELEASE,
            source,
            ignore=shutil.ignore_patterns(
                ".git",
                "__pycache__",
                "*.pyc",
                "build",
                "dist",
                "*.egg-info",
                ".venv",
                "user",
                "logs",
                "runs",
                "backups",
                "browser",
                "accounts.md",
                "secrets.local.md",
                "profile.json",
                "answers.json",
                "reviews.json",
                "tracker.xlsx",
            ),
        )
        artifacts, target = self.root / "artifacts", self.root / "installed"
        env = {
            **os.environ,
            "PIP_NO_INDEX": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        built = self.run_python(
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            artifacts,
            source,
            env=env,
        )
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        sdist = self.run_python("setup.py", "sdist", "--dist-dir", artifacts, env=env, cwd=source)
        self.assertEqual(sdist.returncode, 0, sdist.stdout + sdist.stderr)
        wheels, archives = list(artifacts.glob("*.whl")), list(artifacts.glob("*.tar.gz"))
        self.assertEqual(len(wheels), 1)
        self.assertEqual(len(archives), 1)
        with zipfile.ZipFile(wheels[0]) as wheel, tarfile.open(archives[0]) as archive:
            self.assertFalse(any("tests" in Path(name).parts for name in wheel.namelist()))
            self.assertFalse(any("tests" in Path(name).parts for name in archive.getnames()))
        installed = self.run_python(
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--target",
            target,
            wheels[0],
            env=env,
        )
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        # The isolated checkout cannot satisfy any runtime resource references.
        source.rename(self.root / "checkout-no-longer-at-build-path")
        home = self.root / "synthetic home"
        home.mkdir()
        outside = self.root / "unrelated cwd"
        outside.mkdir()
        env.update(PYTHONPATH=str(target), HOME=str(home), USERPROFILE=str(home))
        env.pop("AUTOAPPLY_LOCAL_HOME", None)
        roots = (
            (home / ".autoapply-local", None, None),
            (self.root / "Lâm runtime có dấu", True, self.root / "conflicting-home"),
            (self.root / "environment runtime", None, self.root / "environment runtime"),
        )
        for root, explicit, override in roots:
            with self.subTest(root=root.name):
                selected_env = dict(env)
                if override is not None:
                    selected_env["AUTOAPPLY_LOCAL_HOME"] = str(override)
                args = ["-m", "autoapply_local"]
                if explicit:
                    args.extend(["--user-dir", str(root)])
                setup = self.run_python(*args, "setup", env=selected_env, cwd=outside)
                self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
                self.assertEqual(Path(json.loads(setup.stdout)["intake"]), root / "user/profile-intake.md")
                for relative in (*WRAPPERS, *SUPPORT, LAUNCHER):
                    self.assertTrue((root / relative).is_file(), relative)
                # Each generated instruction/launcher must come from installed assets.
                payloads = {path.read_bytes() for path in (target / "autoapply_local").rglob("*") if path.is_file()}
                for relative in (*WRAPPERS, LAUNCHER):
                    self.assertIn((root / relative).read_bytes(), payloads, relative)
                for conflict in (None, self.root / "wrong runtime"):
                    launch_env = dict(env)
                    if conflict is not None:
                        launch_env["AUTOAPPLY_LOCAL_HOME"] = str(conflict)
                    result = self.run_python(root / LAUNCHER, "setup", env=launch_env, cwd=outside)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(Path(json.loads(result.stdout)["intake"]), root / "user/profile-intake.md")
                self.assertFalse((self.root / "wrong runtime").exists())
        self.assertFalse((self.root / "conflicting-home").exists())
        self.assertFalse((home / ".codex").exists(), "Setup must not write global engine configuration")
        self.assertFalse((home / ".claude").exists(), "Setup must not write global engine configuration")


class ApplyPreflightTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.module("intake").setup(self.root)
        profile = candidate("authorized")
        profile["targets"]["roles"] = ["backend", "frontend"]
        profile["logistics"] = {"season": "summer", "year": 2030, "window": "2030-06-01 to 2030-08-31"}
        self.write_json("profile.json", profile)
        self.pdf = self.root / "main synthetic.pdf"
        self.alternative = self.root / "frontend synthetic.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n% synthetic main\n%%EOF")
        self.alternative.write_bytes(b"%PDF-1.4\n% synthetic alternative\n%%EOF")
        self.config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.config["runtime"].update(apply_engine="codex", tailor_engine="codex")
        self.config["resumes"] = {
            "main_pdf": str(self.pdf),
            "alternatives": [{"id": "frontend", "path": str(self.alternative)}],
            "role_mapping": {"frontend": "frontend"},
        }
        self.write_json("config.json", self.config)
        self.store = self.module("storage").Store(self.root)
        self.store.enqueue(job("backend-job"))
        self.store.enqueue(job("frontend-job", role="Frontend Software Engineering Intern"))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.bridge = Mock(spec=["open", "fill", "upload_file", "snapshot", "submit", "close"])
        self.bridge.open.side_effect = lambda url: "tab-" + url.rsplit("/", 1)[-1]
        self.bridge.fill.return_value = {"status": "filled"}
        self.bridge.snapshot.side_effect = lambda tab, *, job_id: snapshot(job_id, tab_id=tab)
        self.engine = self.fake_engine()
        self.events = []
        self.stack = self.enterContext(ExitStack())
        self.stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("No real processes")))
        self.stack.enter_context(patch("autoapply_local.fetch.urlopen", side_effect=AssertionError("No network")))
        self.launch = self.stack.enter_context(
            patch("autoapply_local.browser.launch_chrome", side_effect=self.launch_browser)
        )
        self.engine_factory = self.stack.enter_context(
            patch("autoapply_local.engines.Engine", side_effect=self.make_engine)
        )
        self.bridge_factory = self.stack.enter_context(
            patch("autoapply_local.browser.BrowserBridge", return_value=self.bridge)
        )
        self.stack.enter_context(patch("autoapply_local.browser.available_memory_mb", return_value=16384))
        self.tailor = self.stack.enter_context(patch("autoapply_local.tailor.Tailor"))

    def launch_browser(self, *args, **kwargs):
        self.events.append(("browser", self.stderr.getvalue(), self.stdout.getvalue()))
        return [
            {
                "lane": 1,
                "profile_dir": str(self.root / "browser/lane-1"),
                "debug_url": "http://127.0.0.1:9222",
                "pid": 1234,
            }
        ]

    def make_engine(self, *args, **kwargs):
        self.events.append(("engine", self.stderr.getvalue(), self.stdout.getvalue()))
        return self.engine

    def invoke(self, selection):
        return self.module("cli").main(
            ["--user-dir", str(self.root), "apply", "codex", "--quick", selection],
            stdout=self.stdout,
            stderr=self.stderr,
        )

    def test_quick_apply_reports_complete_preflight_before_external_work_and_keeps_stdout_json(self):
        originals = {path: path.read_bytes() for path in (self.pdf, self.alternative)}
        self.assertEqual(self.invoke("backend-job,frontend-job"), 0, self.stderr.getvalue())
        self.assertTrue(self.events, "The successful quick path must reach the mocked browser")
        for boundary, summary, stdout in self.events:
            with self.subTest(boundary=boundary):
                for value in (
                    "backend",
                    "frontend",
                    "summer",
                    "2030",
                    "2030-06-01",
                    "2030-08-31",
                    "backend-job",
                    "frontend-job",
                    str(self.pdf),
                    str(self.alternative),
                ):
                    self.assertIn(value.casefold(), summary.casefold())
                self.assertEqual(stdout, "", "Only the final JSON belongs on stdout")
        report = json.loads(self.stdout.getvalue())
        self.assertIsInstance(report, dict)
        self.assertEqual(self.store.get("backend-job")["state"], "filled")
        self.assertEqual(self.store.get("frontend-job")["state"], "filled")
        self.assertEqual(self.bridge.upload_file.call_args_list[0].args[1], str(self.pdf))
        self.assertEqual(self.bridge.upload_file.call_args_list[1].args[1], str(self.alternative))
        self.assertEqual(self.engine_factory.call_count, 1)
        self.assertEqual(self.engine_factory.call_args.args[0], "codex")
        self.assertTrue(all(call.args[0]["kind"] == "apply_analysis" for call in self.engine.run.call_args_list))
        self.tailor.assert_not_called()
        self.bridge.submit.assert_not_called()
        self.bridge.close.assert_not_called()
        self.assertEqual(self.store.tracker_rows(), [])
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_bad_later_resume_fails_before_any_browser_or_engine_work(self):
        self.alternative.write_bytes(b"synthetic non-PDF")
        self.assertEqual(self.invoke("backend-job,frontend-job"), 2, self.stderr.getvalue())
        self.assertIn("PDF", self.stderr.getvalue())
        self.assert_no_external_work()

    def test_missing_main_resume_fails_before_any_browser_or_engine_work(self):
        self.config["resumes"]["main_pdf"] = str(self.root / "missing.pdf")
        self.write_json("config.json", self.config)
        self.assertEqual(self.invoke("backend-job"), 2, self.stderr.getvalue())
        self.assert_no_external_work()

    def test_malformed_or_unknown_selections_fail_before_browser_work(self):
        for selection in ("0", "-1", "backend-job,", "backend-job,missing-job"):
            with self.subTest(selection=selection):
                self.assertEqual(self.invoke(selection), 2, self.stderr.getvalue())
                self.assert_no_external_work()

    def assert_no_external_work(self):
        self.launch.assert_not_called()
        self.engine_factory.assert_not_called()
        self.bridge_factory.assert_not_called()
        self.bridge.open.assert_not_called()
        self.bridge.submit.assert_not_called()
        self.tailor.assert_not_called()
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertEqual(
            [self.store.get(key)["state"] for key in ("backend-job", "frontend-job")], ["pending", "pending"]
        )
