"""SOURCES.txt contracts exercised through real audits of synthetic archives.

Giả định của người thực thi:
- Reuse CurrentPackageCase through its module, without importing test classes.
- Leave the plan and RED log to the runner because this turn permits tests only.
"""

import test_package_current as current

from autoapply_local.release_audit import audit_artifacts


class PackageManifestTests(current.CurrentPackageCase):
    def sources_name(self):
        return f"autoapply_local-{self.version}/autoapply_local.egg-info/SOURCES.txt"

    def sources_lines(self):
        return self.members("sdist")[self.sources_name()].decode("utf-8").splitlines()

    def audit_manifest(self, lines, *, final_newline=True):
        self.valid_set()
        members = self.members("sdist")
        text = "\n".join(lines)
        if lines and final_newline:
            text += "\n"
        members[self.sources_name()] = text.encode("utf-8")
        self.write_archive("sdist", members)
        return audit_artifacts(self.source, self.artifacts, self.files, expect=("wheel", "sdist", "zip"))

    def assert_manifest_rejected(self, report, reason):
        self.assert_rejected(report, member=self.sources_name())
        self.assertEqual(len(report["violations"]), 1, report)
        self.assertIn(reason, report["violations"][0]["reason"].lower(), report)

    def test_empty_sources_is_rejected(self):
        self.assert_manifest_rejected(self.audit_manifest([]), "missing")

    def test_every_source_and_resource_must_be_listed(self):
        lines = self.sources_lines()
        required = [name for name in self.files if not name.startswith("tests/")]
        for missing in required:
            with self.subTest(missing=missing):
                self.assertIn(missing, lines)
                report = self.audit_manifest([line for line in lines if line != missing])
                self.assert_manifest_rejected(report, "missing")

    def test_every_egg_info_member_must_be_listed(self):
        lines = self.sources_lines()
        prefix = f"autoapply_local-{self.version}/"
        required = [
            name.removeprefix(prefix)
            for name in self.members("sdist")
            if name.removeprefix(prefix).startswith("autoapply_local.egg-info/")
        ]
        self.assertIn("autoapply_local.egg-info/PKG-INFO", required)
        self.assertIn("autoapply_local.egg-info/SOURCES.txt", required)
        for missing in required:
            with self.subTest(missing=missing):
                self.assertIn(missing, lines)
                report = self.audit_manifest([line for line in lines if line != missing])
                self.assert_manifest_rejected(report, "missing")

    def test_duplicate_allowed_lines_are_rejected(self):
        lines = self.sources_lines()
        for duplicate in lines:
            with self.subTest(duplicate=duplicate):
                self.assert_manifest_rejected(self.audit_manifest([*lines, duplicate]), "duplicate")

    def test_complete_sources_with_well_formed_unlisted_path_is_rejected(self):
        # Already GREEN: protects the per-line allowlist independently of completeness.
        lines = self.sources_lines()
        for extra in ("extra.txt", "private/notes.txt"):
            with self.subTest(extra=extra):
                self.assertNotIn(extra, lines)
                self.assert_manifest_rejected(
                    self.audit_manifest([*lines, extra]), "sources.txt path not on explicit allowlist"
                )

    def test_complete_sources_accepts_optional_generated_root_entries(self):
        lines = self.sources_lines()
        optional = {"PKG-INFO", "setup.cfg"}
        self.assertTrue(optional <= set(lines))
        required = [line for line in lines if line not in optional]
        for included in ((), ("PKG-INFO",), ("setup.cfg",), ("PKG-INFO", "setup.cfg")):
            for final_newline in (False, True):
                with self.subTest(included=included, final_newline=final_newline):
                    report = self.audit_manifest(sorted([*required, *included]), final_newline=final_newline)
                    self.assertIs(report["ok"], True, report)
                    self.assertEqual(report["violations"], [])
                    self.assertEqual(report["files_checked"], 3)
