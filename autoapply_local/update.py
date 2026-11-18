"""`update`: back up user/, pull the source (git fast-forward or release zip), reinstall, migrate.

Also provides the one-line newer-release notice that `apply` prints. Every
external effect (subprocess, download, release lookup, clock) is injectable so
tests stay offline.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import __version__
from .migrations import MIGRATIONS

REPOSITORY = "tlamDson/autoapply-local"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ZIP_URL = f"https://github.com/{REPOSITORY}/archive/refs/tags/{{tag}}.zip"
# Not copied into backups: earlier backups, and Chrome profiles (large, locked while Chrome runs).
BACKUP_SKIP = {"backups", "browser", "chrome"}
# Never written from a release zip.
ZIP_SKIP = {"user", ".venv", ".git"}
STATE_FILE = "migrations.json"
NO_CHECK_ENV = "AUTOAPPLY_LOCAL_NO_UPDATE_CHECK"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:(a|b|rc)(\d+))?$")
_STAGES = {"a": 0, "b": 1, "rc": 2, None: 3}


def parse_version(text):
    match = _VERSION.match(str(text or "").strip())
    if not match:
        return None
    major, minor, patch, stage, number = match.groups()
    return int(major), int(minor), int(patch or 0), _STAGES[stage], int(number or 0)


def is_newer(latest, current):
    new, old = parse_version(latest), parse_version(current)
    return bool(new and old and new > old)


def _http_get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "autoapply-local", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read()


def latest_release(fetch=_http_get):
    payload = json.loads(fetch(LATEST_URL))
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    if not tag or payload.get("draft"):
        return None
    return {"tag": tag, "zip": ZIP_URL.format(tag=tag)}


def release_notice(current=__version__, latest=latest_release):
    if os.environ.get(NO_CHECK_ENV):
        return None
    try:
        found = latest()
    except Exception:  # a notice must never break apply
        return None
    if found and is_newer(found["tag"], current):
        return f"AutoApply Local {found['tag']} is available (you have {current}); run `update` to install it."
    return None


def backup_user(user_dir, now=None):
    user_dir = Path(user_dir)
    if not user_dir.is_dir():
        raise RuntimeError(f"No user data folder at {user_dir}; run setup first.")
    stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H%M%S")
    target, counter = user_dir / "backups" / stamp, 1
    while target.exists():
        counter += 1
        target = user_dir / "backups" / f"{stamp}-{counter}"
    target.mkdir(parents=True)
    for item in user_dir.iterdir():
        if item.name in BACKUP_SKIP:
            continue
        if item.is_dir():
            shutil.copytree(item, target / item.name, symlinks=True)
        else:
            shutil.copy2(item, target / item.name)
    return target


def _load_state(user_dir):
    try:
        state = json.loads((Path(user_dir) / STATE_FILE).read_text(encoding="utf-8"))
        return list(state.get("applied", []))
    except (OSError, ValueError, AttributeError):
        return []


def _save_state(user_dir, applied):
    path = Path(user_dir) / STATE_FILE
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"applied": applied}, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_migrations(user_dir, migrations=None):
    """Run each not-yet-applied migration once, recording it only after it succeeds."""
    migrations = MIGRATIONS if migrations is None else migrations
    applied = _load_state(user_dir)
    ran = []
    for name, migrate in migrations:
        if name in applied:
            continue
        migrate(Path(user_dir))
        applied.append(name)
        ran.append(name)
        _save_state(user_dir, applied)
    return ran


def _run(argv, cwd=None, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)


def _checked(run, argv, cwd, what, env=None):
    completed = run(argv, cwd=str(cwd), env=env)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise RuntimeError(f"{what} failed: {detail[-1] if detail else 'exit ' + str(completed.returncode)}")
    return (completed.stdout or "").strip()


def _git_update(repo, run):
    before = _checked(run, ["git", "rev-parse", "HEAD"], repo, "git rev-parse")
    _checked(run, ["git", "pull", "--ff-only"], repo,
             "git pull --ff-only (local commits or edits block a fast-forward)")
    after = _checked(run, ["git", "rev-parse", "HEAD"], repo, "git rev-parse")
    if before == after:
        return []
    log = _checked(run, ["git", "log", "--oneline", f"{before}..{after}"], repo, "git log")
    return [line for line in log.splitlines() if line.strip()]


def _zip_members(data, repo):
    """Validate every member first; return (target, bytes) pairs to write."""
    repo = Path(repo).resolve()
    planned = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if name.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
                raise ValueError(f"Unsafe path in release zip: {info.filename}")
            relative = parts[1:]  # drop the archive's top folder
            if info.is_dir() or not relative or relative[0] in ZIP_SKIP:
                continue
            target = repo.joinpath(*relative).resolve()
            if not target.is_relative_to(repo):
                raise ValueError(f"Unsafe path in release zip: {info.filename}")
            planned.append((target, archive.read(info)))
    return planned


def _zip_update(repo, user_dir, latest, download):
    found = latest()
    if not found:
        raise RuntimeError("No published release found; install git and rerun update.")
    planned = _zip_members(download(found["zip"]), repo)
    previous = _source_version(repo, __version__)
    user_dir = Path(user_dir).resolve()
    for target, content in planned:
        if target.is_relative_to(user_dir):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return [f"{previous} -> {found['tag'].lstrip('v')}"]


def _source_version(repo, default):
    try:
        text = (Path(repo) / "autoapply_local/__init__.py").read_text(encoding="utf-8")
    except OSError:
        return default
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else default


def source_root():
    return Path(__file__).resolve().parents[1]


def _is_source(repo):
    return all((Path(repo) / name).is_file() for name in ("pyproject.toml", "export-allowlist.json",
                                                         "autoapply_local/__init__.py"))


def _pip_environment():
    env = {key: value for key, value in os.environ.items() if key.upper() not in {"PYTHONHOME", "PYTHONPATH"}}
    env.update(PIP_USER="0", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return env


def update(*, root, repo=None, run=_run, which=shutil.which, download=_http_get, latest=latest_release,
           migrations=None, now=None):
    repo = Path(repo) if repo is not None else source_root()
    if not _is_source(repo):
        raise RuntimeError("update works only in a source checkout installed by install.ps1 / install.sh.")
    backup = backup_user(root, now=now)
    if (repo / ".git").exists() and which("git"):
        method, changes = "git", _git_update(repo, run)
    else:
        method, changes = "zip", _zip_update(repo, root, latest, download)
    _checked(run, [sys.executable, "-m", "pip", "install", "-e", str(repo)], repo, "pip install -e .",
             env=_pip_environment())
    ran = run_migrations(root, migrations)
    return {"backup": str(backup), "method": method, "changes": changes, "migrations": ran}
