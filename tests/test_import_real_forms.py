"""scripts/import_real_forms.py keeps only the structure of captured forms."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def importer():
    spec = importlib.util.spec_from_file_location("import_real_forms", ROOT / "scripts" / "import_real_forms.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPTURED = {
    "meta": {"company": "Northwind", "url": "https://boards.example.com/northwind/jobs/1", "resume": "x.pdf"},
    "fields": [
        {"id": "first_name", "label": "First Name", "kind": "text", "req": True, "val": "Alex", "max": 40},
        {"id": "email", "label": "Email", "kind": "text", "req": True, "val": "alex.rivera@example.org"},
        {"id": "q1", "label": "Why Northwind?", "kind": "textarea", "req": False, "val": "Because."},
        {"id": "q2", "label": "Will you require sponsorship?", "kind": "choice", "req": True,
         "opts": ["Yes", "No"], "val": "No", "key": "sponsor"},
        {"id": "resume", "label": "Alex_Rivera_resume.pdf uploaded", "kind": "file", "req": True},
        {"id": "q3", "label": "Contact me at alex.rivera@example.org", "kind": "text", "req": False},
        {"id": "city", "label": "Portland Oregon", "kind": "text", "req": False, "val": "Portland Oregon"},
        {"id": "q2", "label": "Will you require sponsorship?", "kind": "choice", "req": True, "opts": ["Yes", "No"]},
    ],
}
PAGE = """<html><body><form>
<input type="hidden" name="token" value="secret">
<label for="fname">First name</label><input id="fname" name="fname" required value="Alex">
<div class="application-label">Gender ✱</div>
<label><input type="radio" name="gender" value="f">Female</label>
<label><input type="radio" name="gender" value="m">Male</label>
<div class="application-label">Race</div>
<label><input type="checkbox" name="race" value="a">Asian</label>
<label><input type="checkbox" name="race" value="w">White</label>
<label><input type="checkbox" name="agree" value="1">I agree</label>
<select id="vet" name="vet" aria-required="true"><option>Select</option><option>I am a veteran</option></select>
<label for="vet">Veteran status</label>
<textarea name="h-captcha-response"></textarea>
<input type="submit" value="Submit">
</form></body></html>"""


class ImportRealForms(unittest.TestCase):
    def run_import(self, files, names=()):
        module = importer()
        source = tempfile.TemporaryDirectory()
        dest = tempfile.TemporaryDirectory()
        self.addCleanup(source.cleanup)
        self.addCleanup(dest.cleanup)
        for relative, text in files.items():
            path = Path(source.name) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        written, self.dropped = module.import_forms(source.name, Path(dest.name) / "real", names)
        return {path.relative_to(Path(dest.name) / "real").as_posix(): json.loads(path.read_text(encoding="utf-8"))
                for path in written}

    def test_json_form_keeps_only_structure_and_numbers_the_file(self):
        out = self.run_import({"gh/northwind-swe-p1.json": json.dumps(CAPTURED)}, ["Rivera"])
        self.assertEqual(list(out), ["gh/01.json"])
        self.assertEqual(self.dropped, 3)  # resume file name, email in a label, city shown as its label
        self.assertEqual(set(out["gh/01.json"]), {"fields"})
        self.assertEqual(
            out["gh/01.json"]["fields"],
            [
                {"id": "first_name", "label": "First Name", "kind": "text", "req": True, "opts": []},
                {"id": "email", "label": "Email", "kind": "text", "req": True, "opts": []},
                {"id": "q1", "label": "Why Example Co?", "kind": "textarea", "req": False, "opts": []},
                {"id": "q2", "label": "Will you require sponsorship?", "kind": "choice", "req": True,
                 "opts": ["Yes", "No"]},
            ],
        )

    def test_saved_page_reads_labels_groups_and_options_without_values(self):
        out = self.run_import({"lever/acme-page.html": PAGE})
        self.assertEqual(
            out["lever/01.json"]["fields"],
            [
                {"id": "fname", "label": "First name", "kind": "text", "req": True, "opts": []},
                {"id": "gender", "label": "Gender", "kind": "radio", "req": True, "opts": ["Female", "Male"]},
                {"id": "race", "label": "Race", "kind": "checks", "req": False, "opts": ["Asian", "White"]},
                {"id": "agree", "label": "I agree", "kind": "box", "req": False, "opts": []},
                {"id": "vet", "label": "Veteran status", "kind": "choice", "req": True,
                 "opts": ["Select", "I am a veteran"]},
            ],
        )

    def test_page_with_a_recorded_expectation_is_read_once(self):
        expect = {"meta": {"url": "https://example.com"}, "fields": [{"id": "a", "label": "City", "kind": "text", "req": False}]}
        out = self.run_import({"generic/board.html": PAGE, "generic/board.expect.json": json.dumps(expect)})
        self.assertEqual(out, {"generic/01.json": {"fields": [
            {"id": "a", "label": "City", "kind": "text", "req": False, "opts": []}]}})

    def test_ashby_entries_group_by_job_and_map_kinds(self):
        lines = [
            {"job": "j1", "entry": {"isRequired": True, "field": {"path": "u1", "title": "Need sponsorship?", "type": "Boolean"}}},
            {"job": "j1", "entry": {"isRequired": False, "field": {"path": "u2", "title": "Gender", "type": "ValueSelect"}}},
            {"job": "j2", "entry": {"isRequired": True, "field": {"path": "u3", "title": "School", "type": "EducationHistory"}}},
        ]
        out = self.run_import({"ashby/fields.jsonl": "\n".join(json.dumps(line) for line in lines)})
        self.assertEqual([item["kind"] for item in out["ashby/01.json"]["fields"]], ["radio", "choice"])
        self.assertEqual(out["ashby/02.json"]["fields"][0]["label"], "School")

    def test_files_without_a_field_list_are_skipped(self):
        out = self.run_import({"smartrecruiters/posting.json": json.dumps({"id": "1", "company": {"name": "x"}}),
                               "gh/_example-p1.json": json.dumps(CAPTURED)})
        self.assertEqual(out, {})

    def test_denylist_comes_from_the_variable_or_the_untracked_file(self):
        module = importer()
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(module.denylist({}, root), [])
            (Path(root) / ".private-names").write_text("Jordan\n", encoding="utf-8")
            self.assertEqual(module.denylist({"AUTOAPPLY_PRIVATE_NAMES": "Alex,Rivera"}, root), ["Alex", "Jordan", "Rivera"])

    def test_rerun_replaces_the_destination_and_its_allowlist_entries(self):
        module = importer()
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as source:
            (Path(source) / "gh").mkdir()
            (Path(source) / "gh" / "a.json").write_text(json.dumps(CAPTURED), encoding="utf-8")
            dest = Path(root) / "tests" / "fixtures" / "real"
            (dest / "wd").mkdir(parents=True)
            (dest / "wd" / "09.json").write_text("{}", encoding="utf-8")
            (Path(root) / "export-allowlist.json").write_text(
                json.dumps(["README.md", "tests/fixtures/real/wd/09.json"]), encoding="utf-8")
            written, _ = module.import_forms(source, dest)
            module.update_allowlist(written, root=root, dest=dest)
            self.assertFalse((dest / "wd").exists())
            self.assertEqual(json.loads((Path(root) / "export-allowlist.json").read_text(encoding="utf-8")),
                             ["README.md", "tests/fixtures/real/gh/01.json"])

    def test_missing_source_exits_2(self):
        self.assertEqual(importer().main(["--source", str(ROOT / "no-such-folder"), "--no-allowlist"]), 2)


if __name__ == "__main__":
    unittest.main()
