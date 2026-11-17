"""Offline explicit-list release export with content inspection.

Giả định của người thực thi: public examples use reserved example.invalid domains;
source-code negative tests may use other reserved .example domains and explicitly
synthetic workspace paths. Ordinary documents/data with those patterns still fail.
Fingerprints are external SHA256 hashes, never embedded personal identifiers.
"""

import base64
import configparser
import csv
import fnmatch
import hashlib
import io
import os
import re
import shutil
import stat
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath, PureWindowsPath


_PRIVATE_PARTS = {
    "user", "logs", "runs", "backups", "credentials", "accounts", ".git", "__pycache__", "build", "dist",
    ".autoapply-local",
}
_PRIVATE_NAMES = {"accounts.md", "secrets.local.md", "profile.json", "answers.json", "reviews.json", "tracker.xlsx"}
_BINARY_OUTPUTS = {".pdf", ".docx", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".gif", ".pyc"}
_EMAIL = re.compile(r"[A-Z0-9._%+-]+@(?:[A-Z0-9-]+\.)+[A-Z]{2,}", re.IGNORECASE)
_WORKSPACE = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]:[\\/]+[^\s\"'<>]+|/(?:Users|home)/[^/\s\"']+/[^\s\"'<>]*", re.IGNORECASE
)
_CREDENTIAL = re.compile(
    r"(?:password|api[_-]?key|access[_-]?token|refresh[_-]?token)\s*[=:]\s*[\"']([^\"'\n]{8,})[\"']", re.IGNORECASE
)


def _relative(root, entry):
    if not isinstance(entry, str) or not entry or "\\" in entry:
        raise ValueError("Export entries must be relative POSIX file paths")
    pure = PurePosixPath(entry)
    if pure.is_absolute() or PureWindowsPath(entry).is_absolute() or ".." in pure.parts:
        raise ValueError("Absolute paths and parent traversal are prohibited")
    if pure.as_posix() != entry or "." in pure.parts:
        raise ValueError("Export paths must be normalized")
    path = root / pure
    if not path.resolve().is_relative_to(root) or any(
        parent.is_symlink() for parent in (path, *path.parents) if parent != root and root in parent.parents
    ):
        raise ValueError("Export path escapes the release tree or uses a symlink")
    return path, pure


def _private(pure):
    parts = {part.lower() for part in pure.parts}
    name = pure.name.lower()
    if parts & _PRIVATE_PARTS or "fixtures" in parts or any(part.endswith(".egg-info") for part in parts):
        return True
    if name in _PRIVATE_NAMES and (not pure.parts or pure.parts[0] != "examples"):
        return True
    if "secret" in name or name.startswith(".env") or name.endswith(".review.md"):
        return True
    return pure.suffix.lower() in _BINARY_OUTPUTS


def _content_violations(text, pure):
    reasons = []
    source = pure.suffix.lower() in {".py", ".js", ".cjs"} and pure.parts[0] != "examples"
    for match in _EMAIL.finditer(text):
        address = match.group(0)
        domain = address.rsplit("@", 1)[1].lower()
        if domain.endswith(".invalid") or domain in {"example.com", "example.org", "example.net"}:
            continue
        if source and domain.endswith(".example"):
            continue  # Reserved negative-test domains cannot be a real inbox.
        reasons.append("personal contact pattern")
        break
    for match in _WORKSPACE.finditer(text):
        value = match.group(0)
        if source and ("synthetic" in value.lower() or "privateworkspace" in value.lower()):
            continue
        if source and len(PureWindowsPath(value).parts) == 2 and PureWindowsPath(value).drive:
            # A root-level archive traversal specimen has no user/workspace
            # directory to disclose; nested absolute workspace paths still fail.
            continue
        reasons.append("hardcoded private workspace path")
        break
    credentials = [
        match for match in _CREDENTIAL.finditer(text) if not (source and match.group(1).startswith("synthetic-"))
    ]
    if credentials or re.search(r"(?m)^-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----", text):
        reasons.append("credential-like material")
    return reasons


def _member_path(entry):
    pure = PurePosixPath(entry)
    if (
        not entry
        or "\\" in entry
        or pure.is_absolute()
        or PureWindowsPath(entry).drive
        or ".." in pure.parts
        or pure.as_posix() != entry
    ):
        raise ValueError("Unsafe archive member path")
    return pure


