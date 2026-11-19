"""Second stage of install.ps1 / install.sh: check dependencies, build .venv, start setup --terminal.

Run by the system Python 3.11+ that the shell script found. Safe to rerun: an
existing .venv is reused, `pip install -e .` is repeated, and setup keeps every
user-owned file. User data stays in <repo>/user/ (the source launcher's default).
"""

import argparse
import os
import platform
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autoapply_local.install_check import detect as detect_dependencies, format_report  # noqa: E402


def venv_python(root, system=None):
    system = system or platform.system()
    base = Path(root) / ".venv"
    return base / "Scripts/python.exe" if system == "Windows" else base / "bin/python"


def _create_venv(path):
    venv.EnvBuilder(with_pip=True).create(str(path))


def _environment():
    env = {key: value for key, value in os.environ.items() if key.upper() not in {"PYTHONHOME", "PYTHONPATH"}}
    # A user-level pip config with `user = true` makes pip refuse to install into a venv.
    env.update(PIP_USER="0", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return env


def main(argv=None, *, root=ROOT, run=subprocess.call, create_venv=_create_venv, detect=detect_dependencies):
    parser = argparse.ArgumentParser(description="Install AutoApply Local into this checkout.")
    parser.add_argument("--no-setup", action="store_true", help="install only; do not start setup")
    args = parser.parse_args(argv)
    root = Path(root)

    result = detect()
    print(format_report(result))
    if not result["ok"]:
        return 3

    python = venv_python(root)
    if python.is_file():
        print(f"Reusing {root / '.venv'}")
    else:
        print(f"Creating {root / '.venv'}")
        create_venv(root / ".venv")
    env = _environment()
    if run([str(python), "-m", "pip", "install", "-e", str(root)], cwd=str(root), env=env) != 0:
        print("pip install -e . failed; fix the error above and rerun the installer.")
        return 4
    hint = f"{python} autoapply-local.py setup --terminal"
    if args.no_setup:
        print(f"Installed. Start setup with: {hint}")
        return 0
    print(f"Starting setup. Your data stays in {root / 'user'}")
    launcher = [str(python), str(root / "autoapply-local.py"), "setup"]
    if sys.stdin is not None and sys.stdin.isatty():
        return run([*launcher, "--terminal"], cwd=str(root), env=env)
    # No terminal to answer the prompts (CI, a pipe): create the runtime only.
    code = run(launcher, cwd=str(root), env=env)
    if code == 0:
        print(f"No terminal to answer setup. Continue in a terminal with: {hint}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
