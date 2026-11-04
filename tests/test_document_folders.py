"""Offline contracts for PR2's passive document drop folders.

Giả định của người thực thi:
- Setup JSON uses flat resume_dir and additional_document_dir string fields.
- Mock Path resolution/symlink metadata for escape cases on every OS; no skips.
- Keep assumptions here and report RED in chat because only this test may change.
"""

import io
import json
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from test_support import RELEASE, ReleaseCase


FOLDERS = {
    "resume_dir": "user/resume",
    "additional_document_dir": "user/additional-document",
}
DOCUMENTS = {
    "user/resume/Resume bản chính.pdf": b"%PDF-1.4\n% synthetic resume\n%%EOF\n",
    "user/resume/source bản chính.tex": b"% synthetic resume source\n",
    "user/resume/resume source.docx": b"PK\x03\x04synthetic DOCX bytes\x00\xff",
    "user/additional-document/transcript.pdf": b"%PDF-1.4\n% synthetic transcript\n%%EOF\n",
    "user/additional-document/certificate.pdf": b"%PDF-1.4\n% synthetic certificate\n%%EOF\n",
    "user/additional-document/cover letter.docx": b"PK\x03\x04synthetic letter\x00\xff",
}


def file_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


class DocumentFolderTests(ReleaseCase):
    def documents(self, root):
        for relative, payload in DOCUMENTS.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

    def assert_folder_result(self, root, result):
        for key, relative in FOLDERS.items():
            with self.subTest(field=key):
                self.assertIn(key, result, "Setup must expose both drop-folder paths to the host")
                self.assertIsInstance(result[key], str)
                path = Path(result[key])
                self.assertTrue(path.is_absolute(), result[key])
                self.assertEqual(path, (root / relative).resolve())
                self.assertTrue(path.is_dir(), result[key])
                self.assertTrue((path / "README.md").is_file())

    def assert_no_guide_writes(self, root, before):
        after = file_bytes(root)
        for name, payload in before.items():
            self.assertEqual(after.get(name), payload, f"Existing file changed: {name}")
        for relative in FOLDERS.values():
            self.assertEqual(
                {name: payload for name, payload in after.items() if name.startswith(relative + "/")},
                {name: payload for name, payload in before.items() if name.startswith(relative + "/")},
                "Safety checks must precede writing either folder guide",
            )

    def test_real_intake_setup_creates_both_folders_and_returns_absolute_paths(self):
        root = self.root / "runtime hồ sơ có spaces"
        result = self.module("intake").setup(root)
        self.assertEqual(result["status"], "ready")
        self.assert_folder_result(root, result)

    def test_guides_are_generic_and_explain_the_two_document_categories(self):
        root = self.root / "runtime hồ sơ"
        self.module("intake").setup(root)
        guides = {}
        public = self.root / "guide-audit"
        public.mkdir()
        for index, relative in enumerate(FOLDERS.values()):
            path = root / relative / "README.md"
            self.assertTrue(path.is_file(), f"Missing drop-folder guide: {relative}")
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.strip())
            self.assertNotIn(str(root), text, "Guides must not embed machine-specific runtime paths")
            guides[relative] = text
            (public / f"guide-{index}.md").write_text(text, encoding="utf-8")
        resume = guides["user/resume"].casefold()
        self.assertIn("pdf", resume)
        self.assertRegex(resume, r"source|tex|nguồn")
        additional = guides["user/additional-document"].casefold()
        self.assertRegex(additional, r"transcript|bảng điểm")
        self.assertRegex(additional, r"certificate|chứng chỉ")
        self.assertRegex(additional, r"cover\s*letter|thư xin việc")
        names = ["guide-0.md", "guide-1.md"]
        report = self.module("release_audit").audit(public, names, names)
        self.assertIs(report["ok"], True, report["violations"])

    def test_preexisting_documents_and_all_user_edits_survive_repeated_setup(self):
        root = self.root / "existing documents"
        self.documents(root)
        for relative in FOLDERS.values():
            (root / relative / "README.md").write_bytes(b"User-edited guide\r\n\x00\xff")
        intake = self.module("intake")
        intake.setup(root)
        for relative, payload in DOCUMENTS.items():
            self.assertEqual((root / relative).read_bytes(), payload)
        for relative in FOLDERS.values():
            self.assertEqual((root / relative / "README.md").read_bytes(), b"User-edited guide\r\n\x00\xff")
        (root / "user/profile-intake.md").write_text("User-owned intake edit", encoding="utf-8")
        before = file_bytes(root)
        for _ in range(2):
            intake.setup(root)
        self.assertEqual(file_bytes(root), before, "Repeat setup must not overwrite, rename or copy existing files")
        for relative in FOLDERS.values():
            self.assertEqual(
                {path.name for path in (root / relative).iterdir()},
                {PurePosixPath(name).name for name in before if PurePosixPath(name).parent.as_posix() == relative},
            )

    def test_generated_guides_can_be_edited_without_being_replaced(self):
        root = self.root / "edited guides"
        intake = self.module("intake")
        intake.setup(root)
        for relative in FOLDERS.values():
            guide = root / relative / "README.md"
            self.assertTrue(guide.is_file(), f"Setup must create {relative}/README.md")
            guide.write_text("My instructions / hướng dẫn riêng\n", encoding="utf-8")
        before = file_bytes(root)
        self.module("runtime").setup(root=root)
        after_first_runtime_setup = file_bytes(root)
        self.module("runtime").setup(root=root)
        self.assertEqual(file_bytes(root), after_first_runtime_setup)
        for name, payload in before.items():
            self.assertEqual((root / name).read_bytes(), payload, name)

    def test_adding_documents_does_not_select_extract_confirm_or_change_authority(self):
        root = self.root / "passive storage"
        runtime = self.module("runtime")
        runtime.setup(root=root)
        authority = ("profile.json", "config.json", "answers.json", "user/profile-intake.md")
        before = {name: (root / name).read_bytes() for name in authority}
        self.documents(root)
        with (
            patch("subprocess.run", side_effect=AssertionError("Setup must not start external tools")),
            patch("socket.create_connection", side_effect=AssertionError("Setup must stay offline")),
            patch("autoapply_local.tailor.extract_source") as extract,
            patch("autoapply_local.intake.apply_import") as confirm,
            patch("autoapply_local.intake.update_profile") as update,
            patch("autoapply_local.tailor.Tailor") as tailor,
            patch("autoapply_local.browser.launch_chrome") as chrome,
        ):
            runtime.setup(root=root)
        for boundary in (extract, confirm, update, tailor, chrome):
            boundary.assert_not_called()
        for name, payload in before.items():
            self.assertEqual((root / name).read_bytes(), payload, name)
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
        self.assertFalse(config.get("resumes", {}).get("main_pdf"))
        self.assertFalse(config.get("tailor", {}).get("enabled"))
        with self.assertRaisesRegex(ValueError, "main PDF|selected|Select"):
            runtime.select_resume(profile, config, {"role": "backend"})
        self.assertFalse((root / "user/drafts").exists(), "Dropping files must not extract facts")
        for relative, payload in DOCUMENTS.items():
            self.assertEqual((root / relative).read_bytes(), payload)

    def test_explicit_absolute_pdf_selection_needs_no_latex_or_other_tools(self):
        root = self.root / "PDF only"
        self.documents(root)
        pdf = (root / "user/resume/Resume bản chính.pdf").resolve()
        runtime = self.module("runtime")
        with (
            patch("subprocess.run", side_effect=AssertionError("PDF setup/selection requires no compiler")),
            patch("shutil.which", return_value=None),
        ):
            runtime.setup(root=root)
            config = json.loads((root / "config.json").read_text(encoding="utf-8"))
            config["resumes"]["main_pdf"] = str(pdf)
            self.assertEqual(runtime.select_resume({}, config, {"role": "backend"}), pdf)

    def test_cli_real_setup_json_exposes_paths_and_keeps_preview_confirmation_boundary(self):
        root = self.root / "CLI hồ sơ spaces"
        output, errors = io.StringIO(), io.StringIO()
        code = self.module("cli").main(
            ["--user-dir", str(root), "setup"], stdout=output, stderr=errors
        )
        self.assertEqual(code, 0, errors.getvalue())
        self.assertEqual(errors.getvalue(), "")
        result = json.loads(output.getvalue())
        self.assert_folder_result(root, result)
        for relative in FOLDERS.values():
            self.assertIn(str((root / relative).resolve()), result["next"])
        self.assertIn("preview", result["next"].casefold())
        self.assertIn("confirm", result["next"].casefold())

    def test_folder_files_and_guide_directories_are_rejected_before_any_guide_write(self):
        for index, relative in enumerate(("user", *FOLDERS.values(), *(name + "/README.md" for name in FOLDERS.values()))):
            with self.subTest(conflict=relative):
                root = self.root / f"conflict-{index}"
                conflict = root / relative
                conflict.parent.mkdir(parents=True, exist_ok=True)
                if relative.endswith("README.md"):
                    conflict.mkdir()
                    (conflict / "keep.txt").write_bytes(b"synthetic nested data")
                else:
                    conflict.write_bytes(b"synthetic file must survive\x00\xff")
                before = file_bytes(root)
                with self.assertRaisesRegex((ValueError, OSError), r"(?i)user|resume|additional-document|README"):
                    self.module("runtime").setup(root=root)
                self.assert_no_guide_writes(root, before)
                self.assertEqual(conflict.is_dir(), relative.endswith("README.md"))

    def test_mocked_symlink_escapes_are_rejected_before_writing_either_guide(self):
        # Simulate both metadata and resolution, including descendants of a
        # symlinked ancestor. No OS privileges or skip are needed on Windows.
        original_resolve, original_is_symlink = Path.resolve, Path.is_symlink
        for index, relative in enumerate(("user", *FOLDERS.values(), *(name + "/README.md" for name in FOLDERS.values()))):
            with self.subTest(escape=relative):
                root = self.root / f"escape-{index}"
                outside = self.root / f"outside-{index}"
                outside.mkdir()
                (outside / "keep.txt").write_bytes(b"synthetic outside marker")
                link = root / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                if relative.endswith("README.md"):
                    link.write_text("Synthetic linked guide", encoding="utf-8")
                    target = outside / "guide.md"
                    target.write_text("Synthetic outside guide", encoding="utf-8")
                else:
                    link.mkdir()
                    target = outside
                before, outside_before = file_bytes(root), file_bytes(outside)

                def resolve(path, *args, **kwargs):
                    absolute = original_resolve(path, *args, **kwargs)
                    if absolute.is_relative_to(link):
                        return target / absolute.relative_to(link)
                    return absolute

                def is_symlink(path):
                    return path == link or original_is_symlink(path)

                with patch.object(Path, "resolve", resolve), patch.object(Path, "is_symlink", is_symlink):
                    with self.assertRaisesRegex(ValueError, r"(?i)symlink|escape|outside|runtime"):
                        self.module("runtime").setup(root=root)
                self.assertEqual(file_bytes(outside), outside_before)
                self.assert_no_guide_writes(root, before)


