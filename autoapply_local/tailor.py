"""Confirmed-source tailoring with one selected engine and bounded repair.

Giả định của người thực thi: JSON fact sources have typed IDs; conservative
lexical validation refuses unsupported additions rather than guessing meaning.
Compilers/renderers are injectable; an absent visual inspector leaves needs_review.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import re
import shutil
import subprocess
import unicodedata
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree


_IMMUTABLE = ("employer", "title", "start", "end", "metric", "metrics", "dates", "location")
_LATEX = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_GRAMMAR = set("a an and as at by for from in into of on or the through to using via with was were is are".split())
_PROTECTED = set(
    "assist assists assisted assisting help helps helped helping support supports supported supporting "
    "contribute contributes contributed contributing participate participates participated participating "
    "collaborate collaborates collaborated collaborating not no never without không chưa chẳng "
    "partial partially limited only prototype experimental".split()
)
_REWRITES = {
    "build": "built",
    "building": "built",
    "developed": "built",
    "develop": "built",
    "reduction": "reduced",
    "reduce": "reduced",
    "reducing": "reduced",
    "improved": "reduced",
    "improve": "reduced",
    "implemented": "built",
    "implementation": "built",
    "services": "service",
    "apis": "api",
}


def escape_latex(value):
    """Escape in one pass, so inserted escapes cannot themselves be escaped."""
    return "".join(_LATEX.get(char, char) for char in str(value))


def _normalized(value):
    return unicodedata.normalize("NFC", str(value)).casefold()


def _same_fact(left, right):
    if isinstance(left, str) and isinstance(right, str):
        return unicodedata.normalize("NFC", left) == unicodedata.normalize("NFC", right)
    return left == right


def _words(value):
    return {
        _REWRITES.get(word, word) for word in re.findall(r"[^\W\d_](?:[^\W_]|[+#.-])*", _normalized(value))
    } - _GRAMMAR


def _ordered_words(value):
    return [
        _REWRITES.get(word, word)
        for word in re.findall(r"[^\W\d_](?:[^\W_]|[+#])*", _normalized(value))
        if word not in _GRAMMAR
    ]


def validate_claims(sources, claims):
    """Conservatively preserve confirmed source facts, not prove full semantics."""
    if not isinstance(sources, list) or not isinstance(claims, list):
        raise ValueError("Sources and claims must be arrays")
    indexed = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("id"), str):
            raise ValueError("Every source requires an ID")
        if source["id"] in indexed:
            raise ValueError("Duplicate source ID")
        indexed[source["id"]] = source
    hydrated = []
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("source_ids"), list) or not claim["source_ids"]:
            raise ValueError("Every claim needs typed source_ids")
        evidence = []
        for source_id in claim["source_ids"]:
            source = indexed.get(source_id)
            if source is None or source.get("status") != "confirmed":
                raise ValueError("Missing or unconfirmed claim evidence")
            source_type = source.get("type")
            if not source_type or not source_id.startswith(source_type + ":") or claim.get("type") != source_type:
                raise ValueError("Claim type does not match source type")
            if source.get("provenance", {}).get("kind") in (
                "generated_resume",
                "pdf_extraction",
                "document_extraction",
                "derived",
                "engine_inference",
            ):
                raise ValueError("Generated or unconfirmed extraction cannot establish facts")
            evidence.append(source)
        primary_id = claim.get("primary_source_id", claim["source_ids"][0])
        if primary_id not in claim["source_ids"]:
            raise ValueError("Primary source must be cited")
        primary = indexed[primary_id]
        entry = primary.get("entry_id", primary["id"])
        if any(source.get("entry_id", source["id"]) != entry for source in evidence):
            raise ValueError("A claim cannot combine unrelated entries")
        result = copy.deepcopy(claim)
        result["primary_source_id"] = primary_id
        for field in _IMMUTABLE:
            if field in claim:
                if field in primary and not _same_fact(primary[field], claim[field]):
                    raise ValueError("Immutable primary source fact changed: " + field)
                if not any(field in source and _same_fact(source[field], claim[field]) for source in evidence):
                    raise ValueError("Immutable source fact changed: " + field)
            elif field in primary:
                result[field] = copy.deepcopy(primary[field])
        text = claim.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Claim text must be nonempty")
        evidence_text = " ".join(str(source.get("text", "")) for source in evidence)
        evidence_protected = set(re.findall(r"[^\W\d_]+", _normalized(evidence_text))) & _PROTECTED
        claim_protected = set(re.findall(r"[^\W\d_]+", _normalized(text))) & _PROTECTED
        if evidence_protected != claim_protected:
            raise ValueError("Rewrite changed a contribution, negation or limitation qualifier")
        # Numbers and units, including numbers embedded in free text, stay bound.
        numbers = set(re.findall(r"\d+(?:[.,]\d+)*(?:%|x)?", text))
        if not numbers <= set(re.findall(r"\d+(?:[.,]\d+)*(?:%|x)?", evidence_text)):
            raise ValueError("Unsupported numeric claim")
        if not _words(text) <= _words(evidence_text):
            raise ValueError("Rewrite contains unsupported source content")
        # A bag of words cannot establish ownership: retaining lexical order is
        # deliberately conservative and prevents swapping 'used' with 'led'.
        remaining = iter(_ordered_words(evidence_text))
        if not all(any(token == observed for observed in remaining) for token in _ordered_words(text)):
            raise ValueError("Rewrite changes source attribution or ownership")
        normalized_evidence = _normalized(evidence_text)
        normalized_text = _normalized(text)
        for qualifier in ("approximately", "about", "estimated", "roughly", "up to", "at least"):
            if numbers and qualifier in normalized_evidence and qualifier not in normalized_text:
                raise ValueError("Rewrite dropped a metric qualifier")
        hydrated.append(result)
    return hydrated


def classify_keywords(keywords, sources, adjacency=None):
    adjacency = adjacency or {}
    confirmed = [source for source in sources if source.get("status") == "confirmed"]
    result = {}
    for keyword in keywords:
        expression = re.compile(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", re.I)
        direct = [source["id"] for source in confirmed if expression.search(json.dumps(source, ensure_ascii=False))]
        adjacent = []
        if not direct:
            for source in confirmed:
                text = json.dumps(source, ensure_ascii=False)
                if any(
                    re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I)
                    for term in adjacency.get(keyword, [])
                ):
                    adjacent.append(source["id"])
        result[keyword] = {
            "category": "HAVE" if direct else "ADJACENT" if adjacent else "MISSING",
            "source_ids": direct or adjacent,
        }
    return result


def extract_source(path, extractor=None):
    """Read source documents as data only; extraction never confirms facts."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if extractor:
        text = extractor(path)
    elif path.suffix.lower() == ".pdf":
        text = _tool_text(["pdftotext", "-enc", "UTF-8", "-layout", str(path), "-"], timeout=30)
        if text is None:
            raise ValueError("PDF extraction failed: pdftotext must return valid UTF-8 text within 30 seconds")
    elif path.suffix.lower() == ".docx":
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 5_000_000:
                raise ValueError("Document exceeds extraction limit")
            tree = ElementTree.fromstring(archive.read(info))
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        text = "\n".join("".join(p.itertext()) for p in tree.findall(".//w:p", namespace))
    elif path.suffix.lower() in (".tex", ".txt", ".md"):
        text = path.read_text(encoding="utf-8")
    else:
        raise ValueError("Supported sources: PDF, TeX, DOCX, text")
    return {
        "status": "draft",
        "requires_confirmation": True,
        "text": text,
        "facts": [
            {
                "id": "extraction:" + str(index),
                "text": line.strip(),
                "status": "unknown",
                "provenance": {"kind": "document_extraction", "path": str(path)},
            }
            for index, line in enumerate(text.splitlines())
            if line.strip()
        ],
    }


