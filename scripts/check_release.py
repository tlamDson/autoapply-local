"""Audit explicit export contents and source/tracked lists without network access."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autoapply_local.release_audit import (
    artifact_identity,
    artifact_inventory,
    audit,
    audit_artifacts,
    export_release,
    project_metadata,
)
from autoapply_local.storage import _atomic_bytes


def artifact_allowlist(name, source_files, version=None):
    """Map the explicit source inventory plus fixed packaging metadata only."""
    version = version or project_metadata(ROOT)["version"]
    kind = artifact_identity(name, version)
    return [name, *artifact_inventory(ROOT, kind, source_files, version)]


def source_zip(destination, allowed, deny):
    """Stage, inspect and exclusively publish a reproducible source ZIP."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    version = project_metadata(ROOT)["version"]
    name = f"autoapply-local-{version}.zip"
    target = destination / name
    if target.exists() or target.is_symlink():
        raise ValueError("Source ZIP already exists; refusing overwrite")
    stage = Path(tempfile.mkdtemp(prefix="source-zip-", dir=destination))
    temporary = stage / name
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in sorted(allowed):
                info = zipfile.ZipInfo(f"autoapply-local-{version}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, (ROOT / relative).read_bytes())
        report = audit_artifacts(ROOT, stage, allowed, expect=("zip",), deny=deny)
        if not report["ok"]:
            raise ValueError(f"Source ZIP audit failed: {report['violations']}")
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
        stage.rmdir()
    return audit_artifacts(ROOT, destination, allowed, expect=("zip",), deny=deny)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, help="Copy a clean audited release to a new directory")
    parser.add_argument("--deny-fingerprints", type=Path, help="External JSON array of denied SHA256 fingerprints")
    parser.add_argument("--artifacts", type=Path, help="Inspect final wheels, source archives and ZIPs offline")
    parser.add_argument("--expect", default="wheel,sdist", help="Required artifact kinds: wheel,sdist,zip")
    parser.add_argument("--source-zip", type=Path, help="Create an audited source ZIP without replacing existing files")
    args = parser.parse_args(argv)
    allowlist_path = ROOT / "export-allowlist.json"
    if not allowlist_path.is_file():
        print("Release audit failed: export-allowlist.json is required", file=sys.stderr)
        return 1
    allowed = json.loads(allowlist_path.read_text(encoding="utf-8"))
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        print("Release audit failed: allowlist must be an array of file paths", file=sys.stderr)
        return 1
    deny = json.loads(args.deny_fingerprints.read_text(encoding="utf-8")) if args.deny_fingerprints else []
    report = audit(ROOT, allowed, allowed, deny_fingerprints=deny)
    # Do not silently trust .gitignore. Inspect actual files, tolerating only known
    # generated build/cache trees which are deliberately excluded from export.
    generated = {"__pycache__", ".git", "build", "dist", ".pytest_cache"}
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in generated or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.is_file() and relative.as_posix() not in allowed:
            report["violations"].append(
                {"path": relative.as_posix(), "reason": "source file is outside export allowlist"}
            )
    # If this is its own Git repository, inspect tracked files as well, including
    # paths currently absent from disk. Never operate on an ancestor workspace repo.
    if (ROOT / ".git").exists():
        result = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True, timeout=10)
        tracked = [item for item in result.stdout.decode("utf-8").split("\0") if item]
        report["violations"].extend(audit(ROOT, tracked, allowed, deny)["violations"])
    report["ok"] = not report["violations"]
    try:
        expect = tuple(args.expect.split(","))
        if not expect or set(expect) - {"wheel", "sdist", "zip"}:
            raise ValueError("--expect must contain wheel,sdist,zip kinds")
        if args.source_zip and report["ok"]:
            report["violations"].extend(source_zip(args.source_zip, allowed, deny)["violations"])
        if args.artifacts:
            report["violations"].extend(
                audit_artifacts(ROOT, args.artifacts, allowed, expect=expect, deny=deny)["violations"]
            )
    except (ValueError, OSError) as error:
        report["violations"].append({"path": str(args.source_zip or args.artifacts or ROOT), "reason": str(error)})
    report["ok"] = not report["violations"]
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        return 1
    if args.export:
        public = allowed
        export_release(ROOT, args.export, public, deny)
        _atomic_bytes(
            args.export / "export-allowlist.json", (json.dumps(public, indent=2) + "\n").encode(), backup=False
        )
        print(f"Exported {len(public)} audited files to {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
