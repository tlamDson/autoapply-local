"""Copy the structure of captured real ATS forms into tests/fixtures/real/<ats>/.

Usage: python scripts/import_real_forms.py --source DIR [--dest DIR]

DIR holds captured forms as ``<ats>/*.json`` (a ``fields`` list), ``*.expect.json``,
``*.jsonl`` (Ashby field entries) or ``*.html`` pages. Only the ``id``, ``label``,
``kind``, ``req`` and ``opts`` of each field are kept: values, metadata, URLs and
any filled text are dropped, the employer's name in a label becomes "Example Co",
and output files are numbered so no source name is copied. A field whose text
holds an email, a phone number, a street address or a denylisted name is left
out; the denylist comes from the AUTOAPPLY_PRIVATE_NAMES variable (comma or
newline separated) or the untracked ``.private-names`` file, never from the repo.
Rerunning replaces the destination folder and its export-allowlist.json entries.
"""

import argparse
import json
import os
import re
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "tests" / "fixtures" / "real"
ATS_DIRS = ("wd", "gh", "rp", "ashby", "lever", "smartrecruiters", "icims", "generic")
KEYS = ("id", "label", "kind", "req", "opts")
EMAIL = re.compile(r"[A-Z0-9._%+-]+@(?:[A-Z0-9-]+\.)+[A-Z]{2,}", re.I)
PHONE = re.compile(r"(?<![\w+])(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\w)")
ADDRESS = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z][a-z]+\s+){1,3}(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way)\b\.?",
    re.I,
)
ASHBY_KINDS = {"LongText": "textarea", "File": "file", "Boolean": "radio", "ValueSelect": "choice",
               "MultiValueSelect": "checks", "EducationHistory": "text"}


def denylist(environ=None, root=ROOT):
    """Names that must never appear in a public fixture (the owner's, kept off the repo)."""
    environ = os.environ if environ is None else environ
    text = environ.get("AUTOAPPLY_PRIVATE_NAMES", "")
    local = Path(root) / ".private-names"
    if local.is_file():
        text += "\n" + local.read_text(encoding="utf-8")
    return sorted({item.strip() for item in re.split(r"[,\n]", text) if item.strip()})


def private_hits(text, names=()):
    """Kinds of personal data found in text: email, phone, address, name."""
    hits = [kind for kind, pattern in (("email", EMAIL), ("phone", PHONE), ("address", ADDRESS)) if pattern.search(text)]
    # Letters only around the name, so "Alex_Rivera_resume.pdf" still counts.
    if any(re.search(r"(?<![^\W\d_])" + re.escape(name) + r"(?![^\W\d_])", text, re.I) for name in names):
        hits.append("name")
    return hits


def _clean(text):
    return " ".join(str(text or "").replace("�", " ").split())


def filled(items):
    """Text that was typed or chosen on the captured form (never copied, only matched)."""
    found = set()
    for item in items:
        for key in ("val", "values", "val_submitted"):
            value = item.get(key)
            for part in value if isinstance(value, list) else [value]:
                if isinstance(part, str) and len(part.strip()) >= 4:
                    found.add(_clean(part).casefold())
    return found


def field(raw):
    """One field reduced to its structure."""
    opts = [_clean(item.get("label") if isinstance(item, dict) else item) for item in raw.get("opts") or []]
    return {
        "id": str(raw.get("id") or ""),
        "label": _clean(raw.get("label")),
        "kind": str(raw.get("kind") or "text"),
        "req": bool(raw.get("req")),
        "opts": [item for item in opts if item],
    }


