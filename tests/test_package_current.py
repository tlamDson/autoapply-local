"""Current-artifact contracts: synthetic archives and mocked installs, offline.

Giả định của người thực thi:
- Use audit_artifacts(root, artifact_dir, source_files, expect=..., deny=()).
- check_install exposes main(argv), ROOT, subprocess and venv; ordinary failures
  return 1, consistent with check_release. Successful probe subprocesses perform
  their own assertions and may emit an empty JSON object.
- Every RECORD includes its own row with empty hash/size; generated metadata uses
  canonical setuptools spelling and dependency order from the fake pyproject.
- The plan/RED log are left untouched because this turn permits test files only.
"""

import base64
import csv
import fnmatch
import hashlib
import importlib.util
import io
import json
import math
import os
import stat
import subprocess
import tarfile
import tomllib
import warnings
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from test_support import RELEASE, ReleaseCase


class CurrentPackageCase(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.source = self.root / "synthetic source thử ünicode"
        self.source.mkdir()
        contents = {
            "pyproject.toml": (
                '[build-system]\nrequires = ["setuptools==84.1.0", "wheel==0.48.0"]\n'
                'build-backend = "setuptools.build_meta"\n'
                '[project]\nname = "autoapply-local"\nversion = "9.8.7a6"\n'
                'dependencies = ["example-dep==2.3.4", "second-dep>=1"]\n'
                '[project.scripts]\nautoapply-local = "autoapply_local.cli:main"\n'
                "[tool.setuptools.package-data]\nautoapply_local = "
                '["data/*.tex", "data/*.md", "data/*.json", "adapters/*.cjs", '
                '"data/bootstrap/*.py", "data/bootstrap/*.md"]\n'
            ),
            "LICENSE": "Synthetic public license.\n",
            "NOTICE": "Synthetic public notice.\n",
            "README.md": "# Synthetic experimental release\n",
            ".gitignore": "__pycache__/\n",
            "MANIFEST.in": "include LICENSE NOTICE\n",
            "templates/profile-intake.md": "# Synthetic blank intake\n",
            "scripts/check_install.py": '"""Synthetic install helper."""\n',
            "autoapply_local/__init__.py": '"""Synthetic package."""\n',
            "autoapply_local/cli.py": "def main():\n    return 0\n",
            "autoapply_local/data/x.tex": "\\documentclass{article}\n",
            "autoapply_local/data/prompt.md": "# Synthetic template\n",
            "autoapply_local/data/boards.json": "[]\n",
            "autoapply_local/adapters/a.cjs": "module.exports = {};\n",
            "autoapply_local/data/bootstrap/b.py": "SYNTHETIC = True\n",
            "autoapply_local/data/bootstrap/guide.md": "# Synthetic guide\n",
            # Allowlisted, but not selected by package-data globs for the wheel.
            "autoapply_local/data/unshipped.txt": "Source-only synthetic note.\n",
            "tests/test_x.py": "def test_synthetic():\n    assert True\n",
        }
        self.files = sorted([*contents, "export-allowlist.json"])
        contents["export-allowlist.json"] = json.dumps(self.files) + "\n"
        for name, content in contents.items():
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
        self.project = tomllib.loads((self.source / "pyproject.toml").read_text(encoding="utf-8"))
        self.version = self.project["project"]["version"]
        self.artifacts = self.root / "final artifacts thử ünicode"
        self.artifacts.mkdir()

    def load_script(self, name):
        path = RELEASE / "scripts" / (name + ".py")
        self.assertTrue(path.is_file(), f"Missing planned script: scripts/{name}.py")
        spec = importlib.util.spec_from_file_location("synthetic_" + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def filename(self, kind, version=None):
        version = version or self.version
        return {
            "wheel": f"autoapply_local-{version}-py3-none-any.whl",
            "sdist": f"autoapply_local-{version}.tar.gz",
            "zip": f"autoapply-local-{version}.zip",
        }[kind]

    def metadata(self):
        project = self.project["project"]
        return (
            f"Metadata-Version: 2.4\nName: {project['name']}\nVersion: {self.version}\n"
            + "".join(f"Requires-Dist: {dep}\n" for dep in project["dependencies"])
            + "\n"
        ).encode()

    def record_name(self):
        return f"autoapply_local-{self.version}.dist-info/RECORD"

    def record(self, members):
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        for name, payload in sorted(members.items()):
            if name == self.record_name():
                continue
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
            writer.writerow([name, "sha256=" + digest, len(payload)])
        writer.writerow([self.record_name(), "", ""])
        return output.getvalue().encode()

    def members(self, kind):
        source = {name: (self.source / name).read_bytes() for name in self.files}
        entry = b"[console_scripts]\nautoapply-local = autoapply_local.cli:main\n"
        top = b"autoapply_local\n"
        if kind == "zip":
            return {f"autoapply-local-{self.version}/{name}": data for name, data in source.items()}
        if kind == "sdist":
            source = {name: data for name, data in source.items() if not name.startswith("tests/")}
            generated = {
                "PKG-INFO": self.metadata(),
                "setup.cfg": b"[egg_info]\ntag_build = \ntag_date = 0\n\n",
                "autoapply_local.egg-info/PKG-INFO": self.metadata(),
                "autoapply_local.egg-info/dependency_links.txt": b"\n",
                "autoapply_local.egg-info/entry_points.txt": entry,
                "autoapply_local.egg-info/requires.txt": (
                    "\n".join(self.project["project"]["dependencies"]) + "\n"
                ).encode(),
                "autoapply_local.egg-info/top_level.txt": top,
            }
            generated["autoapply_local.egg-info/SOURCES.txt"] = (
                "\n".join(sorted([*source, *generated, "autoapply_local.egg-info/SOURCES.txt"])) + "\n"
            ).encode()
            prefix = f"autoapply_local-{self.version}/"
            return {prefix + name: data for name, data in {**source, **generated}.items()}
        patterns = self.project["tool"]["setuptools"]["package-data"]["autoapply_local"]
        package = {}
        for name, data in source.items():
            if not name.startswith("autoapply_local/"):
                continue
            relative = name.removeprefix("autoapply_local/")
            if ("/" not in relative and relative.endswith(".py")) or any(
                fnmatch.fnmatchcase(relative, pattern) for pattern in patterns
            ):
                package[name] = data
        directory = f"autoapply_local-{self.version}.dist-info/"
        package.update(
            {
                directory + "METADATA": self.metadata(),
                directory + "WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                directory + "entry_points.txt": entry,
                directory + "top_level.txt": top,
                directory + "licenses/LICENSE": source["LICENSE"],
                directory + "licenses/NOTICE": source["NOTICE"],
            }
        )
        package[self.record_name()] = self.record(package)
        return package

    def write_archive(self, kind, members=None, *, version=None, extra=(), refresh_record=True):
        members = dict(self.members(kind) if members is None else members)
        if kind == "wheel" and refresh_record:
            members[self.record_name()] = self.record(members)
        path = self.artifacts / self.filename(kind, version)
        pairs = [*members.items(), *extra]
        if kind == "sdist":
            with tarfile.open(path, "w:gz") as archive:
                for name, payload in pairs:
                    info = tarfile.TarInfo(name)
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)  # Intentional duplicate-member fixtures.
                with zipfile.ZipFile(path, "w") as archive:
                    for name, payload in pairs:
                        info = zipfile.ZipInfo()
                        info.filename = info.orig_filename = name
                        archive.writestr(info, payload)
        return path

    def valid_set(self):
        for kind in ("wheel", "sdist", "zip"):
            self.write_archive(kind)

    def assert_rejected(self, report, reason=None, member=None):
        self.assertIs(report["ok"], False, report)
        matching = report["violations"]
        if member:
            matching = [item for item in matching if member in str(item["path"])]
            self.assertTrue(matching, f"Missing member-specific violation for {member}: {report}")
        if reason:
            self.assertTrue(any(reason in item["reason"] for item in matching), report)


class PackageCurrentTests(CurrentPackageCase):
    def test_complete_current_artifacts_pass(self):
        from autoapply_local.release_audit import audit_artifacts

        self.valid_set()
        report = audit_artifacts(self.source, self.artifacts, self.files, expect=("wheel", "sdist", "zip"))
        self.assertIs(report["ok"], True, report)
        self.assertEqual(report["violations"], [])

    def test_original_stale_wheel_allowlist_regression(self):
        checker = self.load_script("check_release")
        old = "0.1.0"
        # Reproduce the original acceptance bug by assertion, before exercising
        # the newly planned optional argument (which does not exist during RED).
        with self.assertRaisesRegex(ValueError, "stale artifact version"):
            checker.artifact_allowlist(f"autoapply_local-{old}-py3-none-any.whl", self.files)
        with self.assertRaisesRegex(ValueError, "stale artifact version"):
            checker.artifact_allowlist(f"autoapply_local-{old}-py3-none-any.whl", self.files, version=old + "a1")

    def test_original_stale_sdist_allowlist_regression(self):
        checker = self.load_script("check_release")
        old = "0.1.0"
        with self.assertRaisesRegex(ValueError, "stale artifact version"):
            checker.artifact_allowlist(f"autoapply_local-{old}.tar.gz", self.files)
        with self.assertRaisesRegex(ValueError, "stale artifact version"):
            checker.artifact_allowlist(f"autoapply_local-{old}.tar.gz", self.files, version=old + "a1")

    def test_wheel_allowlist_excludes_source_only_files(self):
        checker = self.load_script("check_release")
        allowed = set(checker.artifact_allowlist(self.filename("wheel"), self.files, version=self.version))
        for member in (
            "README.md",
            "tests/test_x.py",
            "templates/profile-intake.md",
            "autoapply_local/data/unshipped.txt",
        ):
            with self.subTest(member=member):
                self.assertNotIn(member, allowed)
        self.assertTrue(set(self.members("wheel")) <= allowed)

    def test_metadata_version_must_match_source_in_each_location(self):
        from autoapply_local.release_audit import audit_artifacts

        for kind, suffix in (
            ("wheel", "/METADATA"),
            ("sdist", "/PKG-INFO"),
            ("sdist", "/autoapply_local.egg-info/PKG-INFO"),
        ):
            with self.subTest(kind=kind, location=suffix):
                self.valid_set()
                members = self.members(kind)
                name = next(name for name in members if name.endswith(suffix))
                members[name] = members[name].replace(self.version.encode(), b"1.2.3")
                self.write_archive(kind, members)
                self.assert_rejected(audit_artifacts(self.source, self.artifacts, self.files), member=name)

    def test_metadata_name_and_dependencies_must_match_project(self):
        from autoapply_local.release_audit import audit_artifacts

        for kind in ("wheel", "sdist"):
            for old, new in (
                (b"Name: autoapply-local", b"Name: different-project"),
                (b"Requires-Dist: example-dep==2.3.4", b"Requires-Dist: example-dep==0"),
            ):
                with self.subTest(kind=kind, header=old):
                    self.valid_set()
                    members = self.members(kind)
                    name = next(name for name in members if name.endswith(("/METADATA", "/PKG-INFO")))
                    members[name] = members[name].replace(old, new)
                    self.write_archive(kind, members)
                    self.assert_rejected(audit_artifacts(self.source, self.artifacts, self.files), member=name)

    def test_missing_wheel_resources_are_identified(self):
        from autoapply_local.release_audit import audit_artifacts

        for name in (
            "autoapply_local/data/x.tex",
            "autoapply_local/adapters/a.cjs",
            "autoapply_local/data/bootstrap/b.py",
        ):
            with self.subTest(resource=name):
                self.valid_set()
                members = self.members("wheel")
                del members[name]
                self.write_archive("wheel", members)
                self.assert_rejected(
                    audit_artifacts(self.source, self.artifacts, self.files), "missing required member", name
                )

    def test_required_generated_metadata_cannot_be_omitted(self):
        from autoapply_local.release_audit import audit_artifacts

        for kind in ("wheel", "sdist"):
            required = [
                name
                for name in self.members(kind)
                if ".dist-info/" in name or ".egg-info/" in name or name.endswith(("/PKG-INFO", "/setup.cfg"))
            ]
            for name in required:
                with self.subTest(kind=kind, member=name):
                    self.valid_set()
                    members = self.members(kind)
                    del members[name]
                    self.write_archive(kind, members, refresh_record=name != self.record_name())
                    self.assert_rejected(
                        audit_artifacts(self.source, self.artifacts, self.files), "missing required member", name
                    )

    def test_source_bytes_must_match_in_every_artifact(self):
        from autoapply_local.release_audit import audit_artifacts

        for kind in ("wheel", "sdist", "zip"):
            with self.subTest(kind=kind):
                self.valid_set()
                members = self.members(kind)
                name = next(name for name in members if name.endswith("autoapply_local/cli.py"))
                members[name] = b"def main():\n    return 1\n"
                self.write_archive(kind, members)
                self.assert_rejected(
                    audit_artifacts(self.source, self.artifacts, self.files), "source payload differs", name
                )

    def test_duplicate_members_rejected_in_all_archive_formats(self):
        from autoapply_local.release_audit import audit_artifacts

        for kind in ("wheel", "sdist", "zip"):
            with self.subTest(kind=kind):
                self.valid_set()
                members = self.members(kind)
                name = next(iter(members))
                self.write_archive(kind, members, extra=[(name, members[name])])
                self.assert_rejected(
                    audit_artifacts(self.source, self.artifacts, self.files), "duplicate archive member", name
                )

    def test_extra_files_and_tests_in_built_distributions_rejected(self):
        from autoapply_local.release_audit import audit_artifacts

        for kind in ("wheel", "sdist", "zip"):
            for relative in ("extra.txt", "tests/test_x.py"):
                if kind == "zip" and relative.startswith("tests/"):
                    continue  # This file is required, rather than forbidden, in the source ZIP.
                with self.subTest(kind=kind, relative=relative):
                    self.valid_set()
                    members = self.members(kind)
                    prefix = (
                        ""
                        if kind == "wheel"
                        else self.filename(kind).removesuffix(".tar.gz").removesuffix(".zip") + "/"
                    )
                    name = prefix + relative
                    members[name] = b"# Synthetic unshipped file\n"
                    self.write_archive(kind, members)
                    self.assert_rejected(
                        audit_artifacts(self.source, self.artifacts, self.files), "not on explicit allowlist", name
                    )

    def test_source_zip_requires_tests(self):
        from autoapply_local.release_audit import audit_artifacts

        self.valid_set()
        members = self.members("zip")
        name = next(name for name in members if name.endswith("tests/test_x.py"))
        del members[name]
        self.write_archive("zip", members)
        self.assert_rejected(
            audit_artifacts(self.source, self.artifacts, self.files, expect=("wheel", "sdist", "zip")),
            "missing required member",
            name,
        )

    def test_record_hash_size_rows_and_self_row_are_verified(self):
        from autoapply_local.release_audit import audit_artifacts

        for damage in ("hash", "size", "missing", "extra", "duplicate", "self"):
            with self.subTest(damage=damage):
                self.valid_set()
                members = self.members("wheel")
                rows = list(csv.reader(io.StringIO(members[self.record_name()].decode())))
                if damage == "hash":
                    rows[0][1] = "sha256=" + "A" * 43
                elif damage == "size":
                    rows[0][2] = str(int(rows[0][2]) + 1)
                elif damage == "missing":
                    rows.pop(0)
                elif damage == "extra":
                    rows.append(["unlisted.py", "sha256=" + "A" * 43, "1"])
                elif damage == "duplicate":
                    rows.append(rows[0])
                else:
                    rows[-1][1:] = ["sha256=" + "A" * 43, "1"]
                output = io.StringIO(newline="")
                csv.writer(output).writerows(rows)
                members[self.record_name()] = output.getvalue().encode()
                self.write_archive("wheel", members, refresh_record=False)
                self.assert_rejected(audit_artifacts(self.source, self.artifacts, self.files), member="RECORD")

    def test_generated_metadata_contents_are_constrained(self):
        from autoapply_local.release_audit import audit_artifacts

        cases = (
            ("sdist", "/setup.cfg", b"[install]\ninstall_scripts = elsewhere\n"),
            ("sdist", "/requires.txt", b"example-dep==0\n"),
            ("sdist", "/entry_points.txt", b"[console_scripts]\nautoapply-local = wrong:main\n"),
            ("wheel", "/entry_points.txt", b"[console_scripts]\nautoapply-local = wrong:main\n"),
            ("wheel", "/top_level.txt", b"different_package\n"),
            ("sdist", "/top_level.txt", b"different_package\n"),
            ("sdist", "/SOURCES.txt", b"../outside.py\n"),
            ("wheel", "/WHEEL", b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: py3-none-any\n"),
            ("wheel", "/WHEEL", b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: cp311-linux_x86_64\n"),
            ("wheel", "/licenses/LICENSE", b"Different license.\n"),
            ("wheel", "/licenses/NOTICE", b"Different notice.\n"),
        )
        for kind, suffix, payload in cases:
            with self.subTest(kind=kind, member=suffix, payload=payload):
                self.valid_set()
                members = self.members(kind)
                name = next(name for name in members if name.endswith(suffix))
                members[name] = payload
                self.write_archive(kind, members)
                self.assert_rejected(audit_artifacts(self.source, self.artifacts, self.files), member=name)

    def test_unsafe_paths_still_rejected_without_extraction(self):
        from autoapply_local.release_audit import audit_artifacts

        for kind in ("wheel", "sdist", "zip"):
            for name in ("../escape.py", "/absolute.py", "Q:/escape.py", "..\\escape.py"):
                with self.subTest(kind=kind, path=name):
                    self.valid_set()
                    self.write_archive(kind, extra=[(name, b"# Synthetic traversal\n")])
                    self.assert_rejected(audit_artifacts(self.source, self.artifacts, self.files), member=name)
        self.assertFalse((self.root / "escape.py").exists())

    def test_links_and_special_members_still_rejected(self):
        from autoapply_local.release_audit import audit_artifacts

        for kind, mode in (
            ("wheel", stat.S_IFLNK),
            ("zip", stat.S_IFLNK),
            ("sdist", tarfile.SYMTYPE),
            ("sdist", tarfile.LNKTYPE),
            ("sdist", tarfile.FIFOTYPE),
        ):
            with self.subTest(kind=kind, mode=mode):
                self.valid_set()
                path = self.artifacts / self.filename(kind)
                if kind == "sdist":
                    members = self.members(kind)
                    name = next(name for name in members if name.endswith("autoapply_local/cli.py"))
                    with tarfile.open(path, "w:gz") as archive:
                        for member, payload in members.items():
                            info = tarfile.TarInfo(member)
                            if member == name:
                                info.type = mode
                                info.linkname = "../../outside.py"
                                archive.addfile(info)
                            else:
                                info.size = len(payload)
                                archive.addfile(info, io.BytesIO(payload))
                else:
                    members = self.members(kind)
                    name = next(name for name in members if name.endswith("autoapply_local/cli.py"))
                    with zipfile.ZipFile(path, "w") as archive:
                        for member, payload in members.items():
                            info = zipfile.ZipInfo(member)
                            if member == name:
                                info.create_system = 3
                                info.external_attr = (mode | 0o777) << 16
                            archive.writestr(info, payload)
                self.assert_rejected(audit_artifacts(self.source, self.artifacts, self.files), member=name)
        self.assertFalse((self.root / "outside.py").exists())

    def test_nested_archive_is_rejected_without_opening_it(self):
        from autoapply_local.release_audit import audit_artifacts

        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr("../escape.py", "# Synthetic nested traversal\n")
        for kind in ("wheel", "sdist", "zip"):
            with self.subTest(kind=kind):
                self.valid_set()
                self.write_archive(kind, extra=[("nested.zip", nested.getvalue())])
                self.assert_rejected(audit_artifacts(self.source, self.artifacts, self.files), member="nested.zip")
        self.assertFalse((self.root / "escape.py").exists())

    def test_privacy_patterns_in_generated_metadata_still_rejected(self):
        from autoapply_local.release_audit import audit_artifacts

        for kind in ("wheel", "sdist"):
            for payload, reason in (
                (b"person@private.example", "personal contact pattern"),
                (b"Q:/PrivateWorkspace/synthetic/user/file.json", "hardcoded private workspace path"),
            ):
                with self.subTest(kind=kind, reason=reason):
                    self.valid_set()
                    members = self.members(kind)
                    name = next(name for name in members if name.endswith(("/METADATA", "/PKG-INFO")))
                    members[name] += b"Synthetic contact: " + payload + b"\n"
                    self.write_archive(kind, members)
                    self.assert_rejected(audit_artifacts(self.source, self.artifacts, self.files), reason, name)

    def test_deny_fingerprints_still_apply_to_artifact_members(self):
        from autoapply_local.release_audit import audit_artifacts

        self.valid_set()
        member = "autoapply_local/data/x.tex"
        denied = hashlib.sha256((self.source / member).read_bytes()).hexdigest()
        self.assert_rejected(
            audit_artifacts(self.source, self.artifacts, self.files, deny=(denied,)),
            "external deny fingerprint",
            member,
        )

    def test_member_size_limit_still_applies(self):
        from autoapply_local.release_audit import audit_artifacts

        self.valid_set()
        name = "autoapply_local/data/x.tex"
        members = self.members("wheel")
        members[name] = b"x" * 20_000_001
        self.write_archive("wheel", members)
        self.assert_rejected(audit_artifacts(self.source, self.artifacts, self.files), "size limit")

    def test_expected_artifact_kind_cannot_be_missing(self):
        from autoapply_local.release_audit import audit_artifacts

        self.write_archive("wheel")
        report = audit_artifacts(self.source, self.artifacts, self.files)
        self.assert_rejected(report)
        self.assertIn("sdist", str(report["violations"]).lower())

    def test_explicit_expect_can_require_zip_or_only_wheel(self):
        from autoapply_local.release_audit import audit_artifacts

        self.write_archive("wheel")
        self.assertIs(audit_artifacts(self.source, self.artifacts, self.files, expect=("wheel",))["ok"], True)
        report = audit_artifacts(self.source, self.artifacts, self.files, expect=("wheel", "zip"))
        self.assert_rejected(report)
        self.assertIn("zip", str(report["violations"]).lower())

    def test_stale_artifact_cannot_hide_beside_current_artifacts(self):
        from autoapply_local.release_audit import audit_artifacts

        for kind in ("wheel", "sdist", "zip"):
            with self.subTest(kind=kind):
                self.valid_set()
                stale = self.write_archive(kind, version="1.2.3")
                self.assert_rejected(
                    audit_artifacts(self.source, self.artifacts, self.files), "stale artifact version", stale.name
                )
                stale.unlink()

    def test_unknown_distribution_or_wheel_tag_is_rejected(self):
        from autoapply_local.release_audit import audit_artifacts

        for name in (
            f"other_project-{self.version}-py3-none-any.whl",
            f"autoapply_local-{self.version}-cp311-none-any.whl",
            "mystery.zip",
        ):
            with self.subTest(name=name):
                self.valid_set()
                unexpected = self.artifacts / name
                unexpected.write_bytes((self.artifacts / self.filename("wheel")).read_bytes())
                self.assert_rejected(audit_artifacts(self.source, self.artifacts, self.files), member=name)
                unexpected.unlink()

    def test_implementation_does_not_embed_real_project_version(self):
        current = tomllib.loads((RELEASE / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        for relative in ("scripts/check_release.py", "autoapply_local/release_audit.py"):
            with self.subTest(source=relative):
                self.assertNotIn(current, (RELEASE / relative).read_text(encoding="utf-8"))


class SourceZipCommandTests(CurrentPackageCase):
    def call_checker(self, checker, argv):
        output = io.StringIO()
        with patch.object(checker, "ROOT", self.source), redirect_stdout(output), redirect_stderr(output):
            try:
                result = checker.main(argv)
            except SystemExit as error:
                self.fail(f"Planned CLI options must be supported, got exit {error.code}: {output.getvalue()}")
        return result, output.getvalue()

    def test_source_zip_contains_exact_source_inventory_with_fixed_timestamps(self):
        checker = self.load_script("check_release")
        code, output = self.call_checker(checker, ["--source-zip", str(self.artifacts)])
        self.assertEqual(code, 0, output)
        path = self.artifacts / self.filename("zip")
        self.assertTrue(path.is_file())
        prefix = f"autoapply-local-{self.version}/"
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.namelist(), [prefix + name for name in sorted(self.files)])
            self.assertEqual(len({member.date_time for member in archive.infolist()}), 1)
            for name in self.files:
                self.assertEqual(archive.read(prefix + name), (self.source / name).read_bytes())
        second = self.root / "second output"
        second.mkdir()
        code, output = self.call_checker(checker, ["--source-zip", str(second)])
        self.assertEqual(code, 0, output)
        self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())

    def test_source_zip_refuses_overwrite_and_preserves_existing_bytes(self):
        checker = self.load_script("check_release")
        existing = self.artifacts / self.filename("zip")
        existing.write_bytes(b"Synthetic existing artifact sentinel")
        code, output = self.call_checker(checker, ["--source-zip", str(self.artifacts)])
        self.assertEqual(code, 1, output)
        self.assertEqual(existing.read_bytes(), b"Synthetic existing artifact sentinel")

    def test_source_audit_failure_prevents_zip_creation(self):
        checker = self.load_script("check_release")
        (self.source / "README.md").write_text("Synthetic contact person@private.example\n", encoding="utf-8")
        code, output = self.call_checker(checker, ["--source-zip", str(self.artifacts)])
        self.assertEqual(code, 1, output)
        self.assertEqual(list(self.artifacts.iterdir()), [])

    def test_combined_create_and_audit_happens_in_correct_order(self):
        checker = self.load_script("check_release")
        self.write_archive("wheel")
        self.write_archive("sdist")
        code, output = self.call_checker(
            checker,
            ["--artifacts", str(self.artifacts), "--expect", "wheel,sdist,zip", "--source-zip", str(self.artifacts)],
        )
        self.assertEqual(code, 0, output)
        self.assertTrue((self.artifacts / self.filename("zip")).is_file())

    def test_artifact_cli_respects_expect(self):
        checker = self.load_script("check_release")
        self.write_archive("wheel")
        code, output = self.call_checker(checker, ["--artifacts", str(self.artifacts), "--expect", "wheel"])
        self.assertEqual(code, 0, output)
        code, output = self.call_checker(checker, ["--artifacts", str(self.artifacts)])
        self.assertEqual(code, 1, output)
        self.assertIn("sdist", output.lower())


class InstallCommandTests(CurrentPackageCase):
    def setUp(self):
        super().setUp()
        self.valid_set()
        self.work = self.root / "fresh install thử ünicode"
        self.wheelhouse = self.root / "offline wheelhouse thử ünicode"
        self.wheelhouse.mkdir()

    def run_installer(self, *, work=None, fail=None, wheelhouse=True):
        installer = self.load_script("check_install")
        commands = []

        def run(argv, **kwargs):
            argv = [os.fspath(arg) for arg in argv]
            commands.append((argv, kwargs))
            code = 1 if fail and fail(argv) else 0
            result = subprocess.CompletedProcess(argv, code, stdout="{}\n", stderr="Synthetic failure" if code else "")
            if code and kwargs.get("check"):
                raise subprocess.CalledProcessError(code, argv, output=result.stdout, stderr=result.stderr)
            return result

        def create(path):
            path = Path(path)
            for directory, python, console in (
                ("Scripts", "python.exe", "autoapply-local.exe"),
                ("bin", "python", "autoapply-local"),
            ):
                scripts = path / directory
                scripts.mkdir(parents=True, exist_ok=True)
                (scripts / python).touch()
                (scripts / console).touch()

        builder = Mock()
        builder.create.side_effect = create
        output = io.StringIO()
        argv = ["--artifacts", str(self.artifacts), "--work-dir", str(work or self.work)]
        if wheelhouse:
            argv += ["--wheelhouse", str(self.wheelhouse)]
        contaminated = {
            "PYTHONPATH": str(self.source),
            "PYTHONHOME": str(self.source),
            "AUTOAPPLY_LOCAL_HOME": str(self.source / "synthetic runtime override"),
        }
        with (
            patch.object(installer, "ROOT", self.source),
            patch.object(installer.subprocess, "run", side_effect=run),
            patch.object(installer.venv, "EnvBuilder", return_value=builder) as factory,
            patch.dict(os.environ, contaminated),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            try:
                code = installer.main(argv)
            except SystemExit as error:
                code = error.code
        return code, commands, factory.call_args_list, builder.create.call_args_list, output.getvalue()

    def test_install_uses_two_fresh_venvs_and_both_final_artifacts_with_dependencies(self):
        code, calls, factories, created, output = self.run_installer()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(created), 2)
        roots = [Path(call.args[0]).resolve() for call in created]
        self.assertEqual(len(set(roots)), 2)
        for root in roots:
            self.assertTrue(root.is_relative_to(self.work.resolve()))
        self.assertTrue(factories)
        for call in factories:
            self.assertIs(call.kwargs.get("with_pip"), True)
            self.assertIs(call.kwargs.get("system_site_packages"), False)
        installs = [args for args, _ in calls if args[1:4] == ["-m", "pip", "install"]]
        self.assertTrue(installs, calls)
        artifact_installs = {}
        for kind in ("wheel", "sdist"):
            artifact = str((self.artifacts / self.filename(kind)).resolve())
            selected = [args for args in installs if artifact in args]
            self.assertEqual(len(selected), 1, installs)
            artifact_installs[kind] = selected[0]
        self.assertNotEqual(artifact_installs["wheel"][0], artifact_installs["sdist"][0])
        self.assertIn("--no-build-isolation", artifact_installs["sdist"])
        for args in installs:
            self.assertNotIn("--no-deps", args)
            self.assertIn("--no-index", args)
            self.assertIn("--find-links", args)
            index = args.index("--find-links")
            self.assertEqual(Path(args[index + 1]).resolve(), self.wheelhouse.resolve())
        sdist_python = artifact_installs["sdist"][0]
        sdist_index = installs.index(artifact_installs["sdist"])
        preceding = [arg for args in installs[:sdist_index] if args[0] == sdist_python for arg in args]
        for requirement in self.project["build-system"]["requires"]:
            self.assertIn(requirement, preceding)
        for args, kwargs in calls:
            self.assertIs(kwargs.get("shell", False), False)
            self.assertEqual(kwargs.get("encoding", "").lower().replace("-", ""), "utf8")
            self.assertGreater(kwargs.get("timeout", 0), 0)
            self.assertTrue(math.isfinite(kwargs["timeout"]))
            env = kwargs["env"]
            self.assertNotIn("PYTHONPATH", env)
            self.assertNotIn("PYTHONHOME", env)
            self.assertNotIn("AUTOAPPLY_LOCAL_HOME", env)
            self.assertEqual(env["PYTHONNOUSERSITE"], "1")
            cwd = Path(kwargs["cwd"]).resolve()
            self.assertFalse(cwd.is_relative_to(self.source.resolve()))
            self.assertTrue(cwd.is_relative_to(self.work.resolve()))
        for python in {artifact_installs["wheel"][0], artifact_installs["sdist"][0]}:
            interpreter = Path(python).resolve()
            self.assertEqual(sum(interpreter.is_relative_to(root) for root in roots), 1)
            self.assertTrue(any(args == [python, "-m", "pip", "check"] for args, _ in calls), calls)
            isolated = [args for args, _ in calls if args[0] == python and "-I" in args]
            self.assertTrue(isolated, calls)
            cli_calls = [args[4:] for args in isolated if args[1:4] == ["-I", "-m", "autoapply_local"]]
            self.assertIn(["--help"], cli_calls)
            for command in (["setup"], ["status"], ["doctor", "claude"], ["doctor", "codex"]):
                matching = [args for args in cli_calls if args[-len(command) :] == command]
                self.assertTrue(matching, (command, cli_calls))
                for args in matching:
                    self.assertEqual(args[:1], ["--user-dir"])
                    runtime = Path(args[1]).resolve()
                    self.assertTrue(runtime.is_relative_to(self.work.resolve()))
                    self.assertFalse(runtime.is_relative_to(self.source.resolve()))
        consoles = [args for args, _ in calls if Path(args[0]).name in ("autoapply-local", "autoapply-local.exe")]
        self.assertEqual(len([args for args in consoles if args[1:] == ["--help"]]), 2)
        for args in consoles:
            self.assertEqual(sum(Path(args[0]).resolve().is_relative_to(root) for root in roots), 1)

    def test_without_wheelhouse_uses_normal_dependency_resolution(self):
        code, calls, _, _, output = self.run_installer(wheelhouse=False)
        self.assertEqual(code, 0, output)
        installs = [args for args, _ in calls if args[1:4] == ["-m", "pip", "install"]]
        self.assertTrue(installs)
        for args in installs:
            self.assertNotIn("--no-deps", args)
            self.assertNotIn("--no-index", args)

    def test_invalid_artifacts_fail_before_venv_or_subprocess(self):
        (self.artifacts / self.filename("sdist")).unlink()
        code, calls, factories, created, output = self.run_installer()
        self.assertEqual(code, 1, output)
        self.assertEqual(calls, [])
        self.assertEqual(factories, [])
        self.assertEqual(created, [])
        self.assertFalse(self.work.exists())

    def test_existing_work_directory_is_preserved_without_running_anything(self):
        self.work.mkdir()
        sentinel = self.work / "synthetic sentinel.txt"
        sentinel.write_bytes(b"Preserve this pre-existing content")
        code, calls, factories, created, output = self.run_installer()
        self.assertEqual(code, 1, output)
        self.assertEqual(sentinel.read_bytes(), b"Preserve this pre-existing content")
        self.assertEqual(list(self.work.iterdir()), [sentinel])
        self.assertEqual(calls, [])
        self.assertEqual(factories, [])
        self.assertEqual(created, [])

    def test_subprocess_failure_propagates_and_stops_following_commands(self):
        for stage in ("install", "check", "probe", "console"):
            with self.subTest(stage=stage):
                work = self.root / ("failure thử ünicode " + stage)

                def fail(args):
                    if stage in ("install", "check"):
                        return args[1:4] == ["-m", "pip", stage]
                    if stage == "probe":
                        return "-I" in args
                    return Path(args[0]).name in ("autoapply-local", "autoapply-local.exe")

                code, calls, _, _, output = self.run_installer(work=work, fail=fail)
                self.assertEqual(code, 1, output)
                self.assertTrue(calls)
                self.assertTrue(fail(calls[-1][0]), "Commands continued after subprocess failure")

    def test_timeout_is_failure_without_following_subprocesses(self):
        def timeout(args):
            raise subprocess.TimeoutExpired(args, 1)

        code, calls, _, _, output = self.run_installer(fail=timeout)
        self.assertEqual(code, 1, output)
        self.assertEqual(len(calls), 1)