def _safe_component(value, fallback="resume"):
    clean = re.sub(r"[^\w -]", "_", str(value), flags=re.UNICODE).strip(" ._")[:90]
    if not clean or clean.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *("COM" + str(i) for i in range(1, 10)),
        *("LPT" + str(i) for i in range(1, 10)),
    }:
        return fallback
    return clean


def _public_jd(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("JD URL must be a public HTTPS URL without credentials")
    if parsed.hostname.lower() in ("localhost", "localhost.localdomain"):
        raise ValueError("Local JD URLs are not public")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private JD URLs are not public")
    request = Request(url, headers={"User-Agent": "autoapply-local experimental"})
    with urlopen(request, timeout=10) as response:
        if response.geturl() != url:
            raise ValueError("Import redirected JD URL explicitly")
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("JD exceeds size limit")
    text = data.decode("utf-8", errors="replace")
    return re.sub(r"<[^>]+>", " ", text)


def _tool_text(args, timeout):
    """Read tool output as strict UTF-8 bytes, failing closed on tool errors."""
    try:
        completed = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not isinstance(completed.stdout, bytes):
        return None
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


class Compiler:
    def compile(self, tex, output_dir, shell_escape=False, timeout=60):
        if shell_escape:
            raise ValueError("Shell escape is forbidden")
        tex = Path(tex).resolve()
        output_dir = Path(output_dir).resolve()
        subprocess.run(
            [
                "pdflatex",
                "-no-shell-escape",
                "-halt-on-error",
                "-interaction=nonstopmode",
                "-output-directory=" + str(output_dir),
                str(tex),
            ],
            cwd=output_dir,
            capture_output=True,
            timeout=min(timeout, 120),
            check=True,
        )
        pdf = output_dir / (tex.stem + ".pdf")
        if not pdf.is_file():
            raise ValueError("Compiler produced no PDF")
        return pdf


class Renderer:
    def render(self, pdf):
        pdf = Path(pdf)
        prefix = pdf.parent / (pdf.stem + "-page")
        subprocess.run(
            ["pdftoppm", "-r", "100", "-png", str(pdf), str(prefix)], capture_output=True, timeout=60, check=True
        )
        return sorted(pdf.parent.glob(prefix.name + "-*.png"))

    def check(self, pdf):
        info = _tool_text(["pdfinfo", "-enc", "UTF-8", str(pdf)], timeout=20)
        text = _tool_text(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], timeout=20)
        match = re.search(r"^Pages:\s+(\d+)", info, re.M) if info is not None else None
        if match is None or text is None:
            return {"page_count": 0, "text_ok": False, "layout_ok": False}
        # Rasterization and nonempty text are mechanical checks; actual visual
        # approval remains a separate inspector obligation.
        return {
            "page_count": int(match.group(1)),
            "text_ok": bool(text.strip()),
            "layout_ok": bool(match),
        }