def _archive_violations(payload, entry, permitted, denied, members=None):
    """Inspect bounded bytes in place; never extract archive members."""
    violations = []
    total = 0
    seen = set()

    def inspect(name, data, regular=True):
        nonlocal total
        label = entry + "!" + name

        def reject(reason):
            violations.append({"path": label, "reason": reason})

        try:
            pure = _member_path(name)
        except ValueError as error:
            reject(str(error))
            return
        if name in seen:
            reject("duplicate archive member")
        seen.add(name)
        if name not in permitted:
            reject("archive member not on explicit allowlist")
        if not regular:
            reject("archive links and special members prohibited")
            return
        if name.endswith((".zip", ".whl", ".tar.gz")):
            reject("nested archives prohibited")
        metadata = (
            entry.endswith(".tar.gz")
            and len(pure.parts) == 3
            and pure.parts[-2] == "autoapply_local.egg-info"
            and pure.name
            in {"PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt", "requires.txt", "top_level.txt"}
            and name in permitted
        )
        if _private(pure) and not metadata:
            reject("private or generated archive member")
        total += len(data)
        if total > 100_000_000:
            raise ValueError("Archive exceeds inspection size limit")
        if hashlib.sha256(data).hexdigest() in denied:
            reject("external deny fingerprint")
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            reject("non-text archive member requires explicit safe handling")
            return
        for reason in _content_violations(content, pure):
            reject(reason)
        if members is not None:
            members[name] = data

    try:
        if entry.endswith((".zip", ".whl")):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        _member_path(member.filename.rstrip("/"))
                        continue
                    if member.file_size > 20_000_000:
                        raise ValueError("Archive member exceeds inspection size limit")
                    mode = member.external_attr >> 16
                    inspect(member.orig_filename, archive.read(member), stat.S_IFMT(mode) in (0, stat.S_IFREG))
        else:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                for member in archive:
                    if member.isdir():
                        _member_path(member.name.rstrip("/"))
                        continue
                    if member.size > 20_000_000:
                        raise ValueError("Archive member exceeds inspection size limit")
                    stream = archive.extractfile(member) if member.isfile() else None
                    inspect(member.name, stream.read() if stream else b"", member.isfile())
    except (ValueError, OSError, tarfile.TarError, zipfile.BadZipFile, RuntimeError) as error:
        violations.append({"path": entry, "reason": "Archive inspection failed: " + str(error)})
    return violations


def project_metadata(root):
    """Read the current identity, requirements and resource globs from TOML."""
    with (Path(root) / "pyproject.toml").open("rb") as handle:
        metadata = tomllib.load(handle)
    project = metadata["project"]
    return {
        "name": project["name"],
        "version": project["version"],
        "dependencies": project.get("dependencies", []),
        "package_data": metadata.get("tool", {})
        .get("setuptools", {})
        .get("package-data", {})
        .get("autoapply_local", []),
        "build_requires": metadata.get("build-system", {}).get("requires", []),
    }


def artifact_identity(name, version):
    """Accept only the three canonical filenames for the current version."""
    patterns = {
        "wheel": r"autoapply_local-([A-Za-z0-9.]+)-py3-none-any\.whl",
        "sdist": r"autoapply_local-([A-Za-z0-9.]+)\.tar\.gz",
        "zip": r"autoapply-local-([A-Za-z0-9.]+)\.zip",
    }
    for kind, pattern in patterns.items():
        match = re.fullmatch(pattern, name)
        if match:
            if match[1] != version:
                raise ValueError(f"stale artifact version {match[1]}; expected {version}")
            return kind
    raise ValueError("Unexpected artifact distribution name or tag")


