"""Read local source documents as unconfirmed draft text; extraction never confirms facts."""

import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree


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
