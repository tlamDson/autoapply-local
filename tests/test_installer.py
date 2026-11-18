"""Offline contracts for install.ps1 / install.sh and scripts/install.py (mocked processes)."""

import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_support import RELEASE

from autoapply_local import install_check


def load_installer():
    spec = importlib.util.spec_from_file_location("autoapply_installer", RELEASE / "scripts/install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OK = {"os": "windows", "ok": True, "checks": [], "missing": []}
MISSING = {
    "os": "macos", "ok": False, "checks": [], "homebrew": True,
    "missing": [{"name": "node", "command": "brew install node"}],
}


class InstallerFlowTests(unittest.TestCase):
    def setUp(self):
        self.installer = load_installer()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "checkout"
        self.repo.mkdir()
        self.calls, self.created = [], []

    def fake_run(self, argv, **options):
        self.calls.append(([str(item) for item in argv], options))
        return 0

    def fake_create(self, path):
        self.created.append(Path(path))
        python = self.installer.venv_python(self.repo)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("synthetic interpreter\n", encoding="utf-8")

    def install(self, arguments=(), report=OK, run=None):
        output = io.StringIO()
        with redirect_stdout(output):
            code = self.installer.main(
                list(arguments), root=self.repo, run=run or self.fake_run,
                create_venv=self.fake_create, detect=lambda: report,
            )
        return code, output.getvalue()

    def test_missing_dependency_prints_commands_and_changes_nothing(self):
        code, output = self.install(report=MISSING)
        self.assertEqual(code, 3, output)
        self.assertIn("brew install node", output)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.created, [])
        self.assertFalse((self.repo / ".venv").exists())

    def test_first_run_creates_repo_venv_installs_editable_and_starts_setup(self):
        code, output = self.install()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.created, [self.repo / ".venv"])
        python = str(self.installer.venv_python(self.repo))
        self.assertTrue(Path(python).is_relative_to(self.repo / ".venv"))
        (pip, pip_options), (setup, setup_options) = self.calls
        self.assertEqual(pip, [python, "-m", "pip", "install", "-e", str(self.repo)])
        # A user-level pip config with user = true must not break a venv install.
        self.assertEqual(pip_options["env"]["PIP_USER"], "0")
        self.assertEqual(Path(pip_options["cwd"]), self.repo)
        self.assertEqual(setup, [python, str(self.repo / "autoapply-local.py"), "setup"])
        self.assertEqual(Path(setup_options["cwd"]), self.repo)

    def test_rerun_reuses_venv_and_repeats_the_same_steps(self):
        self.assertEqual(self.install()[0], 0)
        first = [argv for argv, _ in self.calls]
        self.calls.clear()
        code, output = self.install()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.created, [self.repo / ".venv"], "An existing .venv must be reused")
        self.assertEqual([argv for argv, _ in self.calls], first)
        self.assertIn("reusing", output.lower())

    def test_no_setup_flag_skips_auto_start(self):
        code, _ = self.install(["--no-setup"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("pip", self.calls[0][0])

    def test_failed_pip_install_stops_before_setup(self):
        def failing(argv, **options):
            self.calls.append(([str(item) for item in argv], options))
            return 1

        code, output = self.install(run=failing)
        self.assertEqual(code, 4, output)
        self.assertEqual(len(self.calls), 1)

    def test_venv_python_path_per_os(self):
        windows = self.installer.venv_python(self.repo, system="Windows")
        mac = self.installer.venv_python(self.repo, system="Darwin")
        self.assertEqual(windows, self.repo / ".venv/Scripts/python.exe")
        self.assertEqual(mac, self.repo / ".venv/bin/python")


class ShellScriptTests(unittest.TestCase):
    def read(self, name):
        path = RELEASE / name
        self.assertTrue(path.is_file(), name + " must ship at the repository root")
        return path.read_text(encoding="utf-8")

    def test_scripts_hand_off_to_python_installer_and_clone_when_needed(self):
        for name in ("install.ps1", "install.sh"):
            with self.subTest(script=name):
                text = self.read(name)
                self.assertIn("scripts/install.py", text)
                self.assertIn("https://github.com/tlamDson/autoapply-local.git", text)
                self.assertIn("git clone", text)
                self.assertIn("3, 11", text, "Scripts must require Python 3.11+")
                self.assertNotIn("winget install -e --id Python" if name.endswith(".sh") else "brew install", text)

    def test_python_hint_matches_dependency_report(self):
        self.assertIn(install_check.COMMANDS["windows"]["python"], self.read("install.ps1"))
        self.assertIn(install_check.COMMANDS["windows"]["git"], self.read("install.ps1"))
        self.assertIn(install_check.COMMANDS["macos"]["python"], self.read("install.sh"))
        self.assertIn(install_check.COMMANDS["macos"]["git"], self.read("install.sh"))
        self.assertIn(install_check.HOMEBREW_INSTALL, self.read("install.sh"))

    def test_scripts_never_install_system_software(self):
        for name in ("install.ps1", "install.sh"):
            text = self.read(name)
            for line in text.splitlines():
                stripped = line.strip()
                # Install commands may appear only inside printed hints.
                if "winget install" in stripped or "brew install" in stripped:
                    self.assertTrue(stripped.startswith(("Write-Host", "echo", "hint", "Show-Hint", "'", '"', "printf")),
                                    name + ": " + stripped)

    def test_iex_mode_does_not_exit_the_callers_shell(self):
        text = self.read("install.ps1")
        exits = [line.strip() for line in text.splitlines() if "exit " in line and not line.strip().startswith("#")]
        # `irm | iex` runs in the caller's session: only a script file may exit.
        self.assertEqual(exits, ["if ($PSCommandPath) { exit $code }"], exits)

    def test_pipe_mode_reads_whole_script_before_running(self):
        text = self.read("install.sh")
        self.assertTrue(text.rstrip().endswith('main "$@"'), "curl | bash must parse everything first")
        self.assertIn("/dev/tty", text)

    def test_bash_syntax(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is unavailable on this machine")
        result = subprocess.run([bash, "-n", str(RELEASE / "install.sh")], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_powershell_syntax(self):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell is None:
            self.skipTest("PowerShell is unavailable on this machine")
        command = (
            "$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
            "$env:AUTOAPPLY_TEST_SCRIPT, [ref]$null, [ref]$e); if ($e.Count) { $e | Out-String; exit 1 }"
        )
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True,
            timeout=60, env={**os.environ, "AUTOAPPLY_TEST_SCRIPT": str(RELEASE / "install.ps1")},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class SourceDefaultsTests(unittest.TestCase):
    def test_gitignore_covers_repo_venv_and_user_root(self):
        lines = (RELEASE / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".venv/", lines)
        self.assertIn("user/", lines)

    def test_sdist_manifest_carries_root_installers(self):
        words = set()
        for line in (RELEASE / "MANIFEST.in").read_text(encoding="utf-8").splitlines():
            if line.split()[:1] == ["include"]:
                words.update(line.split()[1:])
        self.assertTrue({"install.ps1", "install.sh"} <= words, "the sdist audit requires both installers")

    def test_installer_files_are_allowlisted(self):
        import json

        allowed = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
        for name in ("install.ps1", "install.sh", "scripts/install.py", "autoapply_local/install_check.py",
                     "tests/test_install_check.py", "tests/test_installer.py"):
            self.assertIn(name, allowed)


if __name__ == "__main__":
    unittest.main()
