"""Offline regression contracts for install-report JSON stream encoding.

Giả định của người thực thi:
- Reuse synthetic archive helpers by module import; fake venv/subprocess only,
  fail pip check with exit 7, and use UTF-8 stdout for the file-format control.
- Create both platform launcher layouts as in run_installer; verify main keeps
  the caller's stdout object as well as its strict encoding settings.
- Record assumptions here and leave the plan/allowlist/RED log untouched because
  this turn permits changes to the new test file only.
"""

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import test_package_current


class InstallStreamEncodingTests(test_package_current.CurrentPackageCase):
    def setUp(self):
        super().setUp()
        self.valid_set()
        self.installer = self.load_script("check_install")

    def run_report(self, encoding, *, fail=False):
        work = self.root / f"install report thử ünicode {encoding} {fail}"
        self.assertFalse(work.resolve().is_relative_to(self.source.resolve()))
        calls = []

        def run(argv, **kwargs):
            calls.append(list(argv))
            code = 7 if fail and argv[1:4] == ["-m", "pip", "check"] else 0
            return subprocess.CompletedProcess(
                argv, code, stdout=json.dumps({"version": self.version}) + "\n", stderr=""
            )

        # No executable is invoked: both venv creation and every subprocess are
        # replaced, while source/artifact auditing and main.print remain real.
        def create(path):
            for directory, python, console in (
                ("Scripts", "python.exe", "autoapply-local.exe"),
                ("bin", "python", "autoapply-local"),
            ):
                scripts = Path(path) / directory
                scripts.mkdir(parents=True, exist_ok=True)
                (scripts / python).touch()
                (scripts / console).touch()

        builder = Mock()
        builder.create.side_effect = create
        raw = io.BytesIO()
        settings = ("PYTHONUTF8", "PYTHONIOENCODING", "LANG", "LC_ALL")
        environment = {key: os.environ.get(key) for key in settings}
        with io.TextIOWrapper(raw, encoding=encoding, errors="strict") as output:
            with (
                patch.object(self.installer, "ROOT", self.source),
                patch.object(self.installer.venv, "EnvBuilder", return_value=builder),
                patch.object(self.installer.subprocess, "run", side_effect=run),
                redirect_stdout(output),
            ):
                try:
                    code = self.installer.main(["--artifacts", str(self.artifacts), "--work-dir", str(work)])
                except UnicodeEncodeError:
                    self.fail(
                        "Valid install-report JSON could not be emitted to strict "
                        f"{encoding} stdout (synthetic subprocess failure={fail}); "
                        "Unicode paths must survive JSON reporting."
                    )
                self.assertIs(sys.stdout, output)
                self.assertEqual(output.encoding, encoding)
                self.assertEqual(output.errors, "strict")
                self.assertFalse(output.closed)
            output.flush()
            payload = raw.getvalue()

        self.assertEqual({key: os.environ.get(key) for key in settings}, environment)
        report = json.loads(payload.decode(encoding))
        self.assertEqual(code, 1 if fail else 0)
        self.assertIs(report["ok"], not fail)
        self.assertEqual([command["argv"] for command in report["commands"]], calls)
        self.assertTrue(calls, "Valid synthetic artifacts must reach fake subprocesses")
        for arguments in calls:
            self.assertTrue(Path(arguments[0]).is_relative_to(work.resolve()))
            self.assertIn("thử ünicode", arguments[0])
        artifact = str((self.artifacts / self.filename("wheel")).resolve())
        self.assertIn(artifact, calls[0])
        for kind in ("wheel",) if fail else ("wheel", "sdist"):
            interpreter = Path(calls[0][0]).name
            scripts = "Scripts" if os.name == "nt" else "bin"
            expected = str(work.resolve() / f"{kind} venv thử ünicode" / scripts / interpreter)
            self.assertIn(expected, [arguments[0] for arguments in calls])
        self.assertTrue(all(command["exit_code"] == 0 for command in report["commands"][:-1]))
        if fail:
            self.assertEqual(report["error"], "RuntimeError")
            self.assertEqual(report["commands"][-1]["exit_code"], 7)
            self.assertEqual(calls[-1][1:4], ["-m", "pip", "check"])
            self.assertEqual(len(calls), 2, "Commands must stop after the injected failure")
            self.assertEqual(report["versions"], {})
        else:
            self.assertNotIn("error", report)
            self.assertTrue(all(command["exit_code"] == 0 for command in report["commands"]))
            self.assertEqual(report["versions"], {"wheel": self.version, "sdist": self.version})

        file_bytes = (work / "install-report.json").read_bytes()
        self.assertIn("thử ünicode".encode("utf-8"), file_bytes)
        self.assertEqual(json.loads(file_bytes.decode("utf-8")), report)
        return payload

    def test_successful_report_round_trips_unicode_on_strict_legacy_stdout(self):
        for encoding in ("cp1252", "ascii"):
            with self.subTest(encoding=encoding):
                payload = self.run_report(encoding)
                payload.decode("ascii")

    def test_failed_command_remains_failure_on_strict_legacy_stdout(self):
        for encoding in ("cp1252", "ascii"):
            with self.subTest(encoding=encoding):
                payload = self.run_report(encoding, fail=True)
                payload.decode("ascii")

    def test_persistent_report_stays_utf8_and_matches_stdout_json(self):
        self.run_report("utf-8")
