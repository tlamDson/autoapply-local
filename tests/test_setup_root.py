"""Offline contracts for PR1's source-local runtime root (tests only).

Giả định của người thực thi: source recognition is tested through cli.main and
synthetic product markers, without prescribing a new helper API. A source launcher
with no arguments starts setup. Public source copies and all runtime/Git writes
live in temporary directories; subprocesses are limited to synthetic Git repos.
The plan is untouched because this turn permits editing only this test file.
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
import zipfile
from contextlib import chdir, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from test_support import RELEASE, ReleaseCase


RUNTIME = ".autoapply-local"
LAUNCHER = "autoapply-local.py"


class SetupRootCase(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.home = self.root / "synthetic home"
        self.home.mkdir()
        environment = dict(os.environ)
        environment.pop("AUTOAPPLY_LOCAL_HOME", None)
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.home_patch = patch.object(Path, "home", return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def source(self, name="source checkout", public_copy=False):
        source = self.root / name
        source.mkdir()
        if public_copy:
            # Copy public, manifest-selected code/assets only, never a user's
            # runtime or the real repository's Git metadata.
            allowed = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
            selected = [
                item for item in allowed
                if item.startswith("autoapply_local/")
                or item in ("pyproject.toml", ".gitignore", "scripts/check_release.py", LAUNCHER)
            ]
            for relative in selected:
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(RELEASE / relative, target)
        else:
            (source / "autoapply_local").mkdir()
            (source / "autoapply_local/__init__.py").write_text(
                '"""Synthetic product marker."""\n', encoding="utf-8"
            )
            (source / "pyproject.toml").write_text(
                '[project]\nname = "autoapply-local"\nversion = "0.1.0a1"\n', encoding="utf-8"
            )
        allowed = sorted(
            path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()
        )
        allowed.append("export-allowlist.json")
        (source / "export-allowlist.json").write_text(json.dumps(allowed), encoding="utf-8")
        return source, allowed

    def invoke(self, cwd, arguments, expected, environment=None):
        command = arguments[-1]
        service = Mock(return_value={"status": "synthetic"})
        output, errors = io.StringIO(), io.StringIO()
        with (
            chdir(cwd),
            patch.dict(os.environ, environment or {}),
            patch("subprocess.Popen", side_effect=AssertionError("Root resolution must be offline")),
        ):
            code = self.module("cli").main(
                arguments, services={command: service}, stdout=output, stderr=errors
            )
        self.assertEqual(code, 0, errors.getvalue())
        service.assert_called_once_with(root=expected.resolve())
        self.assertFalse((self.home / RUNTIME).exists(), "A spy must never create a real runtime")

    def real_setup(self, source):
        output, errors = io.StringIO(), io.StringIO()
        with (
            chdir(source),
            patch("subprocess.Popen", side_effect=AssertionError("Setup must stay local")),
        ):
            code = self.module("cli").main(
                ["setup"], services={"setup": self.module("runtime").setup},
                stdout=output, stderr=errors,
            )
        self.assertEqual(code, 0, errors.getvalue())
        runtime = source / RUNTIME
        self.assertTrue((runtime / "user/profile-intake.md").is_file(), output.getvalue())
        self.assertFalse((self.home / RUNTIME).exists(), "Source setup must not use home")
        self.assertFalse((source.parent / RUNTIME).exists(), "Source setup must not use a sibling")
        return runtime

    def checker(self, source):
        spec = importlib.util.spec_from_file_location("setup_root_release_checker", RELEASE / "scripts/check_release.py")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        checker.ROOT = source
        return checker

    def check(self, checker, arguments=()):
        output = io.StringIO()
        with redirect_stdout(output):
            code = checker.main(list(arguments))
        return code, output.getvalue()

    def git(self, source, *arguments):
        executable = shutil.which("git")
        if executable is None:
            self.skipTest("Git is unavailable; synthetic repository integration requires Git")
        completed = subprocess.run(
            [executable, "-C", str(source), *arguments], capture_output=True,
            text=True, encoding="utf-8", timeout=15,
        )
        return completed

    def init_git(self, source, allowed):
        result = self.git(source, "init", "--quiet")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.git(source, "add", "--", *allowed)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class SourceRootResolutionTests(SetupRootCase):
    def test_setup_from_product_source_without_git_uses_local_root(self):
        source, _ = self.source()
        self.assertFalse((source / ".git").exists(), "ZIP sources need no Git metadata")
        self.invoke(source, ["setup"], source / RUNTIME)

    def test_setup_from_nested_source_directory_uses_source_root(self):
        source, _ = self.source()
        nested = source / "docs/nested"
        nested.mkdir(parents=True)
        self.invoke(nested, ["setup"], source / RUNTIME)

    def test_non_setup_commands_use_same_detected_source_root(self):
        source, _ = self.source()
        for command in ("status", "boards"):
            with self.subTest(command=command):
                if command == "status":
                    self.invoke(source, [command], source / RUNTIME)
                else:
                    service = Mock(return_value=[])
                    with chdir(source), patch("subprocess.Popen", side_effect=AssertionError("No processes")):
                        code = self.module("cli").main(
                            ["boards", "list"], services={"boards_list": service},
                            stdout=io.StringIO(), stderr=io.StringIO(),
                        )
                    self.assertEqual(code, 0)
                    service.assert_called_once_with(root=(source / RUNTIME).resolve())

    def test_explicit_root_wins_over_environment_and_source(self):
        source, _ = self.source()
        explicit = self.root / "explicit runtime"
        self.invoke(
            source, ["--user-dir", str(explicit), "setup"], explicit,
            {"AUTOAPPLY_LOCAL_HOME": str(self.root / "environment runtime")},
        )

    def test_relative_explicit_root_remains_relative_to_caller(self):
        source, _ = self.source()
        nested = source / "nested"
        nested.mkdir()
        self.invoke(nested, ["--user-dir", "chosen", "setup"], nested / "chosen")

    def test_environment_root_wins_over_source(self):
        source, _ = self.source()
        chosen = self.root / "environment runtime"
        for command in ("setup", "status"):
            with self.subTest(command=command):
                self.invoke(source, [command], chosen, {"AUTOAPPLY_LOCAL_HOME": str(chosen)})

    def test_explicit_root_expands_home_before_resolution(self):
        source, _ = self.source()
        # expanduser uses OS home variables rather than Path.home's test patch.
        with patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)}):
            self.invoke(source, ["--user-dir", "~/chosen", "setup"], self.home / "chosen")

    def test_outside_source_uses_home_fallback(self):
        for command in ("setup", "status"):
            with self.subTest(command=command):
                self.invoke(self.root, [command], self.home / RUNTIME)

    def test_unrelated_git_root_does_not_override_home(self):
        unrelated = self.root / "unrelated"
        (unrelated / ".git").mkdir(parents=True)
        nested = unrelated / "nested"
        nested.mkdir()
        self.invoke(nested, ["setup"], self.home / RUNTIME)

    def test_product_markers_are_all_required(self):
        for missing in ("pyproject.toml", "autoapply_local", "export-allowlist.json"):
            with self.subTest(missing=missing):
                source, _ = self.source("missing " + missing.replace(".", "_"))
                marker = source / missing
                if marker.is_dir():
                    shutil.rmtree(marker)
                else:
                    marker.unlink()
                (source / ".git").mkdir()
                self.invoke(source, ["setup"], self.home / RUNTIME)

    def test_other_project_name_with_similar_files_is_not_product_source(self):
        source, _ = self.source()
        (source / "pyproject.toml").write_text(
            '[project]\nname = "unrelated-product"\n', encoding="utf-8"
        )
        self.invoke(source, ["setup"], self.home / RUNTIME)

    def test_product_source_inside_unrelated_parent_repo_uses_product_root(self):
        (self.root / ".git").mkdir()
        source, _ = self.source()
        self.invoke(source, ["setup"], source / RUNTIME)


class SourceLauncherTests(SetupRootCase):
    def run_launcher(self, arguments, expected, command="setup", environment=None, source_launcher=True):
        if source_launcher:
            launcher = RELEASE / LAUNCHER
            self.assertTrue(launcher.is_file(), "PR1 requires the source checkout launcher")
            shutil.copy2(launcher, self.root / LAUNCHER)
            launcher = self.root / LAUNCHER
            expected = self.root / RUNTIME if expected is None else expected
        else:
            runtime = self.root / "bound runtime"
            with patch("subprocess.Popen", side_effect=AssertionError("Setup is local")):
                self.module("runtime").setup(root=runtime)
            launcher = runtime / LAUNCHER
            expected = runtime
        caller = self.root / "unrelated caller"
        caller.mkdir()
        service = Mock(return_value={"status": "synthetic"})
        cli = self.module("cli")
        actual_main = cli.main
        output, errors = io.StringIO(), io.StringIO()

        def dispatch(argv=None):
            return actual_main(argv, services={command: service}, stdout=output, stderr=errors)

        with (
            chdir(caller),
            patch.dict(os.environ, environment or {}),
            patch.object(sys, "argv", [str(launcher), *arguments]),
            patch.object(cli, "main", side_effect=dispatch) as main,
            patch("subprocess.Popen", side_effect=AssertionError("Launcher must not start engines")),
        ):
            before_environment = dict(os.environ)
            try:
                runpy.run_path(str(launcher), run_name="__main__")
            except SystemExit as error:
                self.assertEqual(error.code, 0, errors.getvalue())
            self.assertEqual(Path.cwd(), caller, "Launcher must not change caller cwd")
            self.assertEqual(dict(os.environ), before_environment, "Launcher must not alter process config")
        main.assert_called_once()
        service.assert_called_once_with(root=expected.resolve())

    def test_source_launcher_with_no_arguments_starts_setup_beside_itself(self):
        self.run_launcher([], None)

    def test_source_launcher_status_ignores_unrelated_caller_cwd(self):
        self.run_launcher(["status"], None, command="status")

    def test_source_launcher_preserves_explicit_global_root(self):
        explicit = self.root / "explicit"
        self.run_launcher(
            ["--user-dir", str(explicit), "setup"], explicit,
            environment={"AUTOAPPLY_LOCAL_HOME": str(self.root / "environment")},
        )

    def test_source_launcher_preserves_environment_root(self):
        chosen = self.root / "environment"
        self.run_launcher(["setup"], chosen, environment={"AUTOAPPLY_LOCAL_HOME": str(chosen)})

    def test_runtime_bound_launcher_keeps_own_root_despite_environment(self):
        self.run_launcher(
            ["status"], None, command="status", source_launcher=False,
            environment={"AUTOAPPLY_LOCAL_HOME": str(self.root / "wrong runtime")},
        )


class SourceSetupIntegrationTests(SetupRootCase):
    def test_real_setup_in_temporary_source_preserves_intake_and_mcp_on_repeat(self):
        source, _ = self.source(public_copy=True)
        runtime = self.real_setup(source)
        originals = {
            "user/profile-intake.md": b"Synthetic user-edited intake\n",
            ".mcp.json": b'{"synthetic_user_owned": true}\n',
            "mcp-codex.json": b'{"synthetic_user_owned": true}\n',
        }
        for relative, payload in originals.items():
            self.assertTrue((runtime / relative).is_file(), relative)
            (runtime / relative).write_bytes(payload)
        self.real_setup(source)
        for relative, payload in originals.items():
            self.assertEqual((runtime / relative).read_bytes(), payload, relative)
        self.assertTrue((runtime / LAUNCHER).is_file())
        self.assertEqual(list(self.home.iterdir()), [], "No global config may be written")

    def test_source_gitignore_covers_whole_runtime_and_git_add_does_not_track_it(self):
        source, allowed = self.source(public_copy=True)
        self.init_git(source, allowed)
        # Create runtime through the existing service so an old root resolver
        # cannot mask a separate missing ignore rule.
        runtime = source / RUNTIME
        with patch("subprocess.Popen", side_effect=AssertionError("Setup stays local")):
            self.module("runtime").setup(root=runtime)
        for relative in ("user/profile-intake.md", LAUNCHER, "cookies/session.txt", "arbitrary.txt"):
            path = runtime / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("Synthetic runtime only\n", encoding="utf-8")
            name = path.relative_to(source).as_posix()
            with self.subTest(path=name):
                result = self.git(source, "check-ignore", "--", name)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.strip(), name)
        result = self.git(source, "add", "--all")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.git(source, "ls-files", "-z")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(name.startswith(RUNTIME + "/") for name in result.stdout.split("\0")))

    def test_release_checker_exports_and_zips_source_without_managed_runtime(self):
        source, allowed = self.source(public_copy=True)
        with patch("subprocess.Popen", side_effect=AssertionError("Setup stays local")):
            self.module("runtime").setup(root=source / RUNTIME)
        checker = self.checker(source)
        destination = self.root / "export"
        archives = self.root / "archives"
        code, output = self.check(checker, ["--export", str(destination), "--source-zip", str(archives)])
        self.assertEqual(code, 0, output)
        exported = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()}
        self.assertEqual(exported, set(allowed))
        zipped = list(archives.glob("*.zip"))
        self.assertEqual(len(zipped), 1)
        with zipfile.ZipFile(zipped[0]) as archive:
            names = archive.namelist()
        self.assertEqual({name.split("/", 1)[1] for name in names}, set(allowed))
        self.assertFalse(any(RUNTIME in Path(name).parts for name in names))

    def test_release_checker_does_not_ignore_other_unlisted_trees(self):
        source, _ = self.source(public_copy=True)
        checker = self.checker(source)
        for relative in ("scratch/note.txt", "custom-runtime/note.txt", ".autoapply-local-copy/note.txt"):
            with self.subTest(path=relative):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Synthetic unlisted material\n", encoding="utf-8")
                code, output = self.check(checker)
                self.assertEqual(code, 1, output)
                report = json.loads(output)
                self.assertIn(relative, [item["path"] for item in report["violations"]])
                path.unlink()

    def test_release_checker_only_exempts_runtime_tree_at_source_root(self):
        source, _ = self.source(public_copy=True)
        relative = "nested/.autoapply-local/note.txt"
        path = source / relative
        path.parent.mkdir(parents=True)
        path.write_text("Synthetic unlisted material\n", encoding="utf-8")
        code, output = self.check(self.checker(source))
        self.assertEqual(code, 1, output)
        self.assertIn(relative, [item["path"] for item in json.loads(output)["violations"]])

    def test_tracked_runtime_is_rejected_even_when_ignored_and_allowlisted(self):
        source, allowed = self.source(public_copy=True)
        self.init_git(source, allowed)
        relative = RUNTIME + "/note.txt"
        path = source / relative
        path.parent.mkdir()
        path.write_text("Synthetic private runtime\n", encoding="utf-8")
        result = self.git(source, "add", "--force", "--", relative)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Including it in the manifest must not launder tracked runtime data.
        (source / "export-allowlist.json").write_text(json.dumps([*allowed, relative]), encoding="utf-8")
        code, output = self.check(self.checker(source))
        self.assertEqual(code, 1, output)
        self.assertTrue(
            any(item["path"] == relative and "private" in item["reason"]
                for item in json.loads(output)["violations"]), output,
        )

    def test_tracked_runtime_absent_from_disk_is_still_checked(self):
        source, allowed = self.source(public_copy=True)
        self.init_git(source, allowed)
        relative = RUNTIME + "/note.txt"
        path = source / relative
        path.parent.mkdir()
        path.write_text("Synthetic private runtime\n", encoding="utf-8")
        result = self.git(source, "add", "--force", "--", relative)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path.unlink()
        code, output = self.check(self.checker(source))
        self.assertEqual(code, 1, output)
        self.assertTrue(
            any(item["path"] == relative and "private" in item["reason"]
                for item in json.loads(output)["violations"]), output,
        )


class ManagedRuntimePrivacyTests(SetupRootCase):
    def test_allowlisting_runtime_paths_cannot_authorize_audit_or_export(self):
        source, _ = self.source()
        audit = self.module("release_audit")
        for index, relative in enumerate((
            RUNTIME + "/note.txt", RUNTIME + "/cookies/session.txt",
            RUNTIME + "/" + LAUNCHER, "nested/" + RUNTIME + "/note.txt",
        )):
            with self.subTest(path=relative):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Synthetic local state\n", encoding="utf-8")
                report = audit.audit(source, [relative], [relative])
                self.assertIs(report["ok"], False, report)
                self.assertTrue(any(item["path"] == relative and "private" in item["reason"]
                                    for item in report["violations"]), report)
                destination = self.root / ("unsafe export " + str(index))
                with self.assertRaises(ValueError):
                    audit.export_release(source, destination, [relative])
                self.assertFalse(destination.exists())

    def test_zip_member_cannot_launder_allowlisted_runtime(self):
        source, _ = self.source()
        audit = self.module("release_audit")
        for index, member in enumerate((
            RUNTIME + "/note.txt", "source/" + RUNTIME + "/cookies/session.txt",
            "source/" + RUNTIME + "/" + LAUNCHER,
        )):
            with self.subTest(member=member):
                archive_name = "synthetic-" + str(index) + ".zip"
                with zipfile.ZipFile(source / archive_name, "w") as archive:
                    archive.writestr(member, "Synthetic private runtime state\n")
                report = audit.audit(source, [archive_name], [archive_name, member])
                self.assertIs(report["ok"], False, report)
                self.assertTrue(any(item["path"] == archive_name + "!" + member
                                    and "private" in item["reason"] for item in report["violations"]), report)

    def test_public_manifest_includes_new_source_launcher_and_test(self):
        allowed = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
        self.assertIn(LAUNCHER, allowed)
        self.assertIn("tests/test_setup_root.py", allowed)
        self.assertFalse(any(RUNTIME in Path(name).parts for name in allowed))

    def test_bilingual_quickstarts_setup_and_enter_runtime_inside_source(self):
        for document in ("README.md", "README.vi.md"):
            with self.subTest(document=document):
                text = (RELEASE / document).read_text(encoding="utf-8")
                self.assertTrue(
                    "python -m autoapply_local --user-dir ./.autoapply-local setup" in text,
                    document + " must keep the explicit source-local setup example",
                )
                self.assertIn(LAUNCHER, text)
                for language, enter in (("powershell", "Set-Location"), ("bash", "cd")):
                    blocks = re.findall(r"```" + language + r"\n(.*?)```", text, flags=re.DOTALL)
                    quickstarts = [block for block in blocks if "-m pip install ." in block]
                    self.assertTrue(quickstarts, document + " needs a " + language + " quickstart")
                    self.assertTrue(any(
                        re.search(r"(?:^|\n)" + enter + r"\s+(?:\./)?\.autoapply-local\s*(?:\n|$)", block)
                        for block in quickstarts
                    ), document + " must enter the source-local runtime after setup")
