"""Line endings: .gitattributes keeps text files LF in the index so CRLF churn cannot flood diffs."""

import json
import shutil
import subprocess
import unittest

from test_support import RELEASE

ATTRIBUTES = RELEASE / ".gitattributes"


def _rules():
    rules = {}
    for line in ATTRIBUTES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern, *attrs = line.split()
        rules[pattern] = attrs
    return rules


def _git(*args):
    return subprocess.run(
        ["git", "-C", str(RELEASE), *args], capture_output=True, text=True, encoding="utf-8", check=False
    )


def _in_git_checkout():
    return shutil.which("git") is not None and _git("rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


class GitattributesRulesTests(unittest.TestCase):
    def test_gitattributes_defaults_text_to_lf(self):
        self.assertTrue(ATTRIBUTES.is_file(), "The repo needs a .gitattributes file")
        self.assertEqual(_rules().get("*"), ["text=auto", "eol=lf"])

    def test_windows_scripts_keep_crlf(self):
        rules = _rules()
        for pattern in ("*.ps1", "*.cmd", "*.bat"):
            self.assertEqual(rules.get(pattern), ["text", "eol=crlf"], pattern)

    def test_binary_types_are_marked_binary(self):
        rules = _rules()
        for pattern in ("*.pdf", "*.png", "*.xlsx", "*.zip"):
            self.assertEqual(rules.get(pattern), ["binary"], pattern)

    def test_gitattributes_is_in_the_export_allowlist(self):
        allowed = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
        self.assertTrue(".gitattributes" in allowed, ".gitattributes must be listed in export-allowlist.json")


@unittest.skipUnless(_in_git_checkout(), "needs git and a git checkout (not an sdist)")
class GitIndexLineEndingTests(unittest.TestCase):
    def test_no_tracked_text_file_is_stored_with_crlf(self):
        listing = _git("ls-files", "--eol")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        crlf = [line.split("\t", 1)[-1] for line in listing.stdout.splitlines() if line.startswith("i/crlf")]
        mixed = [line.split("\t", 1)[-1] for line in listing.stdout.splitlines() if line.startswith("i/mixed")]
        self.assertEqual(crlf, [], "Run `git add --renormalize .` to store these files with LF")
        self.assertEqual(mixed, [], "These files mix CRLF and LF in the index")

    def test_powershell_installer_checks_out_with_crlf(self):
        result = _git("check-attr", "eol", "--", "install.ps1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "install.ps1: eol: crlf")

    def test_python_source_checks_out_with_lf(self):
        result = _git("check-attr", "eol", "--", "autoapply_local/__init__.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "autoapply_local/__init__.py: eol: lf")


if __name__ == "__main__":
    unittest.main()
