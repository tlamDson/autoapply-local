"""Export guard: tests/fixtures/real holds the structure of real forms, never a person's data.

Names to refuse come from AUTOAPPLY_PRIVATE_NAMES (comma or newline separated)
or the untracked .private-names file at the repo root, so the owner's own
facts are checked locally and never committed.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "tests" / "fixtures" / "real"
FIELD_KEYS = {"id", "label", "kind", "req", "opts"}
EMAIL = re.compile(r"[A-Z0-9._%+-]+@(?:[A-Z0-9-]+\.)+[A-Z]{2,}", re.I)
PHONE = re.compile(r"(?<![\w+])(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\w)")
ADDRESS = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z][a-z]+\s+){1,3}(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way)\b",
    re.I,
)


def private_names(environ, root):
    text = environ.get("AUTOAPPLY_PRIVATE_NAMES", "")
    local = Path(root) / ".private-names"
    if local.is_file():
        text += "\n" + local.read_text(encoding="utf-8")
    return {item.strip() for item in re.split(r"[,\n]", text) if item.strip()}


def violations(folder, names=()):
    """Every value field or piece of personal data found under folder."""
    found = []
    for path in sorted(Path(folder).rglob("*")):
        if not path.is_file():
            continue
        where = path.relative_to(folder).as_posix()
        if path.suffix != ".json":
            found.append((where, "not a json form"))
            continue
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict) or set(data) != {"fields"}:
            found.append((where, "top level must be only fields"))
        for item in data.get("fields", []) if isinstance(data, dict) else []:
            extra = set(item) - FIELD_KEYS
            if extra:
                found.append((where, "value field " + ",".join(sorted(extra))))
        for kind, pattern in (("email", EMAIL), ("phone", PHONE), ("address", ADDRESS)):
            if pattern.search(text):
                found.append((where, kind))
        for name in names:
            if re.search(r"(?<![^\W\d_])" + re.escape(name) + r"(?![^\W\d_])", text, re.I):
                found.append((where, "denylisted name"))
    return found


class GuardCatchesPersonalData(unittest.TestCase):
    def write(self, folder, fields, extra=None):
        path = Path(folder) / "gh" / "01.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fields": fields, **(extra or {})}), encoding="utf-8")

    def field(self, **changes):
        return {"id": "q1", "label": "Why us?", "kind": "text", "req": False, "opts": [], **changes}

    def test_clean_structure_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            self.write(folder, [self.field(), self.field(id="phone", label="Phone number", opts=["+1"])])
            self.assertEqual(violations(folder, {"Alex Rivera"}), [])

    def test_each_kind_of_leak_is_reported(self):
        cases = {
            "value field val": [self.field(val="typed answer")],
            "email": [self.field(label="Send to alex.rivera@example.org")],
            "phone": [self.field(opts=["(555) 010-2030"])],
            "address": [self.field(label="We are at 42 Maple Street")],
            "denylisted name": [self.field(kind="file", label="Alex_Rivera_resume.pdf uploaded")],
        }
        for reason, fields in cases.items():
            with self.subTest(reason), tempfile.TemporaryDirectory() as folder:
                self.write(folder, fields)
                self.assertIn(("gh/01.json", reason), violations(folder, {"Rivera"}))

    def test_metadata_beside_fields_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            self.write(folder, [self.field()], {"meta": {"url": "https://jobs.example.com/1"}})
            self.assertEqual(violations(folder), [("gh/01.json", "top level must be only fields")])

    def test_names_come_from_the_variable_and_the_untracked_file(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / ".private-names").write_text("Jordan Lee\n", encoding="utf-8")
            self.assertEqual(private_names({"AUTOAPPLY_PRIVATE_NAMES": "Alex, Rivera"}, root),
                             {"Alex", "Rivera", "Jordan Lee"})
        self.assertIn(".private-names", (ROOT / ".gitignore").read_text(encoding="utf-8").split())


class RealFormsArePublic(unittest.TestCase):
    def test_real_forms_exist_for_each_captured_ats(self):
        present = {path.name for path in REAL.iterdir() if path.is_dir()} if REAL.is_dir() else set()
        self.assertTrue({"wd", "gh", "rp", "ashby", "lever", "icims", "generic"} <= present, sorted(present))

    def test_real_forms_hold_no_value_or_personal_data(self):
        self.assertTrue(REAL.is_dir(), "tests/fixtures/real is missing")
        self.assertEqual(violations(REAL, private_names(os.environ, ROOT)), [])

    def test_every_real_form_is_in_the_export_allowlist(self):
        allowed = set(json.loads((ROOT / "export-allowlist.json").read_text(encoding="utf-8")))
        files = {path.relative_to(ROOT).as_posix() for path in REAL.rglob("*") if path.is_file()}
        self.assertTrue(files)
        self.assertEqual(sorted(files - allowed), [])
        self.assertEqual(sorted(item for item in allowed if item.startswith("tests/fixtures/real/") and item not in files), [])
        self.assertTrue("scripts/import_real_forms.py" in allowed, "importer missing from the allowlist")


if __name__ == "__main__":
    unittest.main()
