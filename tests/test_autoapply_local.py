"""Offline CLI contracts; callbacks are mocks, never installed engine CLIs.

Giả định của người thực thi: cli.main(argv, services, stdout, stderr) returns an
exit code; services maps command names to keyword-only workflow callbacks, with
root=Path from --user-dir; quick selection is an int or list of IDs.
"""

import importlib
import io
import os
import subprocess
import sys
from unittest.mock import Mock

from test_support import RELEASE, ReleaseCase


COMMANDS = (
    "setup",
    "profile_update",
    "doctor",
    "fetch",
    "apply",
    "tailor",
    "review",
    "submit",
    "mail_check",
    "status",
)


class CliTests(ReleaseCase):
    def invoke(self, *arguments):
        cli = self.module("cli")
        self.services = {name: Mock(return_value={"status": "ok"}) for name in COMMANDS}
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        return cli.main(
            ["--user-dir", str(self.root), *arguments],
            services=self.services,
            stdout=self.stdout,
            stderr=self.stderr,
        )

    def assert_only_dispatched(self, name):
        self.services[name].assert_called_once()
        self.assertEqual(self.services[name].call_args.kwargs["root"], self.root)
        for other in COMMANDS:
            if other != name:
                self.services[other].assert_not_called()

    def test_module_entrypoint_help_lists_every_product_command_without_side_effects(self):
        # Missing package is an actual import error, not a subprocess cwd error.
        importlib.import_module("autoapply_local")
        env = {**os.environ, "PYTHONPATH": str(RELEASE), "AUTOAPPLY_LOCAL_HOME": str(self.root)}
        before = list(self.root.iterdir())
        completed = subprocess.run(
            [sys.executable, "-m", "autoapply_local", "--help"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for command in (
            "setup",
            "profile",
            "doctor",
            "fetch",
            "apply",
            "tailor",
            "review",
            "submit",
            "mail-check",
            "status",
        ):
            with self.subTest(command=command):
                self.assertIn(command, completed.stdout)
        self.assertEqual(list(self.root.iterdir()), before)

    def test_setup_and_status_execute_the_requested_service(self):
        for command in ("setup", "status"):
            with self.subTest(command=command):
                self.assertEqual(self.invoke(command), 0)
                self.assert_only_dispatched(command)

    def test_profile_update_previews_by_default_and_forwards_explicit_confirm(self):
        path = self.root / "edited-intake.md"
        path.write_text("Synthetic editable intake", encoding="utf-8")
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed):
                args = ["profile", "update", str(path)] + (["--confirm"] if confirmed else [])
                self.assertEqual(self.invoke(*args), 0)
                self.assert_only_dispatched("profile_update")
                kwargs = self.services["profile_update"].call_args.kwargs
                self.assertEqual(kwargs["path"], path)
                self.assertIs(kwargs["confirm"], confirmed)

    def test_doctor_and_fetch_forward_engine_override_to_one_service(self):
        for command in ("doctor", "fetch"):
            for engine in ("codex", "claude"):
                with self.subTest(command=command, engine=engine):
                    self.assertEqual(self.invoke(command, engine), 0)
                    self.assert_only_dispatched(command)
                    self.assertEqual(self.services[command].call_args.kwargs["engine"], engine)

    def test_omitted_engine_allows_core_to_resolve_configured_default(self):
        self.assertEqual(self.invoke("fetch"), 0)
        self.assert_only_dispatched("fetch")
        self.assertIsNone(self.services["fetch"].call_args.kwargs["engine"])

    def test_quick_count_and_ids_dispatch_apply_without_tailor_review_or_submit(self):
        for selection, expected in (("2", 2), ("synthetic-1,synthetic-2", ["synthetic-1", "synthetic-2"])):
            with self.subTest(selection=selection):
                self.assertEqual(self.invoke("apply", "codex", "--quick", selection), 0)
                self.assert_only_dispatched("apply")
                kwargs = self.services["apply"].call_args.kwargs
                self.assertEqual(kwargs["selection"], expected)
                self.assertEqual(kwargs["engine"], "codex")

    def test_tailor_accepts_file_or_url_and_dispatches_only_tailor(self):
        path = self.root / "jd.txt"
        path.write_text("Example Labs backend internship", encoding="utf-8")
        for source in (str(path), "https://jobs.example.invalid/synthetic-1"):
            with self.subTest(source=source):
                self.assertEqual(self.invoke("tailor", "codex", source), 0)
                self.assert_only_dispatched("tailor")
                self.assertEqual(str(self.services["tailor"].call_args.kwargs["jd"]), source)

    def test_review_all_executes_review_without_submit(self):
        self.assertEqual(self.invoke("review", "all"), 0)
        self.assert_only_dispatched("review")
        self.assertEqual(self.services["review"].call_args.kwargs["selection"], "all")

    def test_submit_requires_explicit_ids_or_all_reviewed(self):
        for selection, expected in (
            ("synthetic-1,synthetic-2", ["synthetic-1", "synthetic-2"]),
            ("all-reviewed", "all-reviewed"),
        ):
            with self.subTest(selection=selection):
                self.assertEqual(self.invoke("submit", selection), 0)
                self.assert_only_dispatched("submit")
                self.assertEqual(self.services["submit"].call_args.kwargs["selection"], expected)

    def test_mail_check_executes_mail_service_only(self):
        self.assertEqual(self.invoke("mail-check"), 0)
        self.assert_only_dispatched("mail_check")

    def test_invalid_commands_do_not_dispatch_any_service(self):
        for arguments in (
            ("apply", "--quick", "0"),
            ("apply", "--quick", "-1"),
            ("apply",),
            ("submit",),
            ("submit", "all"),
            ("fetch", "unknown-engine"),
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.invoke(*arguments), 2)
                for service in self.services.values():
                    service.assert_not_called()

    def test_source_extract_dispatches_draft_extraction_only(self):
        source = self.root / "synthetic-source.tex"
        source.write_text("Synthetic checked source text", encoding="utf-8")
        callbacks = {"source_extract": Mock(return_value={"status": "draft"}), "history_import": Mock()}
        result = self.module("cli").main(
            ["--user-dir", str(self.root), "source", "extract", str(source)],
            services=callbacks,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        self.assertEqual(result, 0)
        callbacks["source_extract"].assert_called_once_with(root=self.root, path=source)
        callbacks["history_import"].assert_not_called()

    def test_history_import_dispatches_preview_then_explicit_confirmation(self):
        source = self.root / "history.csv"
        source.write_text("company,role,url,status\n", encoding="utf-8")
        for confirm in (False, True):
            with self.subTest(confirm=confirm):
                callback = Mock(return_value={"applied": confirm})
                arguments = ["--user-dir", str(self.root), "history", "import", str(source)]
                if confirm:
                    arguments.append("--confirm")
                result = self.module("cli").main(
                    arguments,
                    services={"history_import": callback},
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
                self.assertEqual(result, 0)
                callback.assert_called_once_with(root=self.root, path=source, confirm=confirm)