def _value(record, key, default=""):
    value = record.get(key, default)
    if isinstance(value, dict) and "status" in value:
        return value.get("value", default) if value["status"] == "confirmed" else default
    return value


def confirmed_sources(profile):
    """Project explicit confirmations into the typed source contract.

    Explicit fact_sources remain supported. Otherwise each confirmed experience
    is its own immutable entry; extraction drafts and unknown values are omitted.
    """
    from .profile import validate_profile

    validate_profile(profile)
    if profile.get("fact_sources"):
        return [copy.deepcopy(source) for source in profile["fact_sources"] if source.get("status") == "confirmed"]

    def confirmed(record, key, default=None):
        value = record.get(key)
        if isinstance(value, dict) and value.get("status") == "confirmed":
            return value.get("value", default)
        return default

    sources = []
    for kind, records in (("experience", profile.get("experience", [])), ("project", profile.get("projects", []))):
        for record in records:
            description = confirmed(record, "description") or confirmed(record, "text")
            metrics = [
                item["value"]
                for item in record.get("metrics", [])
                if isinstance(item, dict) and item.get("status") == "confirmed"
            ]
            text = description or " ".join(str(metric) for metric in metrics)
            if not text:
                continue
            source = {
                "id": record["id"],
                "type": kind,
                "status": "confirmed",
                "text": text,
                "provenance": {"kind": "user_confirmation", "source_id": record["id"]},
            }
            for key in _IMMUTABLE:
                if key == "metrics":
                    continue
                value = confirmed(record, key)
                if value is not None:
                    source[key] = copy.deepcopy(value)
            if metrics:
                source["metrics"] = copy.deepcopy(metrics)
                numbers = re.findall(r"\d+(?:[.,]\d+)*(?:%|x)", " ".join(str(value) for value in metrics))
                if len(set(numbers)) == 1:
                    source["metric"] = numbers[0]
            sources.append(source)
    return sources


