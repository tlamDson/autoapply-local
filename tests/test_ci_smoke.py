"""CI install smoke (plan aa-22): install scripts on a clean runner, doctor, scripted setup, demo fill.

``scripts/ci_smoke.py`` runs after ``install.ps1`` / ``install.sh`` in the
``install-smoke`` CI job. It drives the installed launcher through doctor, the
terminal setup with scripted stdin (an offline fake engine stands in for the
engine CLI), a confirmed profile for an invented candidate, and the offline demo
form. These tests cover its pieces offline; the CI job is the live check.
"""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_support import RELEASE

from autoapply_local import engines, intake, quota, runtime, terminal_setup


def load_smoke():
    spec = importlib.util.spec_from_file_location("autoapply_ci_smoke", RELEASE / "scripts/ci_smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TempCase(unittest.TestCase):
    def setUp(self):
        self.smoke = load_smoke()
        self.tmp = Path(tempfile.mkdtemp()).resolve()  # macOS: /var is /private/var
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class FakeEngineTests(TempCase):
    def test_windows_layout_is_an_npm_shim_with_a_javascript_entrypoint(self):
        self.smoke.fake_engine(self.tmp, system="Windows")
        self.assertTrue((self.tmp / "claude.cmd").is_file())
        package = self.tmp / "node_modules/@anthropic-ai/claude-code"
        metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["name"], "@anthropic-ai/claude-code")
        self.assertTrue((package / metadata["bin"]["claude"]).is_file())

    def test_posix_layout_is_an_executable_node_script(self):
        self.smoke.fake_engine(self.tmp, system="Darwin")
        script = self.tmp / "claude"
        self.assertTrue(script.read_text(encoding="utf-8").startswith("#!/usr/bin/env node"))
        if os.name != "nt":
            self.assertTrue(os.access(script, os.X_OK))

    @unittest.skipUnless(shutil.which("node"), "the fake engine is a Node script; CI installs Node")
    def test_real_probe_reads_the_fake_engine_as_signed_in_with_quota(self):
        self.smoke.fake_engine(self.tmp)
        path = str(self.tmp) + os.pathsep + os.environ.get("PATH", "")
        with mock.patch.dict(os.environ, {"PATH": path}):
            argv = engines.resolve_executable("claude")
            report = quota.probe("claude", cwd=self.tmp, timeout=60)
        self.assertTrue(any(str(self.tmp) in part for part in argv), argv)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["five_hour_pct"], 12.0)
        self.assertEqual(report["seven_day_pct"], 34.0)


class ScriptedSetupTests(TempCase):
    def test_answers_drive_the_terminal_wizard_to_a_saved_config(self):
        pdf = self.smoke.write_resume(self.tmp / "Alex_Rivera_Resume.pdf")
        root = self.tmp / "runtime"
        root.mkdir()
        lines = iter(self.smoke.setup_answers(pdf).splitlines())
        gmail = mock.Mock()
        result = terminal_setup.run(
            root, ask=lambda prompt: next(lines), out=io.StringIO(),
            probe=lambda engine, cwd=None: {"status": "ready", "five_hour_pct": 1.0, "seven_day_pct": 2.0},
            memory_mb=8192, gmail=gmail,
        )
        self.assertEqual(list(lines), [])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["gmail"], "skipped")
        gmail.open.assert_not_called()
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.smoke.check_setup(root, result)
        self.assertEqual(config["runtime"]["jobs_per_run"], self.smoke.JOBS_PER_RUN)
        self.assertEqual(config["resumes"]["main_tags"], ["SWE"])

    def test_check_setup_rejects_a_config_without_the_copied_resume(self):
        root = self.tmp / "runtime"
        root.mkdir()
        (root / "config.json").write_text(json.dumps({"runtime": {"apply_engine": "claude"}}), encoding="utf-8")
        with self.assertRaises(self.smoke.SmokeError):
            self.smoke.check_setup(root, {"status": "ready", "engine": "claude", "gmail": "skipped",
                                          "jobs_per_run": self.smoke.JOBS_PER_RUN})

    def test_resume_is_a_pdf_the_wizard_accepts(self):
        pdf = self.smoke.write_resume(self.tmp / "resume.pdf")
        self.assertEqual(pdf.read_bytes()[:5], b"%PDF-")


