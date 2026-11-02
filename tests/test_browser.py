"""Offline launcher and MCP contracts; never spawn Chrome or inspect a user profile.

Giả định của người thực thi: browser provides discover_chrome(override=None),
launch_chrome(root, executable, lanes=1, opt_in=False, available_memory_mb=8192,
held_tabs=(), held_tab_cap=5, spawn=...), and mcp_config(engine, debug_url,
existing=None). Launch returns lane records with profile_dir/debug_url/command.
The cap and zero-memory rejection deny new work with ValueError, preserving tabs.
"""

import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch

from test_support import ReleaseCase


class BrowserTests(ReleaseCase):
    def launch(self, **options):
        self.spawn = Mock(return_value=Mock(pid=1234))
        return self.module("browser").launch_chrome(
            self.root,
            executable=self.root / "synthetic-chrome",
            spawn=self.spawn,
            **options,
        )

    def test_windows_discovery_uses_installed_chrome_path(self):
        browser = self.module("browser")
        program_files = str(self.root / "Program Files")
        expected = Path(program_files) / "Google/Chrome/Application/chrome.exe"
        with (
            patch("platform.system", return_value="Windows"),
            patch.dict("os.environ", {"PROGRAMFILES": program_files}, clear=True),
            patch("shutil.which", return_value=None),
            patch.object(Path, "is_file", autospec=True, side_effect=lambda path: path == expected),
            patch.object(Path, "exists", autospec=True, side_effect=lambda path: path == expected),
        ):
            self.assertEqual(Path(browser.discover_chrome()), expected)

    def test_macos_discovery_uses_application_bundle_executable(self):
        browser = self.module("browser")
        expected = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        with (
            patch("platform.system", return_value="Darwin"),
            patch("shutil.which", return_value=None),
            patch.object(Path, "is_file", autospec=True, side_effect=lambda path: path == expected),
            patch.object(Path, "exists", autospec=True, side_effect=lambda path: path == expected),
        ):
            self.assertEqual(Path(browser.discover_chrome()), expected)

    def test_explicit_chrome_override_is_validated(self):
        browser = self.module("browser")
        executable = self.root / "chosen-browser"
        executable.touch()
        self.assertEqual(Path(browser.discover_chrome(override=executable)), executable)
        with self.assertRaises(FileNotFoundError):
            browser.discover_chrome(override=self.root / "missing-browser")

    def test_default_launch_opens_one_lane_on_loopback_with_clean_profile(self):
        lanes = self.launch()
        self.assertEqual(len(lanes), 1)
        self.spawn.assert_called_once()
        lane = lanes[0]
        self.assertTrue(Path(lane["profile_dir"]).resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(lane["debug_url"].startswith("http://127.0.0.1:"))
        command = [str(value) for value in self.spawn.call_args.args[0]]
        self.assertEqual(command[0], str(self.root / "synthetic-chrome"))
        self.assertIn("--remote-debugging-address=127.0.0.1", command)
        self.assertIn(f"--user-data-dir={lane['profile_dir']}", command)
        self.assertFalse(self.spawn.call_args.kwargs.get("shell", False))

    def test_opted_in_three_lanes_have_distinct_profiles_and_debug_ports(self):
        lanes = self.launch(lanes=3, opt_in=True, available_memory_mb=65536)
        self.assertEqual(len(lanes), 3)
        self.assertEqual(self.spawn.call_count, 3)
        self.assertEqual(len({str(item["profile_dir"]) for item in lanes}), 3)
        self.assertEqual(len({item["debug_url"] for item in lanes}), 3)
        self.assertTrue(all(item["debug_url"].startswith("http://127.0.0.1:") for item in lanes))

    def test_multiple_lanes_require_explicit_opt_in(self):
        with self.assertRaises(ValueError):
            self.launch(lanes=2, opt_in=False, available_memory_mb=65536)
        self.spawn.assert_not_called()

    def test_lane_count_outside_one_to_three_is_rejected_before_launch(self):
        for count in (0, 4, -1):
            with self.subTest(lanes=count), self.assertRaises(ValueError):
                self.launch(lanes=count, opt_in=True, available_memory_mb=65536)
            self.spawn.assert_not_called()

    def test_memory_denial_preserves_all_held_tabs_and_does_not_launch(self):
        held = [{"tab_id": "tab-1", "job_id": "synthetic-1", "state": "filled"}]
        original = copy.deepcopy(held)
        with self.assertRaises(ValueError):
            self.launch(available_memory_mb=0, held_tabs=held)
        self.assertEqual(held, original)
        self.spawn.assert_not_called()

    def test_held_tab_cap_blocks_new_work_without_discarding_filled_tabs(self):
        held = [{"tab_id": "tab-1", "job_id": "synthetic-1", "state": "filled"}]
        original = copy.deepcopy(held)
        with self.assertRaises(ValueError):
            self.launch(held_tabs=held, held_tab_cap=1)
        self.assertEqual(held, original)
        self.spawn.assert_not_called()

    def test_launcher_does_not_copy_the_users_existing_profile(self):
        browser = self.module("browser")
        spawn = Mock(return_value=Mock(pid=1234))
        with patch("shutil.copytree", side_effect=AssertionError("User profile must not be cloned")):
            lanes = browser.launch_chrome(self.root, executable="synthetic-chrome", spawn=spawn)
        self.assertEqual(len(lanes), 1)
        self.assertNotIn("--profile-directory=Default", [str(value) for value in lanes[0]["command"]])

    def test_both_engine_configs_preserve_existing_servers_and_pin_mcp_version(self):
        browser = self.module("browser")
        for engine, servers_key in (("claude", "mcpServers"), ("codex", "mcp_servers")):
            with self.subTest(engine=engine):
                original = {servers_key: {"existing-server": {"command": "synthetic-existing-tool"}}}
                before = copy.deepcopy(original)
                result = browser.mcp_config(engine, "http://127.0.0.1:9222", existing=original)
                self.assertEqual(original, before)
                self.assertEqual(result[servers_key]["existing-server"], original[servers_key]["existing-server"])
                additions = {key: value for key, value in result[servers_key].items() if key != "existing-server"}
                self.assertEqual(len(additions), 1)
                serialized = json.dumps(additions)
                self.assertRegex(serialized, r"chrome-devtools-mcp@\d+\.\d+\.\d+")
                self.assertNotIn("@latest", serialized)
                self.assertNotIn("bypassPermissions", serialized)
                self.assertNotIn("dangerously-skip-permissions", serialized)
                self.assertNotIn("dangerously-bypass-approvals", serialized)
                self.assertIn("http://127.0.0.1:9222", serialized)

    def test_mcp_config_rejects_remote_debug_endpoints(self):
        browser = self.module("browser")
        for url in ("http://0.0.0.0:9222", "http://198.51.100.5:9222", "https://browser.example.invalid"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                browser.mcp_config("codex", url)

    def test_mcp_config_never_overwrites_existing_same_named_server(self):
        browser = self.module("browser")
        generated = browser.mcp_config("codex", "http://127.0.0.1:9222")
        key = next(iter(generated["mcp_servers"]))
        existing = {"mcp_servers": {key: {"command": "user-owned-server"}}}
        before = copy.deepcopy(existing)
        try:
            result = browser.mcp_config("codex", "http://127.0.0.1:9222", existing=existing)
        except ValueError:
            pass  # Reporting a conflict is also a safe, explicit outcome.
        else:
            self.assertEqual(result["mcp_servers"][key], before["mcp_servers"][key])
        self.assertEqual(existing, before)
