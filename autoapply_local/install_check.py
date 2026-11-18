"""Installer dependency report: what is missing and the exact command to install it.

The report only looks; it never installs system software. Every system boundary
(platform, PATH lookup, file checks, environment) is a parameter so both OS paths
are testable on any machine.
"""

import os
import platform
import shutil
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


MINIMUM_PYTHON = (3, 11)
HOMEBREW_INSTALL = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
COMMANDS = {
    "windows": {
        "python": "winget install -e --id Python.Python.3.12",
        "git": "winget install -e --id Git.Git",
        "node": "winget install -e --id OpenJS.NodeJS.LTS",
        "npm": "winget install -e --id OpenJS.NodeJS.LTS",
        "chrome": "winget install -e --id Google.Chrome",
    },
    "macos": {
        "python": "brew install python@3.12",
        "git": "brew install git",
        "node": "brew install node",
        "npm": "brew install node",
        "chrome": "brew install --cask google-chrome",
    },
}
LABELS = {"python": "Python 3.11+", "git": "git", "node": "Node.js", "npm": "npm", "chrome": "Google Chrome"}


def _os_name(system):
    return {"Windows": "windows", "Darwin": "macos"}.get(system, "other")


def _command(os_name, name):
    if os_name in COMMANDS:
        return COMMANDS[os_name][name]
    return f"Install {LABELS[name]} with your package manager"


def _chrome(os_name, which, is_file, environ, home):
    candidates = []
    if os_name == "windows":
        candidates = [
            PureWindowsPath(environ[key]) / "Google/Chrome/Application/chrome.exe"
            for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
            if environ.get(key)
        ]
    elif os_name == "macos":
        bundle = "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        candidates = [PurePosixPath("/") / bundle, PurePosixPath(home) / bundle]
    for path in candidates:
        if is_file(path):
            return str(path)
    for name in ("google-chrome", "chrome", "chromium"):
        located = which(name)
        if located:
            return located
    return None


def report(*, system, python_version, which, is_file, environ, home):
    os_name = _os_name(system)
    python_ok = tuple(python_version[:2]) >= MINIMUM_PYTHON
    found = {
        "python": ".".join(str(part) for part in python_version[:3]) if python_ok else None,
        "git": which("git"),
        "node": which("node"),
        "npm": which("npm"),
        "chrome": _chrome(os_name, which, is_file, environ, home),
    }
    checks = [{"name": name, "found": value is not None, "detail": value} for name, value in found.items()]
    missing = [{"name": name, "command": _command(os_name, name)} for name, value in found.items() if value is None]
    result = {"os": os_name, "ok": not missing, "checks": checks, "missing": missing}
    if os_name == "macos" and missing:
        result["homebrew"] = which("brew") is not None
    return result


def detect():
    return report(
        system=platform.system(),
        python_version=tuple(sys.version_info[:3]),
        which=shutil.which,
        is_file=lambda path: Path(path).is_file(),
        environ=dict(os.environ),
        home=str(Path.home()),
    )


def format_report(result):
    if result["ok"]:
        return "All dependencies found: Python 3.11+, git, Node.js/npm, Google Chrome."
    lines = ["Missing dependencies (nothing was installed):"]
    lines += [f"  - {LABELS[item['name']]}" for item in result["missing"]]
    lines.append("Install them with:")
    if result.get("homebrew") is False:
        lines.append("  " + HOMEBREW_INSTALL)
    commands = list(dict.fromkeys(item["command"] for item in result["missing"]))
    lines += ["  " + command for command in commands]
    lines.append("Then open a new terminal and rerun the installer.")
    return "\n".join(lines)
