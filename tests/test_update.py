"""Offline tests for the `update` command (backup, pull or zip, reinstall, migrations, notice)."""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from autoapply_local import cli, update  # noqa: E402

NOW = datetime(2026, 11, 17, 9, 30, 0)


def snapshot(directory, skip=("backups", "migrations.json")):
    directory = Path(directory)
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.relative_to(directory).parts[0] not in skip
    }


def make_zip(members, top="autoapply-local-0.3.0"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in members.items():
            info = zipfile.ZipInfo()
            info.filename = f"{top}/{name}" if top else name
            archive.writestr(info, text)
    return buffer.getvalue()


class Workspace(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.repo = self.temp / "repo"
        (self.repo / "autoapply_local").mkdir(parents=True)
        (self.repo / "autoapply_local/__init__.py").write_text('__version__ = "0.2.0a1"\n', encoding="utf-8")
        (self.repo / "pyproject.toml").write_text('[project]\nname = "autoapply-local"\n', encoding="utf-8")
        (self.repo / "export-allowlist.json").write_text("[]", encoding="utf-8")
        self.user = self.repo / "user"
        (self.user / "resumes").mkdir(parents=True)
        (self.user / "config.json").write_text('{"jobs_per_run": 10}', encoding="utf-8")
        (self.user / "secrets.json").write_text('{"workday": {"email": "alex.rivera@example.test"}}', encoding="utf-8")
        (self.user / "resumes/swe.pdf").write_bytes(b"%PDF-1.4 synthetic")
        (self.user / "chrome/Default").mkdir(parents=True)
        (self.user / "chrome/Default/Cookies").write_bytes(b"locked")


class BackupTests(Workspace):
    def test_backup_copies_user_data_and_keeps_earlier_backups(self):
        old = self.user / "backups/2026-01-01-000000"
        old.mkdir(parents=True)
        (old / "config.json").write_text("old", encoding="utf-8")
        before = snapshot(self.user)

        target = update.backup_user(self.user, now=NOW)

        self.assertEqual(target, self.user / "backups/2026-11-17-093000")
        copied = snapshot(target)
        self.assertEqual(copied["config.json"], before["config.json"])
        self.assertEqual(copied["secrets.json"], before["secrets.json"])
        self.assertEqual(copied["resumes/swe.pdf"], before["resumes/swe.pdf"])
        self.assertNotIn("chrome/Default/Cookies", copied)
        self.assertFalse((target / "backups").exists())
        self.assertEqual((old / "config.json").read_text(encoding="utf-8"), "old")
        self.assertEqual(snapshot(self.user), before)

    def test_second_backup_in_same_second_gets_a_new_folder(self):
        first = update.backup_user(self.user, now=NOW)
        second = update.backup_user(self.user, now=NOW)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir() and second.is_dir())

    def test_missing_user_dir_is_rejected(self):
        with self.assertRaises(RuntimeError):
            update.backup_user(self.temp / "absent", now=NOW)


class FakeRunner:
    def __init__(self, heads=("aaa", "bbb"), pull_code=0, pip_code=0, log="bbb Fix a thing\n"):
        self.calls = []
        self.heads = list(heads)
        self.pull_code, self.pip_code, self.log = pull_code, pip_code, log

    def __call__(self, argv, cwd=None, env=None):
        self.calls.append((list(argv), env))
        if argv[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout=self.heads.pop(0) + "\n", stderr="")
        if argv[:2] == ["git", "pull"]:
            return SimpleNamespace(returncode=self.pull_code, stdout="", stderr="not fast-forward")
        if argv[:2] == ["git", "log"]:
            return SimpleNamespace(returncode=0, stdout=self.log, stderr="")
        if "pip" in argv:
            return SimpleNamespace(returncode=self.pip_code, stdout="", stderr="")
        raise AssertionError(argv)

    def commands(self):
        return [argv for argv, _ in self.calls]


class GitUpdateTests(Workspace):
    def setUp(self):
        super().setUp()
        (self.repo / ".git").mkdir()

    def run_update(self, runner, **extra):
        options = dict(root=self.user, repo=self.repo, run=runner, which=lambda name: "/usr/bin/git",
                       migrations=[], now=NOW, download=Mock(side_effect=AssertionError("no zip with git")))
        options.update(extra)
        return update.update(**options)

    def test_git_update_backs_up_pulls_ff_only_reinstalls_and_reports_changes(self):
        before = snapshot(self.user)
        runner = FakeRunner()
        result = self.run_update(runner)

        commands = runner.commands()
        self.assertIn(["git", "pull", "--ff-only"], commands)
        self.assertEqual(commands[-1][1:], ["-m", "pip", "install", "-e", str(self.repo)])
        self.assertLess(commands.index(["git", "pull", "--ff-only"]), len(commands) - 1)
        self.assertEqual(runner.calls[-1][1]["PIP_USER"], "0")
        self.assertEqual(result["method"], "git")
        self.assertEqual(result["changes"], ["bbb Fix a thing"])
        self.assertTrue(Path(result["backup"]).is_dir())
        self.assertEqual(snapshot(self.user), before)

    def test_up_to_date_reports_no_changes(self):
        result = self.run_update(FakeRunner(heads=("aaa", "aaa")))
        self.assertEqual(result["changes"], [])

    def test_failed_pull_keeps_backup_and_skips_reinstall(self):
        runner = FakeRunner(pull_code=1)
        with self.assertRaises(RuntimeError) as caught:
            self.run_update(runner)
        self.assertIn("ff-only", str(caught.exception))
        self.assertFalse(any("pip" in argv for argv in runner.commands()))
        self.assertTrue((self.user / "backups/2026-11-17-093000/config.json").is_file())

    def test_failed_reinstall_is_an_error(self):
        with self.assertRaises(RuntimeError):
            self.run_update(FakeRunner(pip_code=1))

    def test_non_source_install_is_rejected(self):
        with self.assertRaises(RuntimeError):
            update.update(root=self.user, repo=self.temp, run=FakeRunner(), migrations=[], now=NOW)


class ZipUpdateTests(Workspace):
    def run_update(self, data, **extra):
        runner = FakeRunner()
        latest = {"tag": "v0.3.0", "zip": "https://example.test/v0.3.0.zip"}
        download = Mock(return_value=data)
        options = dict(root=self.user, repo=self.repo, run=runner, which=lambda name: None, migrations=[],
                       now=NOW, download=download, latest=lambda: latest)
        options.update(extra)
        return update.update(**options), runner, download

    def test_without_git_the_release_zip_replaces_source_but_not_user_data(self):
        before = snapshot(self.user)
        data = make_zip({
            "autoapply_local/__init__.py": '__version__ = "0.3.0"\n',
            "autoapply_local/new_module.py": "VALUE = 1\n",
            "user/config.json": '{"overwritten": true}',
            ".venv/pyvenv.cfg": "x",
        })
        result, runner, download = self.run_update(data)

        download.assert_called_once_with("https://example.test/v0.3.0.zip")
        self.assertEqual(result["method"], "zip")
        self.assertEqual(result["changes"], ["0.2.0a1 -> 0.3.0"])
        self.assertIn("0.3.0", (self.repo / "autoapply_local/__init__.py").read_text(encoding="utf-8"))
        self.assertTrue((self.repo / "autoapply_local/new_module.py").is_file())
        self.assertFalse((self.repo / ".venv").exists())
        self.assertEqual(snapshot(self.user), before)
        self.assertTrue(any("pip" in argv for argv in runner.commands()))

    def test_zip_member_escaping_the_repo_is_rejected_before_writing(self):
        data = make_zip({"autoapply_local/__init__.py": "changed", "../escape.txt": "bad"})
        with self.assertRaises(ValueError):
            self.run_update(data)
        self.assertFalse((self.temp / "escape.txt").exists())
        self.assertIn("0.2.0a1", (self.repo / "autoapply_local/__init__.py").read_text(encoding="utf-8"))

    def test_no_release_found_is_an_error(self):
        with self.assertRaises(RuntimeError):
            self.run_update(b"", latest=lambda: None)


class MigrationTests(Workspace):
    def test_each_migration_runs_once(self):
        first = Mock()
        second = Mock()
        migrations = [("0001-first", first), ("0002-second", second)]

        self.assertEqual(update.run_migrations(self.user, migrations), ["0001-first", "0002-second"])
        self.assertEqual(update.run_migrations(self.user, migrations), [])

        first.assert_called_once_with(self.user)
        second.assert_called_once_with(self.user)
        state = json.loads((self.user / "migrations.json").read_text(encoding="utf-8"))
        self.assertEqual(state["applied"], ["0001-first", "0002-second"])

    def test_failed_migration_is_not_recorded_and_retries_next_time(self):
        broken = Mock(side_effect=[OSError("disk"), None])
        migrations = [("0001-broken", broken)]
        with self.assertRaises(OSError):
            update.run_migrations(self.user, migrations)
        self.assertEqual(update.run_migrations(self.user, migrations), ["0001-broken"])
        self.assertEqual(broken.call_count, 2)

    def test_update_runs_migrations_after_reinstall(self):
        (self.repo / ".git").mkdir()
        order = []
        runner = FakeRunner()
        original = runner.__call__

        def recording(argv, cwd=None, env=None):
            order.append("pip" if "pip" in argv else argv[1])
            return original(argv, cwd=cwd, env=env)

        migration = Mock(side_effect=lambda root: order.append("migrate"))
        result = update.update(root=self.user, repo=self.repo, run=recording, which=lambda name: "git",
                               migrations=[("0001-x", migration)], now=NOW)
        self.assertEqual(order[-2:], ["pip", "migrate"])
        self.assertEqual(result["migrations"], ["0001-x"])


class NoticeTests(unittest.TestCase):
    def test_versions_compare_with_prereleases(self):
        self.assertTrue(update.is_newer("v0.2.0", "0.2.0a1"))
        self.assertTrue(update.is_newer("0.2.0a2", "0.2.0a1"))
        self.assertTrue(update.is_newer("0.10.0", "0.9.1"))
        self.assertFalse(update.is_newer("v0.2.0a1", "0.2.0a1"))
        self.assertFalse(update.is_newer("0.1.0", "0.2.0a1"))
        self.assertFalse(update.is_newer("nightly", "0.2.0a1"))

    def test_notice_is_one_line_only_when_newer(self):
        line = update.release_notice(current="0.2.0a1", latest=lambda: {"tag": "v0.3.0", "zip": "z"})
        self.assertIsInstance(line, str)
        self.assertNotIn("\n", line)
        self.assertIn("v0.3.0", line)
        self.assertIn("update", line)
        self.assertIsNone(update.release_notice(current="0.3.0", latest=lambda: {"tag": "v0.3.0", "zip": "z"}))
        self.assertIsNone(update.release_notice(current="0.3.0", latest=lambda: None))

    def test_notice_never_raises_on_network_error(self):
        self.assertIsNone(update.release_notice(current="0.2.0a1", latest=Mock(side_effect=OSError("offline"))))

    def test_latest_release_parses_github_payload(self):
        payload = json.dumps({"tag_name": "v0.3.0", "draft": False}).encode()
        found = update.latest_release(fetch=lambda url: payload)
        self.assertEqual(found["tag"], "v0.3.0")
        self.assertTrue(found["zip"].endswith("/v0.3.0.zip"))


class CliTests(unittest.TestCase):
    def invoke(self, argv, services):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli.main(["--user-dir", "u", *argv], services=services, stdout=stdout, stderr=stderr)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_update_dispatches_with_root(self):
        service = Mock(return_value={"method": "git"})
        code, stdout, _ = self.invoke(["update"], {"update": service})
        self.assertEqual(code, 0)
        self.assertEqual(service.call_args.kwargs["root"], Path("u").resolve())
        self.assertIn('"git"', stdout)

    def test_apply_prints_newer_release_line_on_stderr(self):
        services = {"apply": Mock(return_value={"ok": True}),
                    "release_notice": Mock(return_value="AutoApply Local v9.0.0 is available; run update.")}
        code, stdout, stderr = self.invoke(["apply", "--quick", "1"], services)
        self.assertEqual(code, 0)
        self.assertEqual(stderr.strip(), "AutoApply Local v9.0.0 is available; run update.")
        self.assertNotIn("v9.0.0", stdout)

    def test_apply_without_notice_service_prints_nothing_extra(self):
        code, _, stderr = self.invoke(["apply", "--quick", "1"], {"apply": Mock(return_value={})})
        self.assertEqual((code, stderr), (0, ""))

    def test_runtime_services_expose_update(self):
        from autoapply_local import runtime

        self.assertIn("update", runtime.services())
        self.assertIn("release_notice", runtime.services())


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class RealGitTests(Workspace):
    def git(self, cwd, *args):
        return subprocess.run(["git", "-c", "user.name=Alex Rivera", "-c", "user.email=alex@example.test",
                               "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True,
                              capture_output=True, text=True).stdout

    def test_real_fast_forward_pull_leaves_user_untouched(self):
        origin = self.temp / "origin"
        shutil.copytree(self.repo, origin, ignore=shutil.ignore_patterns("user"))
        (origin / ".gitignore").write_text("user/\n", encoding="utf-8")
        self.git(origin, "init", "-q", "-b", "main")
        self.git(origin, "add", ".")
        self.git(origin, "commit", "-q", "-m", "first")
        clone = self.temp / "clone"
        self.git(self.temp, "clone", "-q", str(origin), str(clone))
        shutil.copytree(self.user, clone / "user")
        before = snapshot(clone / "user")
        (origin / "NEW.md").write_text("new", encoding="utf-8")
        self.git(origin, "add", "NEW.md")
        self.git(origin, "commit", "-q", "-m", "Add new file")

        def run(argv, cwd=None, env=None):
            if "pip" in argv:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)

        result = update.update(root=clone / "user", repo=clone, run=run, migrations=[], now=NOW)
        self.assertEqual(result["method"], "git")
        self.assertTrue((clone / "NEW.md").is_file())
        self.assertEqual(len(result["changes"]), 1)
        self.assertIn("Add new file", result["changes"][0])
        self.assertEqual(snapshot(clone / "user"), before)
        self.assertEqual(self.git(clone, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
