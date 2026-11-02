"""UTF-8 CLI contracts using synthetic services and temporary runtime data.

Giả định của người thực thi
- Use status services and wrappers with LF newlines so byte assertions are portable.
- Import the source package under -I by inserting its absolute root into sys.path.
- Record assumptions here because this turn permits edits to test files only.
- Exercise each stream independently and use nondefault error handlers to verify preservation.

The real -I subprocess case must fail before the fix on Windows with cp1252
standard pipes. It may already pass on platforms whose default pipes use UTF-8;
it deliberately runs on every platform without skipTest.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

from autoapply_local.cli import main


UNICODE = "Thử nghiệm ữ 日本 ü"
RESULT = {"message": UNICODE}
ERROR_CASES = ((RuntimeError, 3), (ValueError, 2), (OSError, 3), (KeyError, 2))


@contextlib.contextmanager
def cp1252_streams(errors="strict"):
    with io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors=errors, newline="\n") as stdout:
        with io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors=errors, newline="\n") as stderr:
            with patch.multiple(sys, stdout=stdout, stderr=stderr):
                yield stdout, stderr


def captured_bytes(stream):
    stream.flush()
    return stream.buffer.getvalue()


class CliUnicodeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.argv = ["--user-dir", str(self.root), "status"]

    def assert_utf8_json(self, data):
        text = data.decode("utf-8", errors="strict")
        self.assertEqual(json.loads(text), RESULT)
        self.assertIn(UNICODE.encode("utf-8"), data)
        self.assertNotRegex(text, r"\\u[0-9a-fA-F]{4}")

    def assert_unused_cp1252(self, *streams):
        for stream in streams:
            self.assertEqual(stream.encoding, "cp1252")
            self.assertEqual(captured_bytes(stream), b"")

    def test_default_cp1252_stdout_emits_raw_utf8_json(self):
        service = Mock(return_value=RESULT)
        with cp1252_streams() as (stdout, stderr):
            original_errors = (stdout.errors, stderr.errors)
            code = main(self.argv, services={"status": service})
            self.assertEqual(code, 0)
            self.assert_utf8_json(captured_bytes(stdout))
            self.assertEqual(captured_bytes(stderr), b"")
            self.assertEqual((stdout.errors, stderr.errors), original_errors)
        service.assert_called_once_with(root=self.root)

    def test_default_streams_preserve_nondefault_error_handlers(self):
        for handler in ("replace", "backslashreplace"):
            with self.subTest(errors=handler):

                def service(*, root):
                    self.assertEqual(root, self.root)
                    print(UNICODE, file=sys.stderr)
                    return RESULT

                with cp1252_streams(errors=handler) as (stdout, stderr):
                    code = main(self.argv, services={"status": service})
                    self.assertEqual(code, 0)
                    self.assert_utf8_json(captured_bytes(stdout))
                    self.assertEqual(
                        captured_bytes(stderr).decode("utf-8", errors="strict"),
                        UNICODE + "\n",
                    )
                    self.assertEqual((stdout.errors, stderr.errors), (handler, handler))

    def test_default_utf8_streams_are_not_reconfigured(self):
        with io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n") as stdout:
            with io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n") as stderr:
                with patch.multiple(sys, stdout=stdout, stderr=stderr):
                    with patch.object(stdout, "reconfigure", wraps=stdout.reconfigure) as out_config:
                        with patch.object(stderr, "reconfigure", wraps=stderr.reconfigure) as err_config:
                            code = main(self.argv, services={"status": Mock(return_value=RESULT)})
                            self.assertEqual(code, 0)
                            self.assert_utf8_json(captured_bytes(stdout))
                            self.assertEqual(captured_bytes(stderr), b"")
                            out_config.assert_not_called()
                            err_config.assert_not_called()

    def test_default_streams_without_reconfigure_are_used_unchanged(self):
        output, errors = io.StringIO(), io.StringIO()

        def service(*, root):
            self.assertEqual(root, self.root)
            self.assertIs(sys.stdout, output)
            self.assertIs(sys.stderr, errors)
            print(UNICODE, file=sys.stderr)
            return RESULT

        with patch.multiple(sys, stdout=output, stderr=errors):
            code = main(self.argv, services={"status": service})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), RESULT)
        self.assertIn(UNICODE, output.getvalue())
        self.assertEqual(errors.getvalue(), UNICODE + "\n")

    def test_default_cp1252_stderr_preserves_unicode_errors_and_exit_codes(self):
        for error_type, expected_code in ERROR_CASES:
            with self.subTest(error_type=error_type.__name__):
                error = error_type(UNICODE)
                service = Mock(side_effect=error)
                with cp1252_streams() as (stdout, stderr):
                    code = main(self.argv, services={"status": service})
                    self.assertEqual(code, expected_code)
                    self.assertEqual(captured_bytes(stdout), b"")
                    self.assertEqual(
                        captured_bytes(stderr).decode("utf-8", errors="strict"),
                        str(error) + "\n",
                    )
                service.assert_called_once_with(root=self.root)

    def test_service_can_write_unicode_to_default_cp1252_stderr(self):
        def service(*, root):
            self.assertEqual(root, self.root)
            print(UNICODE, file=sys.stderr)
            return RESULT

        with cp1252_streams() as (stdout, stderr):
            code = main(self.argv, services={"status": service})
            self.assertEqual(code, 0)
            self.assert_utf8_json(captured_bytes(stdout))
            self.assertEqual(
                captured_bytes(stderr).decode("utf-8", errors="strict"),
                UNICODE + "\n",
            )

    def test_argparse_unicode_token_uses_utf8_stderr_and_exit_2(self):
        service = Mock()
        with cp1252_streams() as (stdout, stderr):
            code = main(
                ["--user-dir", str(self.root), "doctor", UNICODE],
                services={"doctor": service},
            )
            self.assertEqual(code, 2)
            self.assertEqual(captured_bytes(stdout), b"")
            self.assertIn(UNICODE, captured_bytes(stderr).decode("utf-8", errors="strict"))
        service.assert_not_called()

    def test_injected_stringio_receives_json_and_service_stderr(self):
        def service(*, root):
            self.assertEqual(root, self.root)
            print(UNICODE, file=sys.stderr)
            return RESULT

        output, errors = io.StringIO(), io.StringIO()
        with cp1252_streams() as (stdout, stderr):
            code = main(self.argv, services={"status": service}, stdout=output, stderr=errors)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue()), RESULT)
            self.assertIn(UNICODE, output.getvalue())
            self.assertEqual(errors.getvalue(), UNICODE + "\n")
            self.assert_unused_cp1252(stdout, stderr)

    def test_injected_stringio_receives_errors_without_touching_process_streams(self):
        for error_type, expected_code in ERROR_CASES:
            with self.subTest(error_type=error_type.__name__):
                error = error_type(UNICODE)
                output, errors = io.StringIO(), io.StringIO()
                with cp1252_streams() as (stdout, stderr):
                    code = main(
                        self.argv,
                        services={"status": Mock(side_effect=error)},
                        stdout=output,
                        stderr=errors,
                    )
                    self.assertEqual(code, expected_code)
                    self.assertEqual(output.getvalue(), "")
                    self.assertEqual(errors.getvalue(), str(error) + "\n")
                    self.assert_unused_cp1252(stdout, stderr)

    def test_injected_stringio_receives_argparse_unicode_error(self):
        output, errors = io.StringIO(), io.StringIO()
        service = Mock()
        with cp1252_streams() as (stdout, stderr):
            code = main(
                ["--user-dir", str(self.root), "doctor", UNICODE],
                services={"doctor": service},
                stdout=output,
                stderr=errors,
            )
            self.assertEqual(code, 2)
            self.assertEqual(output.getvalue(), "")
            self.assertIn(UNICODE, errors.getvalue())
            self.assert_unused_cp1252(stdout, stderr)
        service.assert_not_called()

    def test_injected_cp1252_streams_are_not_reconfigured(self):
        # ASCII output succeeds without changing either caller-owned wrapper.
        with cp1252_streams() as (stdout, stderr):
            code = main(
                self.argv,
                services={"status": Mock(return_value={"status": "ok"})},
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(captured_bytes(stdout)), {"status": "ok"})
            self.assertEqual(captured_bytes(stderr), b"")
            self.assertEqual((stdout.encoding, stderr.encoding), ("cp1252", "cp1252"))

    def test_only_unspecified_process_stream_is_reconfigured(self):
        for injected_name in ("stdout", "stderr"):
            with self.subTest(injected=injected_name):
                injected = io.StringIO()

                def service(*, root):
                    self.assertEqual(root, self.root)
                    if injected_name == "stderr":
                        self.assertIs(sys.stderr, injected)
                    print(UNICODE, file=sys.stderr)
                    return RESULT

                with cp1252_streams() as (stdout, stderr):
                    code = main(
                        self.argv,
                        services={"status": service},
                        **{injected_name: injected},
                    )
                    self.assertEqual(code, 0)
                    if injected_name == "stdout":
                        self.assertEqual(json.loads(injected.getvalue()), RESULT)
                        self.assertIn(UNICODE, injected.getvalue())
                        self.assert_unused_cp1252(stdout)
                        self.assertEqual(
                            captured_bytes(stderr).decode("utf-8", errors="strict"),
                            UNICODE + "\n",
                        )
                    else:
                        self.assert_utf8_json(captured_bytes(stdout))
                        self.assertEqual(injected.getvalue(), UNICODE + "\n")
                        self.assert_unused_cp1252(stderr)

    def test_isolated_python_setup_then_status_with_unicode_user_directory(self):
        """Windows cp1252 pipes need the fix; other platforms may pass already."""
        runtime_root = self.root / UNICODE
        unrelated_cwd = self.root / "unrelated-cwd"
        unrelated_cwd.mkdir()
        env = os.environ.copy()
        env.pop("PYTHONUTF8", None)
        env.pop("PYTHONIOENCODING", None)
        bootstrap = (
            "import runpy, sys; "
            f"sys.path.insert(0, {str(RELEASE)!r}); "
            "runpy.run_module('autoapply_local', run_name='__main__')"
        )
        for command in ("setup", "status"):
            with self.subTest(command=command):
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        bootstrap,
                        "--user-dir",
                        str(runtime_root),
                        command,
                    ],
                    cwd=unrelated_cwd,
                    env=env,
                    capture_output=True,
                    text=False,
                    timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                output = completed.stdout.decode("utf-8", errors="strict")
                result = json.loads(output)
                self.assertIsInstance(result, dict)
                completed.stderr.decode("utf-8", errors="strict")
                if command == "setup":
                    self.assertEqual(result["status"], "ready")
                    self.assertEqual(Path(result["intake"]), runtime_root / "user/profile-intake.md")
                    self.assertIn(UNICODE, output)
                    self.assertIn(UNICODE, result["next"])
                else:
                    self.assertEqual(result, {"held_tabs": [], "unresolved": [], "counts": {}})


if __name__ == "__main__":
    unittest.main()
