"""Audit synthetic release trees offline, including ignored and allowlisted data.

Giả định của người thực thi: release_audit.audit(root, export_files, allowlist,
deny_fingerprints=()) returns {ok, violations}; export_release(root, destination,
allowlist) returns an exported file list and rejects unsafe exports with ValueError.
All content fixtures are synthetic; deny fingerprints are SHA256 strings.
"""

import hashlib
import importlib.util
import io
import json
import zipfile
from contextlib import redirect_stdout
from unittest.mock import patch

from test_support import RELEASE, ReleaseCase


class ReleaseAuditTests(ReleaseCase):
    def tree(self):
        root = self.root / "source"
        root.mkdir()
        (root / "README.md").write_text("# AutoApply Local\nExperimental offline example.\n", encoding="utf-8")
        examples = root / "examples"
        examples.mkdir()
        (examples / "profile.json").write_text(
            '{"legal_name":"Alex Example","application_email":"alex@example.invalid"}', encoding="utf-8"
        )
        return root, ["README.md", "examples/profile.json"]

    def test_synthetic_allowlisted_public_examples_pass(self):
        root, allowed = self.tree()
        report = self.module("release_audit").audit(root, export_files=allowed, allowlist=allowed)
        self.assertIs(report["ok"], True)
        self.assertEqual(report["violations"], [])

    def test_files_not_on_explicit_allowlist_are_rejected_even_with_benign_contents(self):
        root, allowed = self.tree()
        (root / "private-notes.md").write_text("Synthetic harmless text", encoding="utf-8")
        report = self.module("release_audit").audit(
            root, export_files=allowed + ["private-notes.md"], allowlist=allowed
        )
        self.assertIs(report["ok"], False)
        self.assertTrue(report["violations"])
        self.assertIn("private-notes.md", str(report["violations"]))

    def test_public_https_urls_are_not_misread_as_windows_drive_paths(self):
        root, allowed = self.tree()
        name = "public_transport.py"
        (root / name).write_text('url = "https://jobs.example.invalid/posting"\n', encoding="utf-8")
        report = self.module("release_audit").audit(root, allowed + [name], allowed + [name])
        self.assertIs(report["ok"], True, report["violations"])

    def test_export_file_list_cannot_hide_sensitive_paths_behind_gitignore(self):
        root, allowed = self.tree()
        (root / ".gitignore").write_text("user/**\nlogs/**\n", encoding="utf-8")
        for name in (
            "user/profile.json",
            "logs/run.json",
            "accounts.md",
            "secrets.local.md",
            "tests/fixtures/real-form.json",
        ):
            with self.subTest(name=name):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Synthetic private material", encoding="utf-8")
                # Even an accidentally expanded allowlist cannot authorize private data.
                requested = allowed + [name]
                report = self.module("release_audit").audit(root, export_files=requested, allowlist=requested)
                self.assertIs(report["ok"], False)
                self.assertIn(name, str(report["violations"]))

    def test_allowlisted_readme_contents_are_scanned_for_personal_contact_data(self):
        root, allowed = self.tree()
        (root / "README.md").write_text(
            "Synthetic private email: candidate.person@personal.example\n", encoding="utf-8"
        )
        report = self.module("release_audit").audit(root, export_files=allowed, allowlist=allowed)
        self.assertIs(report["ok"], False)
        self.assertIn("README.md", str(report["violations"]))

    def test_generated_resume_content_is_scanned_even_if_filename_looks_public(self):
        root, allowed = self.tree()
        name = "examples/demo-resume.tex"
        (root / name).write_text(
            "\\documentclass{article}\n\\begin{document}\n"
            "Synthetic Person \\href{mailto:person@private.example}{Contact}\n\\end{document}\n",
            encoding="utf-8",
        )
        report = self.module("release_audit").audit(root, export_files=allowed + [name], allowlist=allowed + [name])
        self.assertIs(report["ok"], False)
        self.assertIn(name, str(report["violations"]))

    def test_hardcoded_private_workspace_paths_are_rejected_in_exported_content(self):
        root, allowed = self.tree()
        for private_path in (
            "Z:\\PrivateWorkspace\\AutoApply\\user\\profile.json",
            "/Users/synthetic-person/PrivateWorkspace/AutoApply/user/profile.json",
        ):
            with self.subTest(path=private_path):
                (root / "README.md").write_text("Source location: " + private_path, encoding="utf-8")
                report = self.module("release_audit").audit(root, export_files=allowed, allowlist=allowed)
                self.assertIs(report["ok"], False)
                self.assertIn("README.md", str(report["violations"]))

    def test_external_deny_fingerprint_detects_copied_material_without_embedded_identity(self):
        root, allowed = self.tree()
        private_payload = b"Synthetic uniquely fingerprinted candidate record"
        (root / "README.md").write_bytes(private_payload)
        fingerprint = hashlib.sha256(private_payload).hexdigest()
        report = self.module("release_audit").audit(
            root, export_files=allowed, allowlist=allowed, deny_fingerprints=[fingerprint]
        )
        self.assertIs(report["ok"], False)
        self.assertIn("README.md", str(report["violations"]))

    def test_missing_export_file_is_an_error_instead_of_silent_omission(self):
        root, allowed = self.tree()
        requested = allowed + ["README.vi.md"]
        report = self.module("release_audit").audit(root, export_files=requested, allowlist=requested)
        self.assertIs(report["ok"], False)
        self.assertIn("README.vi.md", str(report["violations"]))

    def test_export_rejects_parent_traversal_and_absolute_file_entries(self):
        root, allowed = self.tree()
        outside = self.root / "private.md"
        outside.write_text("Synthetic private material", encoding="utf-8")
        audit = self.module("release_audit")
        for entry in ("../private.md", str(outside)):
            with self.subTest(entry=entry):
                report = audit.audit(root, export_files=[entry], allowlist=[entry])
                self.assertIs(report["ok"], False)

    def test_export_copies_only_allowlist_not_all_unignored_files(self):
        root, allowed = self.tree()
        (root / "scratch.md").write_text("Synthetic unrelated content", encoding="utf-8")
        destination = self.root / "export"
        self.module("release_audit").export_release(root, destination, allowlist=allowed)
        files = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()}
        self.assertEqual(files, set(allowed))
        for name in allowed:
            self.assertEqual((destination / name).read_bytes(), (root / name).read_bytes())

    def test_unsafe_export_is_rejected_before_private_content_is_copied(self):
        root, allowed = self.tree()
        (root / "README.md").write_text("Synthetic private: person@private.example", encoding="utf-8")
        destination = self.root / "export"
        with self.assertRaises(ValueError):
            self.module("release_audit").export_release(root, destination, allowlist=allowed)
        self.assertFalse(destination.exists() and any(path.is_file() for path in destination.rglob("*")))

    def checker(self):
        spec = importlib.util.spec_from_file_location("release_checker", RELEASE / "scripts/check_release.py")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        return checker

    def test_obsolete_thin_wheel_is_rejected_and_member_privacy_is_still_scanned(self):
        root, allowed = self.tree()
        package = root / "autoapply_local"
        package.mkdir()
        (package / "__init__.py").write_text('"""Synthetic package."""\n', encoding="utf-8")
        (root / "pyproject.toml").write_text(
            '[project]\nname = "autoapply-local"\nversion = "9.8.7a6"\ndependencies = []\n'
            "[tool.setuptools.package-data]\nautoapply_local = []\n",
            encoding="utf-8",
        )
        allowed += ["autoapply_local/__init__.py", "export-allowlist.json", "pyproject.toml"]
        (root / "export-allowlist.json").write_text(json.dumps(allowed), encoding="utf-8")
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        path = artifacts / "autoapply_local-0.1.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("autoapply_local/__init__.py", (package / "__init__.py").read_bytes())
            archive.writestr("autoapply_local-0.1.0.dist-info/METADATA", "Name: autoapply-local\nVersion: 0.1.0\n")
            archive.writestr("autoapply_local-0.1.0.dist-info/RECORD", "")
        checker = self.checker()
        with patch.object(checker, "ROOT", root), redirect_stdout(io.StringIO()):
            self.assertEqual(checker.main(["--artifacts", str(artifacts)]), 1)
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("user/profile.json", '{"name":"Synthetic private runtime"}')
            self.assertEqual(checker.main(["--artifacts", str(artifacts)]), 1)
        # A stale filename may fail before payload inspection in the final-artifact
        # API. Retain independent coverage of member privacy in the archive scanner.
        report = self.module("release_audit").audit(
            artifacts,
            [path.name],
            [
                path.name,
                "autoapply_local/__init__.py",
                "autoapply_local-0.1.0.dist-info/METADATA",
                "autoapply_local-0.1.0.dist-info/RECORD",
                "user/profile.json",
            ],
        )
        self.assertIs(report["ok"], False)
        self.assertTrue(
            any("user/profile.json" in item["path"] and "private" in item["reason"] for item in report["violations"]),
            report,
        )

    def test_published_export_includes_allowlisted_synthetic_test_sources(self):
        root, allowed = self.tree()
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_local.py").write_text('"""Synthetic offline test."""\n', encoding="utf-8")
        allowed += ["tests/test_local.py", "export-allowlist.json"]
        (root / "export-allowlist.json").write_text(json.dumps(allowed), encoding="utf-8")
        checker = self.checker()
        destination = self.root / "published"
        with patch.object(checker, "ROOT", root), redirect_stdout(io.StringIO()):
            self.assertEqual(checker.main(["--export", str(destination)]), 0)
        self.assertTrue((destination / "README.md").is_file())
        self.assertTrue((destination / "tests/test_local.py").is_file(), "CI needs the allowlisted test source")
        self.assertEqual((destination / "tests/test_local.py").read_bytes(), (tests / "test_local.py").read_bytes())
        self.assertEqual(json.loads((destination / "export-allowlist.json").read_text(encoding="utf-8")), allowed)