class _Form(HTMLParser):
    """Form controls of a saved page: labels, kinds, required flags and option texts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.controls, self.labels, self.radios, self.pending = [], {}, {}, []
        self._label = self._option = self._select = self._legend = None
        self.legend, self.legend_req = "", False

    def handle_starttag(self, tag, attrs):
        attrs = {key: value or "" for key, value in attrs}
        if tag == "label":
            self._label = {"for": attrs.get("for", ""), "text": [], "controls": []}
        elif tag in ("input", "textarea", "select"):
            kind = attrs.get("type", "text").lower() if tag == "input" else tag
            if kind in ("hidden", "submit", "button", "reset", "image") or "captcha" in attrs.get("name", ""):
                return
            control = {
                "id": attrs.get("id") or attrs.get("name", ""),
                "name": attrs.get("name", ""),
                "kind": {"textarea": "textarea", "select": "choice", "checkbox": "box", "file": "file",
                         "radio": "radio"}.get(kind, "text"),
                "req": "required" in attrs or attrs.get("aria-required") == "true",
                "label": attrs.get("aria-label") or attrs.get("placeholder", ""),
                "opts": [],
            }
            if kind in ("radio", "checkbox") and control["name"]:
                group = self.radios.get((kind, control["name"]))
                if group is None:
                    group = {**control, "id": control["name"], "label": self.legend, "members": 0}
                    group["kind"] = "radio" if kind == "radio" else "checks"
                    self.radios[(kind, control["name"])] = group
                    self.controls.append(group)
                group["req"] = group["req"] or control["req"] or self.legend_req
                group["members"] += 1
                control["option_of"] = group
            else:
                self.controls.append(control)
            if self._label is not None:
                self._label["controls"].append(control)
            if attrs.get("id"):
                self.labels.setdefault(attrs["id"], control)
            if tag == "select":
                self._select = control
        elif tag == "option" and self._select is not None:
            self._option = []
        elif self._legend is not None and tag == self._legend["tag"]:
            self._legend["depth"] += 1
        elif self._legend is None and (tag == "legend" or (tag != "label" and re.search(
                r"label|question-title", attrs.get("class", ""), re.I))):
            self._legend = {"tag": tag, "depth": 1, "text": []}
        elif tag == "fieldset":
            self.legend, self.legend_req = "", False

    def handle_data(self, data):
        if self._option is not None:
            self._option.append(data)
            return
        if self._legend is not None:
            self._legend["text"].append(data)
        if self._label is not None:
            self._label["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "option" and self._option is not None:
            self._select["opts"].append(_clean("".join(self._option)))
            self._option = None
        elif tag == "select":
            self._select = None
        elif self._legend is not None and tag == self._legend["tag"]:
            self._legend["depth"] -= 1
            if not self._legend["depth"]:
                text = _clean("".join(self._legend["text"]))
                self.legend_req = text.endswith(("*", "✱"))
                self.legend, self._legend = text.rstrip("✱* "), None
        elif tag == "label" and self._label is not None:
            text = _clean("".join(self._label["text"]))
            if self._label["controls"]:
                for control in self._label["controls"]:
                    _name(control, text)
            else:
                self.pending.append((self._label["for"], text))
            self._label = None

    def close(self):
        super().close()
        for target, text in self.pending:  # A label may come before the control it names.
            if target in self.labels:
                _name(self.labels[target], text)


def _name(control, text):
    if "option_of" in control:
        control["option_of"]["opts"].append(text)
    elif not control["label"]:
        control["label"] = text


def html_fields(text):
    parser = _Form()
    parser.feed(text)
    parser.close()
    for control in parser.controls:
        if control.pop("members", 2) == 1 and control["kind"] == "checks":
            control.update(kind="box", label=(control["opts"] or [control["label"]])[0], opts=[])
    return [
        field({**control, "label": control["label"] or control["name"] or control["id"]})
        for control in parser.controls
        if control["id"]
    ]


def forms(path):
    """Field lists found in one captured file, keyed by an employer name to scrub."""
    name = path.name.split(".")[0]
    employer = name.split("-")[0].strip("_")
    if path.suffix == ".html":
        if path.with_name(name + ".expect.json").exists():
            return []  # The recorded expectation of the same page is read instead.
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        return [(employer, html_fields(text))]
    if path.suffix == ".jsonl":
        grouped = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            spec = entry.get("entry", {}).get("field", {})
            grouped.setdefault(entry.get("job", ""), []).append(
                field({"id": spec.get("path") or spec.get("id"), "label": spec.get("title"),
                       "kind": ASHBY_KINDS.get(spec.get("type"), "text"), "req": entry["entry"].get("isRequired")})
            )
        return [("", items) for items in grouped.values()]
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("fields"), list):
        return []
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    items = [item for item in data["fields"] if isinstance(item, dict)]
    typed = filled(items)
    kept = []
    for item in map(field, items):
        options = {option.casefold() for option in item["opts"]}
        # A label holding a filled value is the answer shown in place of the question.
        if not any(value in item["label"].casefold() and value not in options for value in typed):
            kept.append(item)
    return [(meta.get("company") or employer, kept)]


def scrub(items, employer, names):
    """Drop the employer's name from labels and leave out fields holding personal data."""
    kept, dropped, seen = [], 0, set()
    words = [word for word in {employer, employer.replace("-", " ")} if len(word) >= 3]
    for item in items:
        for word in words:
            pattern = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.I)
            item["label"] = pattern.sub("Example Co", item["label"])
            item["opts"] = [pattern.sub("Example Co", option) for option in item["opts"]]
        if not item["label"] or private_hits(" ".join([item["id"], item["label"], *item["opts"]]), names):
            dropped += 1
            continue
        if item["id"] and item["id"] in seen:
            continue  # The same control captured twice.
        seen.add(item["id"])
        kept.append({key: item[key] for key in KEYS})
    return kept, dropped


def import_forms(source, dest=DEST, names=()):
    source, dest = Path(source), Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    written, dropped = [], 0
    for ats in ATS_DIRS:
        number = 0
        for path in sorted((source / ats).glob("*")) if (source / ats).is_dir() else []:
            if path.suffix not in (".json", ".jsonl", ".html") or path.name.startswith("_"):
                continue
            for employer, items in forms(path):
                kept, lost = scrub(items, employer, names)
                dropped += lost
                if not kept:
                    continue
                number += 1
                target = dest / ats / f"{number:02d}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps({"fields": kept}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
                written.append(target)
    return written, dropped


def update_allowlist(written, root=ROOT, dest=DEST):
    path = Path(root) / "export-allowlist.json"
    prefix = Path(dest).resolve().relative_to(Path(root).resolve()).as_posix() + "/"
    allowed = [item for item in json.loads(path.read_text(encoding="utf-8")) if not item.startswith(prefix)]
    allowed += [item.resolve().relative_to(Path(root).resolve()).as_posix() for item in written]
    path.write_text(json.dumps(allowed, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help="Folder of captured <ats>/ forms")
    parser.add_argument("--dest", type=Path, default=DEST)
    parser.add_argument("--no-allowlist", action="store_true", help="Leave export-allowlist.json unchanged")
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        print(f"No such folder: {args.source}", file=sys.stderr)
        return 2
    written, dropped = import_forms(args.source, args.dest, denylist())
    if not args.no_allowlist:
        update_allowlist(written, dest=args.dest)
    print(f"Wrote {len(written)} forms to {args.dest}; left out {dropped} fields with personal data or no label")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
