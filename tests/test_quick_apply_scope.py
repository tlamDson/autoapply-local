"""The shipped quick-apply product has no submit, tailor or mail-check command.

Their code lives on the archive/submit-tailor branch. The tool never clicks a
Submit control: no CLI command, service, workflow, browser or engine task may
request one.
"""

import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import RELEASE

REMOVED = ("submit", "tailor", "mail-check")
DOCS = (
    "README.md",
    "README.vi.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".codex/skills/autoapply-local/SKILL.md",
)
COMMAND_MENTION = re.compile(
    r"mail[-_]check|all-reviewed|explicit submit|/(?:submit|tailor)\b|`(?:submit|tailor)(?:[` ])"
    r"|autoapply-local(?:\.py)? (?:submit|tailor)\b",
    re.IGNORECASE,
)


def _doc_paths():
    paths = [RELEASE / name for name in DOCS]
    paths += sorted((RELEASE / ".claude/commands").glob("*.md"))
    paths += sorted((RELEASE / "autoapply_local/data/bootstrap").glob("*.md"))
    return paths


class RemovedCommandTests(unittest.TestCase):
    def test_cli_rejects_removed_commands(self):
        from autoapply_local import cli

        for argv in (["submit", "job-1"], ["tailor", "jd.txt"], ["mail-check"]):
            with self.subTest(argv=argv):
                stdout, stderr = io.StringIO(), io.StringIO()
                code = cli.main(["--user-dir", "unused", *argv], services={}, stdout=stdout, stderr=stderr)
                self.assertEqual(code, 2)
                self.assertIn("invalid choice", stderr.getvalue())

    def test_runtime_services_exclude_removed_commands(self):
        from autoapply_local import runtime

        names = set(runtime.services())
        self.assertFalse(names & {"submit", "tailor", "mail_check"})
        self.assertTrue({"setup", "fetch", "apply", "review", "status", "doctor"} <= names)
        for name in ("submit", "tailor", "mail_check", "NativeMailRuntime"):
            self.assertFalse(hasattr(runtime, name), name)

    def test_tailor_and_gmail_modules_are_not_shipped(self):
        for name in ("autoapply_local.tailor", "autoapply_local.gmail"):
            self.assertIsNone(importlib.util.find_spec(name), name)
        for relative in ("autoapply_local/data/ola.tex", "autoapply_local/data/tailor-prompt.md"):
            self.assertFalse((RELEASE / relative).exists(), relative)

    def test_workflow_and_browser_have_no_submit_operation(self):
        from autoapply_local import browser, workflow

        self.assertFalse(hasattr(workflow.Workflow, "submit"))
        self.assertFalse(hasattr(browser.BrowserBridge, "submit"))
        self.assertFalse(hasattr(browser.EngineMCPClient, "submit"))
        base = workflow.Workflow(tempfile.mkdtemp(), browser=None, engines={})
        with self.assertRaises(ValueError):
            base._selection("all-reviewed", "submit")

    def test_engine_refuses_every_submit_task_and_tool(self):
        from autoapply_local.engines import Engine

        calls = []
        engine = Engine("claude", runner=lambda *a, **k: calls.append(a))
        schema = {"type": "object", "properties": {}}
        tasks = (
            {"kind": "submit", "allowed_tools": [], "schema": schema},
            {"kind": "submit", "allowed_tools": ["mcp__chrome__click_submit"], "schema": schema},
            {"kind": "browser_fill", "allowed_tools": ["mcp__chrome__submit_form"], "schema": schema},
        )
        for task in tasks:
            with self.subTest(task=task):
                with self.assertRaises(ValueError):
                    engine.run(task)
        self.assertEqual(calls, [])

    def test_engine_prompt_never_grants_submit(self):
        from autoapply_local import engines

        source = Path(engines.__file__).read_text(encoding="utf-8")
        self.assertNotIn("kind=submit", source)
        self.assertNotIn('kind == "submit"', source)

    def test_bootstrap_installs_no_removed_command(self):
        from autoapply_local import bootstrap

        self.assertFalse(set(bootstrap.COMMANDS) & set(REMOVED))
        for name in REMOVED:
            self.assertFalse((RELEASE / f".claude/commands/{name}.md").exists(), name)
            self.assertFalse((RELEASE / f"autoapply_local/data/bootstrap/command-{name}.md").exists(), name)
        with tempfile.TemporaryDirectory() as temporary:
            bootstrap.install(temporary)
            installed = {path.stem for path in (Path(temporary) / ".claude/commands").glob("*.md")}
        self.assertFalse(installed & set(REMOVED))

    def test_allowlist_lists_no_removed_file(self):
        allowlist = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
        pattern = re.compile(r"(?:^|/)(?:command-)?(?:submit|tailor|mail-check|gmail|ola|tailor-prompt)[._]|test_(?:tailor|gmail)")
        self.assertEqual([item for item in allowlist if pattern.search(item)], [])

    def test_docs_do_not_mention_removed_commands(self):
        found = []
        for path in _doc_paths():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if COMMAND_MENTION.search(line):
                    found.append(f"{path.relative_to(RELEASE).as_posix()}:{number}")
        self.assertEqual(found, [])


class SourceExtractionTests(unittest.TestCase):
    def test_extract_source_moves_to_its_own_module(self):
        from autoapply_local.extraction import extract_source

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "resume.txt"
            path.write_text("Alex Rivera\n\nPython, SQL\n", encoding="utf-8")
            draft = extract_source(path)
        self.assertEqual(draft["status"], "draft")
        self.assertTrue(draft["requires_confirmation"])
        self.assertEqual([item["text"] for item in draft["facts"]], ["Alex Rivera", "Python, SQL"])
        self.assertEqual({item["status"] for item in draft["facts"]}, {"unknown"})

    def test_pdf_extraction_fails_closed_through_tool_text(self):
        from autoapply_local import extraction

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "resume.pdf"
            path.write_bytes(b"%PDF-synthetic")
            with patch.object(extraction, "_tool_text", return_value=None) as tool:
                with self.assertRaises(ValueError):
                    extraction.extract_source(path)
        self.assertEqual(tool.call_args.args[0][0], "pdftotext")


if __name__ == "__main__":
    unittest.main()