def _document(profile, claims, config):
    preamble = (Path(__file__).parent / "data/ola.tex").read_text(encoding="utf-8")
    esc = escape_latex
    contact = profile.get("contact", {})
    name = _value(contact, "preferred_name") or _value(contact, "legal_name")
    lines = [preamble, r"\begin{document}", r"\begin{center}{\Large\bfseries " + esc(name) + r"}\end{center}"]
    details = [
        _value(contact, key) for key in ("application_email", "phone", "city", "linkedin", "github", "portfolio")
    ]
    lines.append(r"\begin{center}" + " $|$ ".join(esc(value) for value in details if value) + r"\end{center}")
    sections = config.get("section_order", ["education", "experience", "project", "skills"])
    if config.get("summary"):
        summaries = [claim for claim in claims if claim.get("type") == "summary"]
        if summaries:
            lines.extend([r"\cvsection{Summary}", " ".join(esc(c["text"]) for c in summaries)])
    for section in sections:
        key = str(section).lower().rstrip("s")
        if key == "education":
            records = [
                record
                for record in profile.get("education", [])
                if record.get("id") not in config.get("excluded_entries", [])
            ]
            if records:
                lines.append(r"\cvsection{Education}")
            for record in records:
                school = _value(record, "school")
                degree = _value(record, "degree")
                major = _value(record, "major")
                graduation = _value(record, "graduation", {})
                date = graduation.get("value", "") if isinstance(graduation, dict) else graduation
                lines.extend(
                    [
                        r"\entry{" + esc(school) + "}{" + esc(date) + "}",
                        r"\rolesub{" + esc(" ".join(str(x) for x in (degree, major) if x)) + "}",
                    ]
                )
                gpa = _value(record, "gpa") or _value(profile.get("education_details", {}), "gpa")
                if config.get("gpa") and gpa:
                    lines.append("GPA: " + esc(gpa) + r"\par")
                honors = _value(record, "honors")
                if config.get("honors") and honors:
                    lines.append(esc(honors) + r"\par")
        elif key == "skill":
            skills = [
                str(item["name"])
                for item in profile.get("skills", [])
                if item.get("source_id") and item.get("evidence") in ("work", "project", "course", "learning")
            ]
            if skills:
                lines.extend([r"\cvsection{Skills}", esc(", ".join(skills))])
        else:
            selected = [claim for claim in claims if claim.get("type") == key]
            if not selected:
                continue
            lines.append(r"\cvsection{" + esc(str(section).title()) + "}")
            grouped = {}
            for claim in selected:
                grouped.setdefault(
                    (
                        claim.get("employer", claim.get("name", "")),
                        claim.get("title", ""),
                        claim.get("start", ""),
                        claim.get("end", ""),
                    ),
                    [],
                ).append(claim)
            for (employer, title, start, end), group in grouped.items():
                lines.extend(
                    [
                        r"\entry{" + esc(employer) + "}{" + esc(" -- ".join(str(x) for x in (start, end) if x)) + "}",
                        r"\rolesub{" + esc(title) + "}",
                        r"\begin{itemize}",
                    ]
                )
                lines.extend(r"\item " + esc(claim["text"]) for claim in group)
                lines.extend([r"\end{itemize}", r"\entrygap"])
    lines.append(r"\end{document}")
    return "\n".join(lines)


