"""Offline export/install regressions from autoapply-local-complete, section 7.

Giả định của người thực thi: audit also inspects ZIP/wheel/tar.gz members and
reports the offending member in violations; safe archives are supported rather
than rejecting every binary. Archive members are included in the explicit list.
Build/install smoke uses pip with --no-index/--no-deps and a temporary source
copy, so no source-tree build outputs, network access or engine launch is needed.
The plan is not edited because this turn authorizes test files only.
"""

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

from test_support import RELEASE, ReleaseCase


class ReleaseCompleteTests(ReleaseCase):
    def source(self):
        root = self.root / "source"
        root.mkdir()
        (root / "README.md").write_text("# Synthetic public release\n", encoding="utf-8")
        return root

    def report(self, root, files, allowed=None):
        return self.module("release_audit").audit(root, export_files=files, allowlist=allowed or files)

    def assert_member_rejected(self, report, member):
        self.assertIs(report["ok"], False)
        # A generic 'binary unsupported' error does not prove member inspection.
        self.assertTrue(
            any(member in str(item["path"]) for item in report["violations"]),
            f"Audit did not identify unsafe archive member {member!r}: {report['violations']}",
        )

    def archive(self, root, suffix, members):
        name = (
            "autoapply_local_demo-0.0.0-py3-none-any.whl" if suffix == ".whl" else "autoapply-local-demo-0.0.0" + suffix
        )
        path = root / name
        if suffix == ".tar.gz":
            with tarfile.open(path, "w:gz") as archive:
                for member, content in members.items():
                    data = content.encode("utf-8")
                    info = tarfile.TarInfo(member)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
        else:
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for member, content in members.items():
                    # Preserve hostile backslashes: Windows ZipInfo(str) otherwise
                    # normalizes them before the scanner can exercise this case.
                    info = zipfile.ZipInfo()
                    info.filename = info.orig_filename = member
                    archive.writestr(info, content)
        return name

    def public_members(self, suffix):
        members = {"autoapply_local/__init__.py": '"""Synthetic public package."""\n'}
        if suffix == ".whl":
            directory = "autoapply_local_demo-0.0.0.dist-info/"
            members.update(
                {
                    directory
                    + "WHEEL": "Wheel-Version: 1.0\nGenerator: synthetic\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                    directory + "METADATA": "Metadata-Version: 2.1\nName: autoapply-local-demo\nVersion: 0.0.0\n",
                    directory + "RECORD": "",
                }
            )
        if suffix == ".tar.gz":
            members = {"autoapply-local-demo-0.0.0/" + key: value for key, value in members.items()}
            members["autoapply-local-demo-0.0.0/PKG-INFO"] = (
                "Metadata-Version: 2.1\nName: autoapply-local-demo\nVersion: 0.0.0\n"
            )
        return members

    def test_safe_zip_wheel_and_sdist_are_inspected_successfully(self):
        root = self.source()
        for suffix in (".zip", ".whl", ".tar.gz"):
            with self.subTest(format=suffix):
                members = self.public_members(suffix)
                name = self.archive(root, suffix, members)
                report = self.report(root, [name], [name, *members])
                self.assertIs(report["ok"], True, report["violations"])

    def test_sdist_fixed_build_metadata_is_scanned_without_allowing_private_generated_members(self):
        root = self.source()
        member = "autoapply-local-demo-0.0.0/autoapply_local.egg-info/PKG-INFO"
        public = self.public_members(".tar.gz")
        members = {**public, member: "Metadata-Version: 2.1\nName: autoapply-local-demo\nVersion: 0.0.0\n"}
        name = self.archive(root, ".tar.gz", members)
        report = self.report(root, [name], [name, *members])
        self.assertIs(report["ok"], True, report["violations"])
        members[member] = "Synthetic private contact: person@private.example\n"
        self.archive(root, ".tar.gz", members)
        self.assert_member_rejected(self.report(root, [name], [name, *members]), member)
        members = {**public, "autoapply-local-demo-0.0.0/autoapply_local.egg-info/local-state.json": "{}"}
        self.archive(root, ".tar.gz", members)
        self.assertIs(self.report(root, [name], [name, *members])["ok"], False)

    def test_archives_reject_parent_absolute_and_windows_member_traversal(self):
        root = self.source()
        for suffix in (".zip", ".whl", ".tar.gz"):
            for member in ("../escape.py", "/absolute.py", "Q:/escape.py", "..\\escape.py"):
                with self.subTest(format=suffix, member=member):
                    members = {**self.public_members(suffix), member: "# synthetic traversal\n"}
                    name = self.archive(root, suffix, members)
                    self.assert_member_rejected(self.report(root, [name], [name, *members]), member)
        self.assertFalse((self.root / "escape.py").exists())

    def test_archives_scan_member_contents_for_contacts_and_credentials(self):
        root = self.source()
        for suffix in (".zip", ".whl", ".tar.gz"):
            for content in (
                "Synthetic private contact: person@private.example\n",
                'access_token = "synthetic-token-not-a-secret"\n',
                "-----BEGIN PRIVATE KEY-----\nSYNTHETIC-NONKEY\n-----END PRIVATE KEY-----\n",
            ):
                with self.subTest(format=suffix, content=content):
                    member = "docs/private-note.md"
                    members = {**self.public_members(suffix), member: content}
                    name = self.archive(root, suffix, members)
                    self.assert_member_rejected(self.report(root, [name], [name, *members]), member)

    def test_archives_cannot_hide_unlisted_or_private_generated_members(self):
        root = self.source()
        for suffix in (".zip", ".whl", ".tar.gz"):
            for member in (
                "unlisted-note.md",
                "user/profile.json",
                "build/generated.json",
                "tests/__pycache__/case.pyc",
            ):
                with self.subTest(format=suffix, member=member):
                    public = self.public_members(suffix)
                    name = self.archive(root, suffix, {**public, member: "synthetic private runtime content"})
                    # Unlisted entries fail the manifest; even listing a private
                    # generated path must not turn it into public source.
                    allowed = [name, *public] + ([] if member == "unlisted-note.md" else [member])
                    self.assert_member_rejected(self.report(root, [name], allowed), member)

    def test_sdist_symlink_and_hardlink_members_cannot_escape_archive(self):
        root = self.source()
        name = "synthetic-links.tar.gz"
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(kind=kind):
                with tarfile.open(root / name, "w:gz") as archive:
                    info = tarfile.TarInfo("public/link.md")
                    info.type = kind
                    info.linkname = "../../outside.md"
                    archive.addfile(info)
                self.assert_member_rejected(self.report(root, [name], [name, "public/link.md"]), "public/link.md")

    def test_zip_and_wheel_symlink_members_cannot_escape_archive(self):
        root = self.source()
        for suffix in (".zip", ".whl"):
            with self.subTest(format=suffix):
                public = self.public_members(suffix)
                name = self.archive(root, suffix, public)
                member = "public/link.md"
                info = zipfile.ZipInfo(member)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                with zipfile.ZipFile(root / name, "a") as archive:
                    archive.writestr(info, "../../outside.md")
                self.assert_member_rejected(self.report(root, [name], [name, *public, member]), member)
                self.assertFalse((self.root / "outside.md").exists())

    def test_symlink_ancestor_cannot_launder_private_tree_inside_release_root(self):
        root = self.source()
        private = root / "user"
        private.mkdir()
        (private / "profile.json").write_text('{"legal_name":"Alex Example"}', encoding="utf-8")
        link = root / "public-alias"
        try:
            link.symlink_to(private, target_is_directory=True)
        except OSError as error:
            self.skipTest("Temporary directory symlink creation unavailable on this OS: " + str(error))
        name = "public-alias/profile.json"
        report = self.report(root, [name])
        self.assertIs(report["ok"], False, "Ancestor symlinks must not hide a user directory")
        with self.assertRaises(ValueError):
            self.module("release_audit").export_release(root, self.root / "export", allowlist=[name])
        self.assertFalse((self.root / "export").exists())

    def test_symlink_ancestor_outside_release_tree_is_rejected_without_copy(self):
        root = self.source()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "sample.md").write_text("Synthetic outside content", encoding="utf-8")
        link = root / "public-alias"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest("Temporary directory symlink creation unavailable on this OS: " + str(error))
        name = "public-alias/sample.md"
        self.assertIs(self.report(root, [name])["ok"], False)
        with self.assertRaises(ValueError):
            self.module("release_audit").export_release(root, self.root / "export", allowlist=[name])
        self.assertFalse((self.root / "export").exists())

    def test_generated_js_and_html_contents_are_scanned(self):
        root = self.source()
        generated = root / "examples"
        generated.mkdir()
        for suffix in (".js", ".html"):
            with self.subTest(suffix=suffix):
                name = "examples/generated-form" + suffix
                (root / name).write_text("Synthetic private contact: person@private.example", encoding="utf-8")
                report = self.report(root, [name])
                self.assertIs(report["ok"], False, "Generated browser artifacts need content inspection")
                self.assertIn(name, str(report["violations"]))

    def test_build_and_local_runtime_outputs_are_private_even_if_allowlisted(self):
        root = self.source()
        for name in (
            "build/local-result.json",
            "dist/local-state.json",
            "tests/__pycache__/cached.pyc",
            "user/profile.json",
        ):
            with self.subTest(name=name):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Synthetic local-only output", encoding="utf-8")
                report = self.report(root, [name])
                self.assertIs(report["ok"], False)
                self.assertIn(name, str(report["violations"]))

    def test_explicit_release_manifest_covers_every_public_source_file(self):
        manifest = RELEASE / "export-allowlist.json"
        self.assertTrue(manifest.is_file(), "The release needs the explicit export manifest required by the plan")
        allowed = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertIsInstance(allowed, list)
        self.assertEqual(len(allowed), len(set(allowed)), "Export entries must be unique")
        generated = {".git", "__pycache__", "build", "dist", ".pytest_cache"}
        actual = {
            path.relative_to(RELEASE).as_posix()
            for path in RELEASE.rglob("*")
            if path.is_file()
            and not any(part in generated or part.endswith(".egg-info") for part in path.relative_to(RELEASE).parts)
        }
        self.assertEqual(set(allowed), actual, "New release source files must be in export-allowlist.json")

    def test_offline_wheel_install_retains_resources_and_runs_cli_outside_source(self):
        pyproject = RELEASE / "pyproject.toml"
        self.assertTrue(pyproject.is_file(), "Missing installable package metadata")
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        self.assertIn("build-system", project)
        self.assertIn("autoapply-local", project.get("project", {}).get("scripts", {}))
        source = self.root / "build-source"
        shutil.copytree(
            RELEASE,
            source,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                "build",
                "dist",
                "*.egg-info",
                ".git",
                ".venv",
                "user",
                "logs",
                "runs",
                "backups",
                "accounts.md",
                "secrets.local.md",
                "profile.json",
                "answers.json",
                "reviews.json",
                "tracker.xlsx",
            ),
        )
        wheel_dir = self.root / "wheels"
        target = self.root / "installed"
        env = {**os.environ, "PIP_NO_INDEX": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        built = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "pip",
                "wheel",
                "--no-index",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(wheel_dir),
                str(source),
            ],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        wheels = list(wheel_dir.glob("*.whl"))
        self.assertEqual(len(wheels), 1)
        installed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--target",
                str(target),
                str(wheels[0]),
            ],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        package = target / "autoapply_local"
        for relative in (
            "data/ola.tex",
            "data/tailor-prompt.md",
            "adapters/common.cjs",
            "adapters/greenhouse.cjs",
            "adapters/rippling.cjs",
            "adapters/workday.cjs",
        ):
            with self.subTest(resource=relative):
                self.assertEqual(
                    (package / relative).read_bytes(), (RELEASE / "autoapply_local" / relative).read_bytes()
                )
        with zipfile.ZipFile(wheels[0]) as archive:
            self.assertFalse(any(name.startswith(("tests/", "user/", "build/")) for name in archive.namelist()))
        env.update(PYTHONPATH=str(target), AUTOAPPLY_LOCAL_HOME=str(self.root / "runtime"))
        for arguments in (("--help",), ("setup",), ("status",)):
            with self.subTest(command=arguments):
                completed = subprocess.run(
                    [sys.executable, "-B", "-m", "autoapply_local", *arguments],
                    cwd=self.root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue((self.root / "runtime" / "user/profile-intake.md").is_file())