class ProfileTests(TempCase):
    def test_filled_intake_confirms_an_invented_contact(self):
        root = self.tmp / "runtime"
        runtime.setup(root=root)
        text = (root / "user/profile-intake.md").read_text(encoding="utf-8")
        path = self.tmp / "intake.md"
        path.write_text(self.smoke.fill_intake(text), encoding="utf-8")
        intake.apply_import(root, intake.preview_import(root, path), confirm=True)
        contact = json.loads((root / "profile.json").read_text(encoding="utf-8"))["contact"]
        self.assertEqual(contact["first_name"]["value"], "Alex")
        self.assertEqual(contact["last_name"]["value"], "Rivera")
        self.assertTrue(contact["application_email"]["value"].endswith("@example.com"))
        self.assertEqual(contact["first_name"]["status"], "confirmed")


class ChecksTests(TempCase):
    def test_last_json_skips_wizard_prompts(self):
        text = 'Language [1]: \n{ not json\n{\n  "status": "ready",\n  "nested": {"a": 1}\n}\n'
        self.assertEqual(self.smoke.last_json(text), {"status": "ready", "nested": {"a": 1}})

    def test_last_json_reads_a_result_printed_right_after_a_prompt(self):
        text = 'Connect Gmail now? [Y/n]: {\n  "status": "ready",\n  "gmail": "skipped"\n}\n'
        self.assertEqual(self.smoke.last_json(text), {"status": "ready", "gmail": "skipped"})

    def test_last_json_without_a_result_fails(self):
        with self.assertRaises(self.smoke.SmokeError):
            self.smoke.last_json("Language [1]: \n")

    def test_doctor_needs_chrome_and_node(self):
        good = {"chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "node": "/usr/bin/node",
                "npx": "/usr/bin/npx"}
        self.smoke.check_doctor(good)
        for missing in ("chrome", "node", "npx"):
            with self.subTest(missing=missing), self.assertRaises(self.smoke.SmokeError):
                self.smoke.check_doctor({**good, missing: None})

    def test_doctor_with_the_engine_must_resolve_it(self):
        good = {"chrome": "c", "node": "n", "npx": "x", "engine": "claude", "executable_status": "resolved"}
        self.smoke.check_doctor(good, engine="claude")
        with self.assertRaises(self.smoke.SmokeError):
            self.smoke.check_doctor({**good, "executable_status": "configuration"}, engine="claude")

    def test_demo_must_fill_the_contact_fields(self):
        good = {"status": "filled", "ok": True, "filled": ["first_name", "last_name", "email", "phone"]}
        self.smoke.check_demo(good)
        bad = (
            {**good, "ok": False},
            {**good, "filled": ["first_name"]},
            {**good, "status": "submitted"},
        )
        for result in bad:
            with self.subTest(result=result), self.assertRaises(self.smoke.SmokeError):
                self.smoke.check_demo(result)