class Tailor:
    def __init__(self, root, engines, compiler=None, renderer=None, inspector=None, jd_loader=None):
        self.root = Path(root)
        self.engines = engines
        self.compiler = compiler if compiler is not None else Compiler()
        self.renderer = renderer if renderer is not None else Renderer()
        self.inspector = inspector
        self.jd_loader = jd_loader or _public_jd

    def run(self, jd, profile, engine, config):
        if not config.get("enabled", False):
            return {"status": "disabled"}
        if engine not in self.engines:
            raise ValueError("Selected engine is unavailable; no cross-engine fallback")
        if config.get("pages", 1) not in (1, 2):
            raise ValueError("Resume page count must be 1 or 2")
        source_text = str(jd)
        jd_text = (
            self.jd_loader(source_text)
            if source_text.startswith(("https://", "http://"))
            else Path(jd).read_text(encoding="utf-8")
        )
        scope = config.get("scope", "all")
        if scope not in ("all", "priority", "bigtech"):
            raise ValueError("Unknown tailoring scope")
        if scope != "all" and not any(
            str(employer).casefold() in jd_text.casefold() for employer in config.get("employers", [])
        ):
            return {"status": "out_of_scope"}
        sources = confirmed_sources(profile)
        if not sources:
            raise ValueError("Confirm source facts before tailoring")
        prompt = (Path(__file__).parent / "data/tailor-prompt.md").read_text(encoding="utf-8")
        task = {
            "kind": "tailor",
            "jd_text": jd_text,
            "sources": sources,
            "config": copy.deepcopy(config),
            "prompt": prompt,
            "allowed_tools": [],
            "schema": {
                "type": "object",
                "required": ["company", "role"],
                "additionalProperties": False,
                "properties": {
                    "company": {"type": "string"},
                    "role": {"type": "string"},
                    "questions": {"type": "array", "items": {"type": "string"}},
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["type", "text", "source_ids"],
                            "additionalProperties": False,
                            "properties": {
                                **{
                                    key: {"type": "string"}
                                    for key in (
                                        "id",
                                        "type",
                                        "text",
                                        "primary_source_id",
                                        "employer",
                                        "title",
                                        "start",
                                        "end",
                                        "location",
                                        "status",
                                        "name",
                                    )
                                },
                                "metric": {"type": ["string", "null"]},
                                **{
                                    key: {"type": "array", "items": {"type": "string"}}
                                    for key in ("source_ids", "metrics", "dates", "skills")
                                },
                            },
                        },
                    },
                },
            },
        }
        response = None
        for attempt in range(2):
            response = self.engines[engine].run(copy.deepcopy(task))
            try:
                if isinstance(response, dict) and response.get("questions"):
                    questions = response["questions"]
                    if not isinstance(questions, list) or not all(
                        isinstance(question, str) and question.strip() for question in questions
                    ):
                        raise ValueError("Missing-fact questions must be nonempty strings")
                    if config.get("missing_facts") == "ask":
                        return {"status": "needs_input", "questions": questions}
                if (
                    not isinstance(response, dict)
                    or not isinstance(response.get("claims"), list)
                    or not response["claims"]
                ):
                    raise ValueError("Engine must return nonempty claims")
                claims = validate_claims(sources, response["claims"])
                self._validate_permissions(sources, claims, config, profile)
                if not isinstance(response.get("company"), str) or not isinstance(response.get("role"), str):
                    raise ValueError("Engine must return company and role strings")
                break
            except ValueError as error:
                if attempt:
                    raise
                task.update({"repair": True, "errors": [str(error)], "previous": response})
        output = (
            self.root
            / "user/resumes/tailored"
            / _safe_component(response["company"], "company")
            / _safe_component(response["role"], "role")
            / uuid.uuid4().hex
        )
        output.mkdir(parents=True, exist_ok=False)
        filename = _safe_component(config.get("filename", "resume"))
        tex = output / (filename + ".tex")
        tex.write_text(_document(profile, claims, config), encoding="utf-8")
        changes = ["# Changes", "", "Confirmed source bindings:"]
        changes.extend("- " + ", ".join(claim["source_ids"]) + ": " + claim["text"] for claim in claims)
        changes.extend(["", "Generation count: " + str(attempt + 1), "Visual approval is recorded separately below."])
        (output / "changes.md").write_text("\n".join(changes) + "\n", encoding="utf-8")
        pdf = Path(self.compiler.compile(tex, output, shell_escape=False, timeout=60))
        if not pdf.resolve().is_relative_to(output.resolve()) or not pdf.is_file():
            raise ValueError("Compiler output must exist within new run directory")
        target = output / (filename + ".pdf")
        if pdf != target:
            shutil.copyfile(pdf, target)
            pdf = target
        images = self.renderer.render(pdf)
        checks = self.renderer.check(pdf)
        mechanical_ok = (
            checks.get("page_count") == config.get("pages", 1)
            and checks.get("text_ok") is True
            and checks.get("layout_ok") is True
        )
        verdict = self.inspector.inspect(images) if self.inspector is not None else {"approved": False}
        status = "approved" if mechanical_ok and verdict.get("approved") is True else "needs_review"
        with (output / "changes.md").open("a", encoding="utf-8") as stream:
            stream.write("\nStatus: " + status + "\n")
            stream.write("Mechanical checks: " + json.dumps(checks, sort_keys=True) + "\n")
        return {
            "status": status,
            "output_dir": str(output),
            "pdf": str(pdf),
            "tex": str(tex),
            "checks": checks,
            "claims": claims,
            "images": [str(image) for image in images],
        }

    @staticmethod
    def _validate_permissions(sources, claims, config, profile=None):
        selected = {source_id for claim in claims for source_id in claim["source_ids"]}
        required = set(config.get("required_entries", [])) | set(config.get("required_bullets", []))
        excluded = set(config.get("excluded_entries", [])) | set(config.get("excluded_bullets", []))
        static_entries = {
            record["id"]
            for record in (profile or {}).get("education", [])
            if record.get("id") and _value(record, "school")
        }
        if not required <= selected | (static_entries - excluded) or selected & excluded or required & excluded:
            raise ValueError("Required/excluded evidence selection violated")
        if "section_order" in config:
            visible = {str(section).lower().rstrip("s") for section in config["section_order"]}
            if config.get("summary"):
                visible.add("summary")
            if any(claim.get("type") not in visible for claim in claims):
                raise ValueError("Selected claims must have a visible configured section")
            if required & static_entries and "education" not in visible:
                raise ValueError("Required education must have a visible configured section")
        if config.get("allow_reselection") is False and selected != {source["id"] for source in sources} - excluded:
            raise ValueError("User disabled source reselection")
        if config.get("allow_rewrite") is False:
            indexed = {source["id"]: source for source in sources}
            if any(
                not any(claim["text"] == indexed[source_id].get("text") for source_id in claim["source_ids"])
                for claim in claims
            ):
                raise ValueError("User disabled rewriting")
