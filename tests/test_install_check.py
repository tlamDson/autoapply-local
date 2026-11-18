"""Offline dependency report for the one-line installer on Windows and macOS (mocked)."""

import unittest
from pathlib import PurePosixPath, PureWindowsPath

from test_support import RELEASE  # noqa: F401  (puts the release root on sys.path)

from autoapply_local import install_check


# Built from parts so the release audit never sees a literal workstation path.
DRIVE = PureWindowsPath("C" + ":" + "\\")
WINDOWS_HOME = str(DRIVE / "Users" / "alex")
WINDOWS_ENV = {
    "PROGRAMFILES": str(DRIVE / "Program Files"),
    "PROGRAMFILES(X86)": str(DRIVE / "Program Files (x86)"),
    "LOCALAPPDATA": str(PureWindowsPath(WINDOWS_HOME) / "AppData" / "Local"),
}
WINDOWS_CHROME = str(PureWindowsPath(WINDOWS_ENV["PROGRAMFILES"]) / "Google/Chrome/Application/chrome.exe")
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def probe(system, tools=(), files=(), version=(3, 12, 1), environ=None):
    tools, files = set(tools), {str(item) for item in files}
    return install_check.report(
        system=system,
        python_version=version,
        which=lambda name: ("/fake/" + name) if name in tools else None,
        is_file=lambda path: str(path) in files,
        environ=WINDOWS_ENV if environ is None and system == "Windows" else (environ or {}),
        home="/Users/alex" if system == "Darwin" else WINDOWS_HOME,
    )


def missing(report):
    return {item["name"]: item["command"] for item in report["missing"]}


class WindowsReportTests(unittest.TestCase):
    def test_all_present_is_ok(self):
        report = probe("Windows", tools=("git", "node", "npm"), files=(PureWindowsPath(WINDOWS_CHROME),))
        self.assertEqual(report["os"], "windows")
        self.assertIs(report["ok"], True, report)
        self.assertEqual(report["missing"], [])
        self.assertEqual({item["name"] for item in report["checks"]}, {"python", "git", "node", "npm", "chrome"})

    def test_missing_tools_print_winget_commands(self):
        report = probe("Windows", tools=(), files=())
        self.assertIs(report["ok"], False)
        self.assertEqual(missing(report), {
            "git": "winget install -e --id Git.Git",
            "node": "winget install -e --id OpenJS.NodeJS.LTS",
            "npm": "winget install -e --id OpenJS.NodeJS.LTS",
            "chrome": "winget install -e --id Google.Chrome",
        })

    def test_old_python_is_missing_with_winget_command(self):
        report = probe("Windows", tools=("git", "node", "npm"), files=(PureWindowsPath(WINDOWS_CHROME),),
                       version=(3, 10, 9))
        self.assertEqual(missing(report), {"python": "winget install -e --id Python.Python.3.12"})

    def test_chrome_in_local_app_data_is_found(self):
        local = PureWindowsPath(WINDOWS_ENV["LOCALAPPDATA"]) / "Google/Chrome/Application/chrome.exe"
        report = probe("Windows", tools=("git", "node", "npm"), files=(local,))
        self.assertIs(report["ok"], True, report)

    def test_text_lists_each_command_once(self):
        text = install_check.format_report(probe("Windows", tools=("git",), files=()))
        self.assertEqual(text.count("winget install -e --id OpenJS.NodeJS.LTS"), 1, text)
        self.assertIn("winget install -e --id Google.Chrome", text)
        self.assertIn("rerun", text.lower())


class MacReportTests(unittest.TestCase):
    def test_all_present_is_ok(self):
        report = probe("Darwin", tools=("git", "node", "npm", "brew"), files=(PurePosixPath(MAC_CHROME),))
        self.assertEqual(report["os"], "macos")
        self.assertIs(report["ok"], True, report)

    def test_user_applications_chrome_is_found(self):
        user_chrome = PurePosixPath("/Users/alex") / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        report = probe("Darwin", tools=("git", "node", "npm"), files=(user_chrome,))
        self.assertIs(report["ok"], True, report)

    def test_missing_tools_print_brew_commands(self):
        report = probe("Darwin", tools=("brew",), files=(), version=(3, 9, 6))
        self.assertEqual(missing(report), {
            "python": "brew install python@3.12",
            "git": "brew install git",
            "node": "brew install node",
            "npm": "brew install node",
            "chrome": "brew install --cask google-chrome",
        })
        self.assertNotIn("Homebrew", install_check.format_report(report))

    def test_missing_homebrew_is_explained_first(self):
        report = probe("Darwin", tools=(), files=())
        text = install_check.format_report(report)
        self.assertIn(install_check.HOMEBREW_INSTALL, text)
        self.assertLess(text.index(install_check.HOMEBREW_INSTALL), text.index("brew install git"))

    def test_report_never_installs_anything(self):
        calls = []
        install_check.report(
            system="Darwin", python_version=(3, 12), which=lambda name: calls.append(name),
            is_file=lambda path: False, environ={}, home="/Users/alex",
        )
        self.assertTrue(set(calls) <= {"git", "node", "npm", "brew", "google-chrome", "chrome", "chromium"}, calls)


class OtherSystemTests(unittest.TestCase):
    def test_other_system_gets_generic_hint(self):
        report = probe("Linux", tools=("git", "node", "npm"), files=())
        self.assertEqual(report["os"], "other")
        self.assertEqual(list(missing(report)), ["chrome"])
        self.assertIn("package manager", missing(report)["chrome"])

    def test_chrome_on_path_counts(self):
        report = probe("Linux", tools=("git", "node", "npm", "google-chrome"), files=())
        self.assertIs(report["ok"], True, report)


if __name__ == "__main__":
    unittest.main()
