"""Process liveness must never signal a process: on Windows os.kill terminates it.

Offline: the live pid is this test's own process; the dead pid is a child that
already exited. No Chrome is started and no network call leaves the machine.
"""

import io
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from test_support import ReleaseCase


def _dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


class ProcessAliveTests(ReleaseCase):
    def alive(self):
        return self.module("process_alive").pid_alive

    def test_own_pid_is_alive(self):
        self.assertTrue(self.alive()(os.getpid()))

    def test_exited_child_pid_is_not_alive(self):
        self.assertFalse(self.alive()(_dead_pid()))

    def test_unknown_pid_values_are_not_alive_and_never_raise(self):
        for value in (None, 0, -5, "", "abc", 2**40, True, 3.5):
            self.assertFalse(self.alive()(value), value)

    def test_numeric_string_pid_is_accepted(self):
        self.assertTrue(self.alive()(str(os.getpid())))

    def test_windows_check_never_calls_os_kill(self):
        if os.name != "nt":
            self.skipTest("ctypes.windll exists only on Windows; POSIX path tested below")
        with patch("os.kill", side_effect=AssertionError("os.kill on Windows")) as kill:
            self.assertTrue(self.alive()(os.getpid()))
            self.assertFalse(self.alive()(_dead_pid()))
        kill.assert_not_called()

    def test_windows_platform_never_calls_os_kill_even_when_query_fails(self):
        module = self.module("process_alive")
        with patch.object(module.sys, "platform", "win32"), patch.object(
            module, "_windows_alive", return_value=False
        ) as query, patch("os.kill", side_effect=AssertionError("os.kill on Windows")) as kill:
            self.assertFalse(module.pid_alive(4321))
        query.assert_called_once_with(4321)
        kill.assert_not_called()

    def test_posix_platform_uses_signal_zero(self):
        module = self.module("process_alive")
        with patch.object(module.sys, "platform", "linux"), patch("os.kill") as kill:
            self.assertTrue(module.pid_alive(4321))
        kill.assert_called_once_with(4321, 0)

    def test_posix_platform_treats_errors_as_not_alive(self):
        module = self.module("process_alive")
        for error in (ProcessLookupError(), PermissionError(), OSError(), SystemError(), OverflowError()):
            with patch.object(module.sys, "platform", "linux"), patch("os.kill", side_effect=error):
                self.assertFalse(module.pid_alive(4321), error)


class BrowserLaneReuseTests(ReleaseCase):
    def owned_busy_lane(self, pid):
        executable = self.root / "synthetic-chrome"
        profile = self.root / "chrome" / "lane-1"
        profile.mkdir(parents=True)
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        self.addCleanup(busy.close)
        port = busy.getsockname()[1]
        (profile / "autoapply-owner.json").write_text(
            json.dumps({"root": str(self.root), "executable": str(executable), "port": port, "pid": pid}),
            encoding="utf-8",
        )
        return executable

    def launch(self, executable):
        # Run the Windows branch on every OS: the kernel query is stubbed so
        # that only this test's own pid is running, and os.kill is forbidden.
        browser = self.module("browser")
        process_alive = self.module("process_alive")
        version = io.BytesIO(json.dumps({"Browser": "Chrome/130.0"}).encode())
        with patch("os.kill", side_effect=AssertionError("os.kill must not probe the lane Chrome")) as kill, patch.object(
            browser, "urlopen", return_value=version
        ), patch.object(process_alive.sys, "platform", "win32"), patch.object(
            process_alive, "_windows_alive", side_effect=lambda pid: pid == os.getpid()
        ):
            try:
                return browser.launch_chrome(self.root, executable=executable, spawn=None), kill
            except ValueError as error:
                return error, kill

    def test_live_owned_lane_is_reused_without_signalling_it(self):
        lanes, kill = self.launch(self.owned_busy_lane(os.getpid()))
        kill.assert_not_called()
        self.assertIsInstance(lanes, list)
        self.assertEqual(lanes[0]["pid"], os.getpid())

    def test_dead_owner_requires_reconciliation_without_signalling(self):
        error, kill = self.launch(self.owned_busy_lane(_dead_pid()))
        kill.assert_not_called()
        self.assertIsInstance(error, ValueError)
        self.assertIn("manual reconciliation", str(error))

    def test_package_never_probes_with_os_kill_outside_process_alive(self):
        package = Path(self.module("browser").__file__).parent
        offenders = [
            path.name
            for path in package.rglob("*.py")
            if path.name != "process_alive.py" and "os.kill(" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])
