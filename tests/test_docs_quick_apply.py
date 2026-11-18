"""The user documents follow the quick-apply flows (plan aa-23).

Commands shown in the documents must be accepted by the real CLI parser, every
CLI command is listed in both READMEs, relative links resolve, the two READMEs
keep the same structure and commands, and repository text other than
README.vi.md is English.
"""

import ast
import io
import re
import tempfile
import unittest
from pathlib import Path

from test_support import RELEASE

from autoapply_local import cli

BOOTSTRAP = "autoapply_local/data/bootstrap/"
README = ("README.md", "README.vi.md")
AGENT_DOCS = ("AGENTS.md", "CLAUDE.md", BOOTSTRAP + "AGENTS.md")
DOCS = (*README, *AGENT_DOCS, BOOTSTRAP + "README.md")
ENGLISH = ("README.md", *AGENT_DOCS, BOOTSTRAP + "README.md")
FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.DOTALL)
COMMAND = re.compile(r"^(?:python|py|\S*python\S*) autoapply-local\.py\s+(.+)$")
LINK = re.compile(r"\]\(([^)\s]+)\)")
REFERENCE_ITEM = re.compile(r"^- `([a-z-]+)")
VIETNAMESE = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]", re.IGNORECASE)
LANGUAGE_LINK = "[Tiếng Việt](README.vi.md)"
SECTIONS = {
    "README.md": ("## Install", "## Setup, part 1: terminal", "## Setup, part 2: in your engine",
                  "## Daily use: apply", "## Update", "## Your data", "## Commands"),
    "README.vi.md": ("## Cài đặt", "## Thiết lập, phần 1: terminal", "## Thiết lập, phần 2: trong engine",
                     "## Dùng hằng ngày: apply", "## Cập nhật", "## Dữ liệu của bạn", "## Lệnh"),
}
MACOS = {"README.md": "macOS has not been tested live", "README.vi.md": "macOS chưa được thử thật"}


def text(relative):
    return (RELEASE / relative).read_text(encoding="utf-8")


def fenced_commands(relative):
    found = []
    for block in FENCE.findall(text(relative)):
        for line in block.splitlines():
            match = COMMAND.match(line.strip())
            if match:
                found.append(match.group(1).split())
    return found


def cli_commands():
    stdout = io.StringIO()
    cli.main(["--help"], services={}, stdout=stdout, stderr=io.StringIO())
    return set(re.search(r"\{([a-z,-]+)\}", stdout.getvalue()).group(1).split(","))


def reference_commands(relative):
    lines = text(relative).splitlines()
    heading = SECTIONS[relative][-1]
    start = lines.index(heading)
    names = set()
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        match = REFERENCE_ITEM.match(line)
        if match:
            names.add(match.group(1))
    return names


class Recorder(dict):
    def __missing__(self, name):
        return lambda **kwargs: {"called": name}


class CommandsMatchCliTests(unittest.TestCase):
    def test_every_shown_command_parses_with_the_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            for relative in DOCS:
                for argv in fenced_commands(relative):
                    with self.subTest(document=relative, command=" ".join(argv)):
                        stderr = io.StringIO()
                        code = cli.main(["--user-dir", temporary, *argv], services=Recorder(),
                                        stdout=io.StringIO(), stderr=stderr)
                        self.assertEqual(code, 0, stderr.getvalue())

    def test_readmes_show_commands_for_each_flow(self):
        shown = {argv[0] for argv in fenced_commands("README.md")}
        for name in ("setup", "apply", "track", "status", "review", "submitted", "questions", "update", "doctor"):
            self.assertIn(name, shown)

    def test_both_readmes_show_the_same_commands(self):
        self.assertEqual(fenced_commands("README.md"), fenced_commands("README.vi.md"))

    def test_command_reference_lists_exactly_the_cli_commands(self):
        commands = cli_commands()
        self.assertIn("apply", commands)
        for relative in README:
            with self.subTest(document=relative):
                self.assertEqual(reference_commands(relative), commands)


class StructureTests(unittest.TestCase):
    def test_readmes_follow_install_setup_apply_update(self):
        for relative, headings in SECTIONS.items():
            with self.subTest(document=relative):
                lines = text(relative).splitlines()
                positions = [lines.index(heading) for heading in headings]
                self.assertEqual(positions, sorted(positions))

    def test_readmes_have_the_same_sections(self):
        counts = {relative: sum(line.startswith("## ") for line in text(relative).splitlines()) for relative in README}
        self.assertEqual(counts["README.md"], counts["README.vi.md"])

    def test_relative_links_resolve(self):
        for relative in (*README, "AGENTS.md", "CLAUDE.md"):
            for target in LINK.findall(text(relative)):
                if re.match(r"[a-z]+:", target) or target.startswith("#"):
                    continue
                with self.subTest(document=relative, link=target):
                    self.assertTrue((RELEASE / target.split("#")[0]).exists())

    def test_one_line_installers_exist(self):
        for relative in README:
            for name in re.findall(r"raw\.githubusercontent\.com/tlamDson/autoapply-local/main/(\S+?)(?=\s|\|)", text(relative)):
                with self.subTest(document=relative, file=name):
                    self.assertTrue((RELEASE / name).is_file())


class ContentTests(unittest.TestCase):
    def test_docs_name_both_document_folders(self):
        for relative in DOCS:
            with self.subTest(document=relative):
                body = text(relative)
                self.assertIn("user/resume/", body)
                self.assertIn("user/additional-document/", body)

    def test_macos_is_stated_as_not_tested_live(self):
        for relative, phrase in MACOS.items():
            self.assertIn(phrase, text(relative), relative)
        for relative in AGENT_DOCS:
            self.assertIn(MACOS["README.md"], text(relative), relative)

    def test_readmes_do_not_claim_passwords_are_never_stored(self):
        # Workday credentials are stored in secrets.json since aa-06.
        for relative in DOCS:
            self.assertNotIn("Passwords are not collected", text(relative), relative)
        for relative in README:
            self.assertIn("secrets.json", text(relative), relative)


class EnglishTests(unittest.TestCase):
    def test_repository_documents_are_english(self):
        for relative in ENGLISH:
            body = text(relative).replace(LANGUAGE_LINK, "")
            with self.subTest(document=relative):
                self.assertEqual(VIETNAMESE.findall(body), [])

    def test_intake_docstring_is_english(self):
        docstring = ast.get_docstring(ast.parse(text("autoapply_local/intake.py")))
        self.assertEqual(VIETNAMESE.findall(docstring), [])


if __name__ == "__main__":
    unittest.main()