class DocumentFolderExportTests(ReleaseCase):
    def test_copied_source_export_and_artifact_inventory_exclude_runtime_documents(self):
        allowed = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
        audit = self.module("release_audit")
        source = self.root / "synthetic copied source"
        source.mkdir()
        for name in allowed:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(RELEASE / name, target)
        self.module("intake").setup(source / ".autoapply-local")
        for prefix in (source, source / ".autoapply-local"):
            for relative, payload in DOCUMENTS.items():
                target = prefix / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            for relative in FOLDERS.values():
                guide = prefix / relative / "README.md"
                if not guide.exists():
                    guide.write_text("Synthetic runtime-only guide", encoding="utf-8")
        self.assertFalse(any("user" in PurePosixPath(name).parts for name in allowed))
        destination = self.root / "clean export"
        exported = audit.export_release(source, destination, allowed)
        self.assertEqual(set(exported), set(allowed))
        self.assertEqual(set(file_bytes(destination)), set(allowed))
        for kind in ("zip", "wheel", "sdist"):
            with self.subTest(artifact=kind):
                inventory = audit.artifact_inventory(source, kind, allowed)
                self.assertFalse(any("user" in PurePosixPath(name).parts for name in inventory))
                self.assertFalse(any(".autoapply-local" in PurePosixPath(name).parts for name in inventory))
        # A future accidental manifest entry must still not authorize user data.
        for name in (*DOCUMENTS, *(relative + "/README.md" for relative in FOLDERS.values())):
            with self.subTest(private_entry=name):
                report = audit.audit(source, [name], [name])
                self.assertIs(report["ok"], False)
                self.assertTrue(any(item["path"] == name for item in report["violations"]))

    def test_git_ignore_excludes_document_folders_and_runtime_guides(self):
        source = self.root / "synthetic git source"
        source.mkdir()
        shutil.copyfile(RELEASE / ".gitignore", source / ".gitignore")
        subprocess.run(["git", "init", "--quiet", str(source)], check=True, capture_output=True, timeout=10)
        paths = [*DOCUMENTS, *(relative + "/README.md" for relative in FOLDERS.values())]
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-z", "--stdin"],
            input=("\0".join(paths) + "\0").encode("utf-8"),
            cwd=source,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.decode("utf-8").rstrip("\0").split("\0")), set(paths))
