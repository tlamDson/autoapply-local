"""Audit and smoke-install final wheel/sdist into two new isolated environments."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autoapply_local.release_audit import artifact_inventory, audit_artifacts, project_metadata


# This runs under the installed interpreter, never by importing the source tree.
# Assertions live in the probe so a failed check is a failed subprocess even when
# stdout is unavailable; the outer runner records only non-sensitive outcomes.
_PROBE = r"""
import hashlib
import importlib.metadata as metadata
import importlib.resources as resources
import json
import sys
from pathlib import Path
import autoapply_local

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
runtime = Path(sys.argv[2]).resolve()
environment = Path(sys.argv[3]).resolve()
source = Path(spec["source"]).resolve()
# POSIX venv executables can be symlinks to the base interpreter. Check the
# invoked path and sys.prefix, while resolving the imported module independently.
assert Path(sys.executable).absolute().is_relative_to(environment)
assert Path(sys.prefix).resolve() == environment
module = Path(autoapply_local.__file__).resolve()
assert module.is_relative_to(environment) and not module.is_relative_to(source)
assert not Path.cwd().resolve().is_relative_to(source)
dist = metadata.distribution(spec["name"])
assert dist.metadata["Name"] == spec["name"]
assert dist.version == spec["version"]
normalize = lambda values: sorted("".join(value.split()) for value in values)
assert normalize(dist.requires or []) == normalize(spec["dependencies"])
assert any(ep.name == "autoapply-local" and ep.value == "autoapply_local.cli:main"
           and ep.group == "console_scripts" for ep in dist.entry_points)
package = resources.files("autoapply_local")
for relative, digest in spec["assets"].items():
    assert hashlib.sha256(package.joinpath(relative).read_bytes()).hexdigest() == digest, relative
assert runtime.is_relative_to(Path(spec["work"]).resolve())
assert not runtime.is_relative_to(source)
launcher = runtime / "autoapply-local.py"
assert launcher.read_bytes() == package.joinpath("data/bootstrap/launcher.py").read_bytes()
intake = runtime / "user/profile-intake.md"
sentinel = b"\nSynthetic install smoke sentinel.\n"
if sys.argv[4] == "seed":
    with intake.open("ab") as handle:
        handle.write(sentinel)
else:
    assert intake.read_bytes().endswith(sentinel)
    from autoapply_local.storage import Store
    from autoapply_local.runtime import status
    assert Store(runtime).tracker_rows() == []
    assert Store(runtime).jobs() == []
    assert status(root=runtime) == {"held_tabs": [], "unresolved": [], "counts": {}}
print(json.dumps({"version": dist.version, "dependencies": dist.requires or []}))
"""


def _environment():
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"}
        and not key.upper().startswith(("AUTOAPPLY_", "PYTHONUSER", "PIP_"))
    }
    env.update(PYTHONNOUSERSITE="1", PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_CONFIG_FILE=os.devnull)
    return env


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args(argv)
    commands = []
    report = {"ok": False, "commands": commands, "versions": {}}
    work = args.work_dir.resolve()
    created = False
    try:
        if args.work_dir.exists() or args.work_dir.is_symlink():
            raise ValueError("Work directory must be new; existing contents are never removed")
        source_files = json.loads((ROOT / "export-allowlist.json").read_text(encoding="utf-8"))
        metadata = project_metadata(ROOT)
        audit = audit_artifacts(ROOT, args.artifacts, source_files)
        if not audit["ok"]:
            raise ValueError("Final artifact audit failed")
        wheelhouse = args.wheelhouse.resolve() if args.wheelhouse else None
        if wheelhouse and not wheelhouse.is_dir():
            raise ValueError("Wheelhouse must be an existing directory")
        # Refuse paths inside the product: builds and runtime state belong outside it.
        if work.is_relative_to(ROOT.resolve()):
            raise ValueError("Work directory must be outside the source checkout")
        work.mkdir(parents=True, exist_ok=False)
        created = True
        cwd = work / "unrelated cwd thử ünicode"
        cwd.mkdir()
        env = _environment()
        spec = {
            "name": metadata["name"],
            "version": metadata["version"],
            "dependencies": metadata["dependencies"],
            "source": str(ROOT.resolve()),
            "work": str(work),
            "assets": {
                name.removeprefix("autoapply_local/"): hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
                for name, source in artifact_inventory(ROOT, "wheel", source_files).items()
                if source is not None and name.startswith("autoapply_local/")
            },
        }
        manifest = work / "expected-install.json"
        manifest.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        def run(arguments):
            arguments = [os.fspath(argument) for argument in arguments]
            record = {"argv": arguments, "exit_code": None}
            commands.append(record)
            try:
                completed = subprocess.run(
                    arguments,
                    cwd=cwd,
                    env=env,
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    timeout=300,
                    check=False,
                )
                record["exit_code"] = completed.returncode
            except subprocess.TimeoutExpired:
                record["error"] = "timeout"
                raise
            if completed.returncode != 0:
                raise RuntimeError("Install smoke command failed")
            return completed

        pip_options = ["--no-index", "--find-links", str(wheelhouse)] if wheelhouse else []
        version = metadata["version"]
        for kind, filename in (
            ("wheel", f"autoapply_local-{version}-py3-none-any.whl"),
            ("sdist", f"autoapply_local-{version}.tar.gz"),
        ):
            environment = work / (kind + " venv thử ünicode")
            venv.EnvBuilder(with_pip=True, system_site_packages=False).create(environment)
            scripts = environment / ("Scripts" if os.name == "nt" else "bin")
            python = scripts / ("python.exe" if os.name == "nt" else "python")
            console = scripts / ("autoapply-local.exe" if os.name == "nt" else "autoapply-local")
            if kind == "sdist":
                run([python, "-m", "pip", "install", *pip_options, *metadata["build_requires"]])
            run(
                [
                    python,
                    "-m",
                    "pip",
                    "install",
                    *pip_options,
                    *(["--no-build-isolation"] if kind == "sdist" else []),
                    (args.artifacts / filename).resolve(),
                ]
            )
            run([python, "-m", "pip", "check"])
            run([console, "--help"])
            run([python, "-I", "-m", "autoapply_local", "--help"])
            runtime = work / (kind + " runtime thử ünicode")
            cli = [python, "-I", "-m", "autoapply_local", "--user-dir", runtime]
            run([*cli, "setup"])
            run([python, "-I", "-c", _PROBE, manifest, runtime, environment, "seed"])
            run([*cli, "setup"])
            run([*cli, "status"])
            for engine in ("claude", "codex"):
                run([*cli, "doctor", engine])
            run([python, "-I", runtime / "autoapply-local.py", "status"])
            completed = run([python, "-I", "-c", _PROBE, manifest, runtime, environment, "verify"])
            report["versions"][kind] = json.loads(completed.stdout).get("version", version)
        report["ok"] = True
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        # Do not expose subprocess stdout/stderr, environment, or user documents.
        report["error"] = type(error).__name__
    if created:
        (work / "install-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
