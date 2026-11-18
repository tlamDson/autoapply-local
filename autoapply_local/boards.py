"""Validated public discovery sources, seed groups and content-bound config imports.

Shipped seeds carry a ``group`` (``sp500``, ``unicorn``); the user turns a whole
group off in ``config.json`` ``board_groups`` without deleting its boards.
Boards the user adds carry no group and are always fetched.
"""

import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from .storage import atomic_write_json, lock, read_json


GROUP = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")


def identity(board):
    if board["ats"] == "workday":
        return ("workday", board["host"], board["tenant"], board["site"])
    return (board["ats"], board["board"])


def normalize(values):
    if not isinstance(values, list):
        raise ValueError("Boards must be a JSON list")
    result = {}
    for raw in values:
        if not isinstance(raw, dict):
            raise ValueError("Each board must be an object")
        board = {}
        for key, value in raw.items():
            if not isinstance(value, str) or (key != "search" and not value.strip()):
                raise ValueError(f"Board {key} must be a nonempty string")
            board[key] = value.strip()
        ats = board.get("ats", "").lower()
        board["ats"] = ats
        if ats not in ("greenhouse", "workday", "rippling"):
            raise ValueError("Choose greenhouse, workday or rippling boards")
        fields = ("host", "tenant", "site") if ats == "workday" else ("board",)
        allowed = {"ats", "company", "endpoint", "group", *fields}
        if ats == "workday":
            allowed.add("search")
        if set(board) - allowed:
            raise ValueError("Unsupported board fields")
        if "group" in board and not GROUP.fullmatch(board["group"]):
            raise ValueError("A board group is a short lowercase name (letters, digits, - or _)")
        for field in fields:
            value = board.get(field, "")
            if field == "host":
                value = value.lower()
                board[field] = value
                pattern = r"[a-z0-9][a-z0-9-]*\.wd\d{1,2}\.myworkdayjobs\.com"
            else:
                pattern = r"[A-Za-z0-9][A-Za-z0-9_-]*"
            if not re.fullmatch(pattern, value):
                raise ValueError(f"Invalid public {ats} {field}")
        if ats == "greenhouse":
            hosts = {"boards-api.greenhouse.io", "boards-api.eu.greenhouse.io"}
            path = f"/v1/boards/{board['board']}/jobs"
        elif ats == "rippling":
            hosts = {"api.rippling.com"}
            path = f"/platform/api/ats/v1/board/{board['board']}/jobs"
        else:
            hosts = {board["host"]}
            path = f"/wday/cxs/{board['tenant']}/{board['site']}/jobs"
        if "endpoint" in board:
            endpoint = board["endpoint"]
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in hosts
                or parsed.path != path
                or parsed.port not in (None, 443)
                or parsed.username
                or parsed.password
                or parsed.fragment
                or "\\" in endpoint
                or any(char.isspace() for char in endpoint)
            ):
                raise ValueError("Endpoint must be the board's supported public HTTPS ATS route")
        result[identity(board)] = board
    return list(result.values())


def _config(root):
    path = Path(root) / "config.json"
    if not path.is_file():
        raise ValueError("Run setup first, then boards list or boards import PATH")
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError("Config must be an object")
    return value


def install_defaults(root, default_config):
    root = Path(root)
    with lock(root / "profile-update.lock"):
        path = root / "config.json"
        current = _config(root) if path.exists() else dict(default_config)
        if "boards" not in current:
            current["boards"] = normalize(
                json.loads(files(__package__).joinpath("data/boards.json").read_text(encoding="utf-8"))
            )
            atomic_write_json(path, current)


def list_boards(root):
    return normalize(_config(root).get("boards") or [])


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _merge(current, incoming):
    merged = {identity(board): board for board in normalize(current or [])}
    changes = []
    for board in incoming:
        key = identity(board)
        old = merged.get(key)
        if old != board:
            changes.append({"identity": key, "old": old, "new": board})
        merged[key] = board
    return list(merged.values()), changes


def preview_import(root, path):
    root, path = Path(root), Path(path).resolve()
    content = path.read_bytes()
    incoming = normalize(json.loads(content.decode("utf-8-sig")))
    _config(root)
    config_content = (root / "config.json").read_bytes()
    merged, changes = _merge(json.loads(config_content).get("boards"), incoming)
    return {
        "path": str(path),
        "sha256": _digest(content),
        "config_sha256": _digest(config_content),
        "boards": merged,
        "changes": changes,
    }


def apply_import(root, preview, confirm=False):
    if not confirm:
        return {"applied": False, "changes": preview["changes"]}
    root = Path(root)
    with lock(root / "profile-update.lock"):
        content = Path(preview["path"]).read_bytes()
        config_content = (root / "config.json").read_bytes()
        if _digest(content) != preview["sha256"] or _digest(config_content) != preview["config_sha256"]:
            raise ValueError("Boards input or config changed; preview again")
        incoming = normalize(json.loads(content.decode("utf-8-sig")))
        config = json.loads(config_content)
        merged, changes = _merge(config.get("boards"), incoming)
        atomic_write_json(root / "config.json", {**config, "boards": merged})
        return {"applied": True, "boards": merged, "changes": changes}


def _enabled(config):
    value = config.get("board_groups")
    return value if isinstance(value, dict) else {}


def groups(root):
    """Each seed group with its board count and on/off state, in first-seen order."""
    config = _config(root)
    enabled, counts = _enabled(config), {}
    for board in normalize(config.get("boards") or []):
        if "group" in board:
            counts[board["group"]] = counts.get(board["group"], 0) + 1
    return [{"group": name, "boards": count, "enabled": enabled.get(name, True) is not False}
            for name, count in counts.items()]


def active_boards(root):
    """Boards to fetch: every board whose group is not turned off."""
    config = _config(root)
    enabled = _enabled(config)
    return [board for board in normalize(config.get("boards") or []) if enabled.get(board.get("group"), True) is not False]


def set_group(root, group, enabled):
    if type(enabled) is not bool:
        raise ValueError("A board group is turned on or off")
    root = Path(root)
    with lock(root / "profile-update.lock"):
        known = [item["group"] for item in groups(root)]
        if group not in known:
            raise ValueError(f"Unknown board group {group!r}; choose " + ", ".join(known or ["none configured"]))
        config = _config(root)
        states = {**_enabled(config), group: enabled}
        # Only an off group is stored; on is the default.
        config["board_groups"] = {name: False for name, value in states.items() if value is False}
        atomic_write_json(root / "config.json", config)
    return {"group": group, "enabled": enabled, "groups": groups(root)}