def artifact_inventory(root, kind, source_files, version=None):
    """Return member -> source path (or None for generated metadata)."""
    metadata = project_metadata(root)
    version = version or metadata["version"]
    if kind == "zip":
        return {f"autoapply-local-{version}/{name}": name for name in source_files}
    if kind == "sdist":
        inventory = {name: name for name in source_files if not name.startswith("tests/")}
        generated = ["PKG-INFO", "setup.cfg"] + [
            "autoapply_local.egg-info/" + name
            for name in (
                "PKG-INFO",
                "SOURCES.txt",
                "dependency_links.txt",
                "entry_points.txt",
                "requires.txt",
                "top_level.txt",
            )
        ]
        inventory.update(dict.fromkeys(generated))
        return {f"autoapply_local-{version}/{name}": source for name, source in inventory.items()}
    if kind != "wheel":
        raise ValueError("Unknown artifact kind")
    inventory = {}
    for name in source_files:
        if not name.startswith("autoapply_local/"):
            continue
        relative = name.removeprefix("autoapply_local/")
        if (
            "tests" in PurePosixPath(relative).parts
            or "__pycache__" in PurePosixPath(relative).parts
            or relative.endswith(".pyc")
        ):
            continue
        if ("/" not in relative and relative.endswith(".py")) or any(
            fnmatch.fnmatchcase(relative, glob) for glob in metadata["package_data"]
        ):
            inventory[name] = name
    prefix = f"autoapply_local-{version}.dist-info/"
    inventory.update(
        {prefix + name: None for name in ("METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt")}
    )
    inventory.update({prefix + "licenses/" + name: name for name in ("LICENSE", "NOTICE")})
    return inventory


def _requirements(values):
    return sorted(re.sub(r"\s+", "", value) for value in values)


def _record_error(name, text, members):
    rows = list(csv.reader(io.StringIO(text), strict=True))
    if any(len(row) != 3 for row in rows):
        return "invalid RECORD columns"
    names = [row[0] for row in rows]
    if len(names) != len(set(names)) or set(names) != set(members):
        return "RECORD must list exactly every member once"
    for member, digest, size in rows:
        if member == name:
            if digest or size:
                return "RECORD self row must have empty hash and size"
        else:
            expected = base64.urlsafe_b64encode(hashlib.sha256(members[member]).digest()).rstrip(b"=").decode()
            if digest != "sha256=" + expected or size != str(len(members[member])):
                return "RECORD hash or size differs from member"
    return None


def _generated_metadata_error(name, data, members, inventory, metadata):
    """Validate generated packaging text without trusting its filenames alone."""
    leaf = PurePosixPath(name).name
    text = data.decode("utf-8-sig")
    if leaf in {"METADATA", "PKG-INFO"}:
        headers = BytesParser().parsebytes(data)
        for key, expected in (("Name", metadata["name"]), ("Version", metadata["version"])):
            if headers.get_all(key, []) != [expected]:
                return f"metadata {key} differs from project"
        if _requirements(headers.get_all("Requires-Dist", [])) != _requirements(metadata["dependencies"]):
            return "metadata Requires-Dist differs from project dependencies"
    elif leaf == "WHEEL":
        headers = BytesParser().parsebytes(data)
        if headers.get_all("Root-Is-Purelib", []) != ["true"] or headers.get_all("Tag", []) != ["py3-none-any"]:
            return "invalid pure Python wheel metadata"
    elif leaf == "RECORD":
        return _record_error(name, text, members)
    elif leaf in {"entry_points.txt", "setup.cfg"}:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(text)
        if parser.defaults():
            return "unexpected metadata defaults"
        expected = (
            {"console_scripts": {"autoapply-local": "autoapply_local.cli:main"}}
            if leaf == "entry_points.txt"
            else {"egg_info": {"tag_build": "", "tag_date": "0"}}
        )
        if {section: dict(parser[section]) for section in parser.sections()} != expected:
            return "unexpected " + leaf + " contents"
    elif leaf == "top_level.txt" and text.strip() != "autoapply_local":
        return "unexpected top-level package"
    elif leaf == "requires.txt" and _requirements(text.splitlines()) != _requirements(metadata["dependencies"]):
        return "requires.txt differs from project dependencies"
    elif leaf == "dependency_links.txt" and text.strip():
        return "unexpected dependency links"
    elif leaf == "SOURCES.txt":
        prefix = f"autoapply_local-{metadata['version']}/"
        allowed = {member.removeprefix(prefix) for member in inventory}
        seen = set()
        for line in text.splitlines():
            _member_path(line)
            if line not in allowed:
                return "SOURCES.txt path not on explicit allowlist"
            if line in seen:
                return "SOURCES.txt duplicate path: " + line
            seen.add(line)
        required = allowed - {"PKG-INFO", "setup.cfg"}
        missing = required - seen
        if missing:
            return "SOURCES.txt missing required members: " + ", ".join(sorted(missing))
    return None


