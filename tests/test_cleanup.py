"""Contract tests for bounded cleanup of synthetic release directories.

Giả định của người thực thi: exercise cleanup through ReleaseCase's registered
callback without prescribing the helper name; sleep uses time.sleep.
This test-only turn leaves the plan and implementation untouched.
"""

import unittest
from unittest.mock import Mock, call, patch

import test_support


DELAYS = [0.1, 0.2, 0.4, 0.8, 1.6]


def sharing_violation():
    error = PermissionError("synthetic sharing violation")
    error.winerror = 32
    return error


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.sleep_patch = patch("time.sleep")
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def temporary(self, outcomes=None):
        temporary = Mock(spec=["name", "cleanup"])
        temporary.name = "synthetic-root"
        temporary.cleanup.side_effect = outcomes
        return temporary

    def run_registered_cleanup(self, temporary):
        case = test_support.ReleaseCase()
        with (
            patch.object(test_support.tempfile, "TemporaryDirectory", return_value=temporary),
            patch.object(case, "addCleanup") as register,
        ):
            case.setUp()
        register.assert_called_once()
        args, kwargs = register.call_args
        callback, *callback_args = args
        callback(*callback_args, **kwargs)

    def test_success_on_first_call_does_not_wait(self):
        temporary = self.temporary()

        self.run_registered_cleanup(temporary)

        temporary.cleanup.assert_called_once_with()
        self.sleep.assert_not_called()

    def test_transient_sharing_violations_then_success(self):
        for failures in range(1, 6):
            with self.subTest(failures=failures):
                events = Mock()
                self.sleep.reset_mock()
                temporary = self.temporary([sharing_violation() for _ in range(failures)] + [None])
                events.attach_mock(temporary.cleanup, "cleanup")
                events.attach_mock(self.sleep, "sleep")

                try:
                    self.run_registered_cleanup(temporary)
                except PermissionError:
                    self.fail(
                        "Registered cleanup must retry transient WinError 32; "
                        f"stopped after {temporary.cleanup.call_count} cleanup calls"
                    )

                expected = []
                for delay in DELAYS[:failures]:
                    expected.extend([call.cleanup(), call.sleep(delay)])
                expected.append(call.cleanup())
                self.assertEqual(events.mock_calls, expected)

    def test_persistent_sharing_violation_raises_final_error_after_six_calls(self):
        errors = [sharing_violation() for _ in range(6)]
        temporary = self.temporary(errors)

        with self.assertRaises(PermissionError) as raised:
            self.run_registered_cleanup(temporary)

        self.assertIs(raised.exception, errors[-1])
        self.assertEqual(temporary.cleanup.call_args_list, [call()] * 6)
        self.assertEqual(self.sleep.call_args_list, [call(delay) for delay in DELAYS])

    def test_other_permission_errors_fail_immediately(self):
        for winerror in (None, 5, 33):
            with self.subTest(winerror=winerror):
                self.sleep.reset_mock()
                error = PermissionError("synthetic permission error")
                if winerror is not None:
                    error.winerror = winerror
                temporary = self.temporary(error)

                with self.assertRaises(PermissionError) as raised:
                    self.run_registered_cleanup(temporary)

                self.assertIs(raised.exception, error)
                temporary.cleanup.assert_called_once_with()
                self.sleep.assert_not_called()

    def test_other_os_errors_fail_immediately_even_with_winerror_32(self):
        for winerror in (None, 32):
            with self.subTest(winerror=winerror):
                self.sleep.reset_mock()
                error = OSError("synthetic OS error")
                if winerror is not None:
                    error.winerror = winerror
                temporary = self.temporary(error)

                with self.assertRaises(OSError) as raised:
                    self.run_registered_cleanup(temporary)

                self.assertIs(raised.exception, error)
                temporary.cleanup.assert_called_once_with()
                self.sleep.assert_not_called()

    def test_unexpected_exception_fails_immediately(self):
        error = RuntimeError("synthetic unexpected error")
        temporary = self.temporary(error)

        with self.assertRaises(RuntimeError) as raised:
            self.run_registered_cleanup(temporary)

        self.assertIs(raised.exception, error)
        temporary.cleanup.assert_called_once_with()
        self.sleep.assert_not_called()

    def test_nonretryable_error_after_sharing_violation_stops_immediately(self):
        error = PermissionError("synthetic access denied")
        error.winerror = 5
        temporary = self.temporary([sharing_violation(), error])

        with self.assertRaises(PermissionError) as raised:
            self.run_registered_cleanup(temporary)

        self.assertIs(raised.exception, error)
        self.assertEqual(temporary.cleanup.call_args_list, [call(), call()])
        self.sleep.assert_called_once_with(0.1)

    def test_release_case_registers_bounded_helper_for_its_temporary_directory(self):
        temporary = Mock(spec=["name", "cleanup"])
        temporary.name = "synthetic-root"
        case = test_support.ReleaseCase()
        with (
            patch.object(test_support.tempfile, "TemporaryDirectory", return_value=temporary),
            patch.object(case, "addCleanup") as register,
        ):
            case.setUp()

        self.assertIs(case.temporary, temporary)
        register.assert_called_once()
        args, _ = register.call_args
        self.assertIsNot(
            args[0],
            temporary.cleanup,
            "ReleaseCase must register the bounded helper instead of direct cleanup",
        )
        temporary.cleanup.assert_not_called()
        self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