class MainTests(TempCase):
    """``main`` with a fake process runner: order of commands, scripted stdin, no submit, Chrome stopped."""

    def fake_runner(self, root, calls):
        def run(argv, **kwargs):
            command = argv[2 + argv.index("--user-dir"):] if "--user-dir" in argv else argv[2:]
            calls.append((command, kwargs))
            if command[:1] == ["doctor"]:
                result = {"chrome": "c", "node": "n", "npx": "x", "engine": "claude" if len(calls) > 1 else None,
                          "executable_status": "resolved" if len(calls) > 1 else "not_selected"}
            elif command[:1] == ["setup"]:
                copied = root / "resumes/Alex_Rivera_Resume.pdf"
                copied.parent.mkdir(parents=True, exist_ok=True)
                copied.write_bytes(b"%PDF-1.4")
                (root / "config.json").write_text(json.dumps({
                    "runtime": {"apply_engine": "claude", "jobs_per_run": self.smoke.JOBS_PER_RUN},
                    "resumes": {"main_pdf": str(copied), "main_tags": ["SWE"]},
                    "gmail": {"status": "skipped"},
                }), encoding="utf-8")
                result = {"status": "ready", "engine": "claude", "gmail": "skipped",
                          "jobs_per_run": self.smoke.JOBS_PER_RUN}
                stdout = "Language [1]: \n" + json.dumps(result, indent=2) + "\n"
                return subprocess.CompletedProcess(argv, 0, stdout, "")
            elif command[:2] == ["profile", "update"]:
                result = {"applied": True}
            elif command[:1] == ["demo"]:
                lane = root / "chrome/lane-1"
                lane.mkdir(parents=True, exist_ok=True)
                (lane / "autoapply-owner.json").write_text(json.dumps({"pid": 4242}), encoding="utf-8")
                result = {"status": "filled", "ok": True, "filled": ["first_name", "last_name", "email", "phone"]}
            else:
                raise AssertionError(f"unexpected command {command}")
            return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")
        return run

    def test_main_runs_doctor_setup_profile_and_demo_then_stops_chrome(self):
        repo = self.tmp / "repo"
        python = self.smoke.venv_python(repo)
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
        (repo / "autoapply-local.py").write_text("", encoding="utf-8")
        root = repo / "user"
        root.mkdir()
        (root / "user").mkdir()
        (root / "config.json").write_text("{}", encoding="utf-8")
        (root / "user/profile-intake.md").write_text(
            "```json\n" + json.dumps({"schema_version": 1, "groups": {"contact": {}}}) + "\n```\n", encoding="utf-8")
        calls, killed = [], []
        code = self.smoke.main(["--repo", str(repo), "--work-dir", str(self.tmp / "work")],
                               run=self.fake_runner(root, calls), kill=killed.append, out=io.StringIO())
        self.assertEqual(code, 0)
        commands = [command for command, _ in calls]
        self.assertEqual([command[0] for command in commands], ["doctor", "setup", "doctor", "profile", "demo"])
        self.assertEqual(commands[1], ["setup", "--terminal", "--no-launch"])
        self.assertIn("--confirm", commands[3])
        self.assertTrue(all("submit" not in part.casefold() for command in commands for part in command))
        setup_kwargs = calls[1][1]
        self.assertIn("Alex_Rivera_Resume.pdf", setup_kwargs["input"])
        self.assertTrue(setup_kwargs["env"]["PATH"].startswith(str((self.tmp / "work").resolve())))  # macOS: /var is /private/var
        for _, kwargs in calls:
            self.assertEqual(Path(kwargs["cwd"]), repo.resolve())
        self.assertEqual(calls[0][0], ["doctor"])
        self.assertTrue(all(call[0] is not None for call in calls))
        self.assertEqual(killed, [4242])

    def test_main_fails_when_the_installer_did_not_start_setup(self):
        repo = self.tmp / "repo"
        python = self.smoke.venv_python(repo)
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
        (repo / "autoapply-local.py").write_text("", encoding="utf-8")
        calls, out = [], io.StringIO()
        code = self.smoke.main(["--repo", str(repo), "--work-dir", str(self.tmp / "work")],
                               run=lambda argv, **kwargs: calls.append(argv), kill=lambda pid: None, out=out)
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertIn("setup", out.getvalue())

    def test_main_fails_before_any_command_without_the_installed_venv(self):
        calls = []
        code = self.smoke.main(["--repo", str(self.tmp / "missing"), "--work-dir", str(self.tmp / "work")],
                               run=lambda argv, **kwargs: calls.append(argv), kill=lambda pid: None,
                               out=io.StringIO())
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])


class WorkflowTests(unittest.TestCase):
    def test_install_smoke_job_runs_both_installers_then_the_smoke(self):
        text = (RELEASE / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("install-smoke:", text)
        job = text.split("install-smoke:", 1)[1]
        self.assertIn("windows-latest", job)
        self.assertIn("macos-latest", job)
        self.assertIn("./install.ps1", job)
        self.assertIn("shell: pwsh", job)
        self.assertIn("./install.sh", job)
        self.assertIn("scripts/ci_smoke.py", job)

    def test_smoke_script_ships_in_the_release(self):
        allowlist = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
        self.assertIn("scripts/ci_smoke.py", json.dumps(allowlist))


if __name__ == "__main__":
    unittest.main()