def audit_artifacts(root, artifact_dir, source_files, expect=("wheel", "sdist"), deny=()):
    """Inspect final archives in place against this source's exact inventory."""
    root, artifact_dir = Path(root).resolve(), Path(artifact_dir).resolve()
    metadata = project_metadata(root)
    violations, found = [], set()
    files = sorted(artifact_dir.iterdir()) if artifact_dir.is_dir() else []
    for artifact in files:
        if not artifact.is_file():
            continue

        def reject(member, reason):
            violations.append({"path": artifact.name + ("!" + member if member else ""), "reason": reason})

        try:
            kind = artifact_identity(artifact.name, metadata["version"])
            found.add(kind)
            inventory = artifact_inventory(root, kind, source_files)
            members = {}
            if artifact.is_symlink():
                raise ValueError("Artifact symlinks prohibited")
            payload = artifact.read_bytes()
            if hashlib.sha256(payload).hexdigest() in deny:
                reject("", "external deny fingerprint")
            violations.extend(_archive_violations(payload, artifact.name, set(inventory), set(deny), members))
            for missing in sorted(set(inventory) - set(members)):
                reject(missing, "missing required member")
            for member, data in members.items():
                if member not in inventory:
                    continue
                source = inventory[member]
                if source is not None:
                    path, _ = _relative(root, source)
                    if not path.is_file() or data != path.read_bytes():
                        reject(member, "source payload differs")
                    continue
                try:
                    error = _generated_metadata_error(member, data, members, inventory, metadata)
                    if error:
                        reject(member, error)
                except (ValueError, UnicodeError, csv.Error, configparser.Error) as error:
                    reject(member, "invalid generated metadata: " + str(error))
        except (ValueError, OSError) as error:
            reject("", str(error))
    for kind in sorted(set(expect) - found):
        violations.append({"path": str(artifact_dir), "reason": "missing expected artifact kind: " + kind})
    return {"ok": not violations, "violations": violations, "files_checked": len(files)}


def audit(root, export_files, allowlist, deny_fingerprints=()):
    root = Path(root).resolve()
    permitted = set(allowlist)
    denied = set(deny_fingerprints)
    violations = []
    seen = set()
    for entry in export_files:
        try:
            path, pure = _relative(root, entry)
        except (ValueError, TypeError) as error:
            violations.append({"path": str(entry), "reason": str(error)})
            continue
        if entry in seen:
            violations.append({"path": entry, "reason": "duplicate export path"})
            continue
        seen.add(entry)
        if entry not in permitted:
            violations.append({"path": entry, "reason": "not on explicit allowlist"})
        if _private(pure):
            violations.append({"path": entry, "reason": "private or generated data path"})
        if not path.is_file():
            violations.append({"path": entry, "reason": "missing export file"})
            continue
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() in denied:
            violations.append({"path": entry, "reason": "external deny fingerprint"})
        if entry.endswith((".zip", ".whl", ".tar.gz")):
            violations.extend(_archive_violations(payload, entry, permitted, denied))
            continue
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            violations.append({"path": entry, "reason": "non-text release file requires explicit safe handling"})
            continue
        for reason in _content_violations(text, pure):
            violations.append({"path": entry, "reason": reason})
    return {"ok": not violations, "violations": violations, "files_checked": len(seen)}


def export_release(root, destination, allowlist, deny_fingerprints=()):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    paths = list(allowlist)
    report = audit(root, paths, paths, deny_fingerprints)
    if not report["ok"]:
        raise ValueError(f"Release audit rejected export: {report['violations']}")
    if destination == root or destination.exists():
        raise ValueError("Export destination must be a new directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="autoapply-export-", dir=destination.parent))
    try:
        for entry in paths:
            source, _ = _relative(root, entry)
            target = stage / entry
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        # Inspect staged bytes too, before making the export visible.
        staged = audit(stage, paths, paths, deny_fingerprints)
        if not staged["ok"]:
            raise ValueError(f"Staged export audit failed: {staged['violations']}")
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return paths
