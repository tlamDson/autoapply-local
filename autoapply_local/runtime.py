"""Executable default services; all external boundaries remain injectable."""

import json
import os
import shutil
import sys
from pathlib import Path

from .storage import Store, atomic_write_json, read_json


def configuration(root):
    path = Path(root) / "config.json"
    if not path.exists():
        raise ValueError("Run setup, edit the complete intake, and confirm profile update first")
    return read_json(path)


def selected_engine(config, override=None, purpose="apply"):
    selected = override or config.get("runtime", {}).get(purpose + "_engine")
    if selected not in ("codex", "claude"):
        raise ValueError("Choose an installed engine in the intake or command arguments")
    return selected


def select_resume(profile, config, job):
    settings = config.get("resumes", {})
    role = str(job.get("role", "")).casefold()
    selected = settings.get("main_pdf")
    alternatives = settings.get("alternatives", [])
    by_id = {item.get("id"): item.get("path", item.get("pdf")) for item in alternatives if isinstance(item, dict)}
    for token, choice in settings.get("role_mapping", {}).items():
        if str(token).casefold() in role:
            selected = by_id.get(choice, choice)
            break
    if not selected:
        raise ValueError("Select a main PDF or a role-mapped alternative in the intake")
    path = Path(selected).expanduser().resolve()
    if not path.is_file() or not path.read_bytes().startswith(b"%PDF-"):
        raise ValueError("The selected resume must be an existing PDF")
    return path


def setup(*, root):
    from . import browser, intake
    from .bootstrap import install

    result = intake.setup(root)
    root = Path(root)
    install(root)
    # Project-local files only. Never replace global configuration or existing files.
    configs = {
        ".mcp.json": browser.mcp_config("claude", "http://127.0.0.1:9222"),
        "mcp-codex.json": browser.mcp_config("codex", "http://127.0.0.1:9222"),
    }
    for relative, value in configs.items():
        path = root / relative
        if not path.exists():
            atomic_write_json(path, value)
    return {
        **result,
        "next": f"Open this runtime root: {root}. Use python autoapply-local.py COMMAND. "
        "Edit user/profile-intake.md; preview profile update, then confirm. "
        "Sign in to your selected engine and approve the project browser MCP normally. "
        "Doctor reports executable presence separately from account/connector readiness.",
    }


def profile_update(*, root, path, confirm=False):
    from .intake import apply_import, preview_import

    preview = preview_import(root, path)
    return apply_import(root, preview, confirm=confirm) if confirm else preview


def boards_list(*, root):
    from .boards import list_boards

    return {"boards": list_boards(root)}


def boards_import(*, root, path, confirm=False):
    from .boards import apply_import, preview_import

    preview = preview_import(root, path)
    return apply_import(root, preview, confirm=True) if confirm else preview


def source_extract(*, root, path):
    import uuid
    from .tailor import extract_source

    draft = extract_source(path)
    destination = Path(root) / "user/drafts" / (uuid.uuid4().hex + ".json")
    atomic_write_json(destination, draft)
    return {
        "status": "draft",
        "path": str(destination),
        "requires_confirmation": True,
        "next": "Check extracted text, map facts to the full intake's education/experience/skills groups, "
        "preview profile update, then explicitly --confirm. Extraction never establishes facts.",
    }


def history_import(*, root, path, confirm=False):
    store = Store(root)
    preview = store.preview_history(path)
    return store.import_history(preview, confirm=True) if confirm else preview


def doctor(*, root, engine=None):
    from .browser import discover_chrome
    from .engines import EngineError, resolve_executable

    config = configuration(root)
    selected = engine or config.get("runtime", {}).get("apply_engine")
    launch_argv, executable_status = None, "not_selected"
    if selected in ("claude", "codex"):
        try:
            launch_argv = resolve_executable(selected)
            executable_status = "resolved"
        except EngineError:
            executable_status = "configuration"
    try:
        chrome = str(discover_chrome(config.get("resources", {}).get("chrome_override") or None))
    except FileNotFoundError:
        chrome = None
    return {
        "engine": selected,
        "executable": shutil.which(selected) if selected else None,
        "executable_status": executable_status,
        "launch_argv": launch_argv,
        "capability": "not_verified",
        "login": "not_verified",
        "native_connectors": "not_verified",
        "chrome": chrome,
        "node": shutil.which("node"),
        "npx": shutil.which("npx"),
        "optional_tex": shutil.which("pdflatex"),
        "optional_poppler": shutil.which("pdftoppm"),
        "instructions": "Install missing executables; sign in using the selected engine's normal UI. "
        "Approve MCP connections normally. Gmail needs separate read/search opt-in and an explicitly verified account.",
    }


def fetch(*, root, engine=None, url=None):
    from .boards import list_boards
    from .fetch import Fetcher, evaluate_job

    configuration(root)
    sources = list_boards(root) if not url else []
    if not url and not sources:
        raise ValueError(
            "No sources configured: run setup, boards list, or boards import PATH --confirm; or fetch --url URL"
        )
    store = Store(root)
    profile_path = Path(root) / "profile.json"
    profile = read_json(profile_path) if profile_path.exists() else {}
    fetcher = Fetcher()
    accepted, excluded, unknown = [], [], []
    if url:
        imported = fetcher.import_url(url)
        if imported.get("status") != "open":
            return imported
        batches = [imported["job"]]
    else:
        batches = []
        for board in sources:
            try:
                batches.extend(fetcher.fetch(board["ats"], board))
            except (OSError, ValueError) as error:
                unknown.append({"board": board, "reason": str(error)})
    for item in batches:
        verdict = evaluate_job(item, profile)
        if verdict["decision"] == "exclude":
            excluded.append({"id": item["id"], **verdict})
        else:
            accepted.append(store.enqueue(item))
    return {
        "jobs": accepted,
        "excluded": excluded,
        "unknown": unknown,
        "engine": engine,
        "note": "Public board fetch does not call an LLM.",
    }


def _workflow(root, override=None, launch=False, lane=None, approve_browser_tools=False):
    if type(approve_browser_tools) is not bool:
        raise ValueError("approve_browser_tools must be a bool")
    from . import browser, engines
    from .workflow import Workflow

    config = configuration(root)
    selected = selected_engine(config, override)
    store = Store(root)
    held = [record for record in store.jobs() if record.get("tab_id") and record["state"] != "submitted"]
    lane_path = Path(root) / "browser/active-lanes.json"
    lanes = read_json(lane_path) if lane_path.exists() else [{"debug_url": "http://127.0.0.1:9222"}]
    if launch:
        resources = config.get("resources", {})
        lanes = browser.launch_chrome(
            root,
            executable=resources.get("chrome_override") or None,
            lanes=resources.get("lanes", 1),
            opt_in=resources.get("multiple_lanes_opt_in", resources.get("lanes", 1) > 1),
            available_memory_mb=None,
            held_tabs=held,
            held_tab_cap=resources.get("held_tab_cap", 5),
        )
        atomic_write_json(lane_path, lanes)
    owner = lane or lanes[0]
    lane_id = str(owner.get("lane", 1))
    endpoint = owner["debug_url"]
    options = {"approve_browser_tools": True} if approve_browser_tools else {}
    engine = engines.Engine(selected, cwd=root, debug_url=endpoint, **options)
    bridge = browser.BrowserBridge(root=root, engine=engine, debug_url=endpoint)
    if hasattr(bridge, "restore"):
        for record in held:
            if record.get("debug_url", endpoint) == endpoint and str(record.get("lane", 1)) == lane_id:
                bridge.restore(record)
    return (
        Workflow(
            root, browser=bridge, engines={selected: engine}, default_engine=selected, lane=lane_id, debug_url=endpoint
        ),
        config,
        selected,
    )


def _apply_summary(profile, records, pdfs):
    targets, logistics = profile.get("targets", {}), profile.get("logistics", {})
    season = logistics.get("season") or targets.get("season") or "unspecified"
    year = logistics.get("year") or targets.get("year") or "unspecified"
    window = logistics.get("window") or "unspecified"
    return "\n".join(
        [
            "Apply preflight",
            "Target roles: " + ", ".join(targets.get("roles", [])),
            f"Recruiting season: {season} {year}; window: {window}",
            *(f"{record['id']} | {record['role']} -> {pdfs[record['id']]}" for record in records),
        ]
    )


def apply(*, root, selection, engine=None, approve_browser_tools=False):
    if type(approve_browser_tools) is not bool:
        raise ValueError("approve_browser_tools must be a bool")
    from . import browser
    from .storage import lock
    from .workflow import Workflow

    config = configuration(root)
    selected = selected_engine(config, engine)
    base = Workflow(root, browser=None, engines={}, default_engine=selected)
    ids = base._selection(selection, "apply")
    records = [base.store.get(job_id) for job_id in ids]
    for record in records:
        if record["state"] not in ("pending", "filled", "reviewed", "needs_manual"):
            raise ValueError("Job is unresolved or submitted; submission outcomes require manual reconciliation")
        if record["state"] != "pending" and not record.get("tab_id"):
            raise ValueError("Manual continuation requires the original held tab")
    profile = base._profile()
    pdfs = {record["id"]: select_resume(profile, config, record) for record in records}
    print(_apply_summary(profile, records, pdfs), file=sys.stderr, flush=True)
    resources = config.get("resources", {})
    lane_count = resources.get("lanes", 1)
    if type(lane_count) is not int or lane_count not in (1, 2, 3):
        raise ValueError("Choose one to three browser lanes")
    if lane_count > 1 and not resources.get("multiple_lanes_opt_in", lane_count > 1):
        raise ValueError("Multiple browser lanes require explicit opt-in")
    lanes, runners = None, {}
    cursor = 0
    results = []
    reason = None
    for job_id in ids:
        # The lock covers admission and creation of the held-tab record, so
        # another command cannot admit the same capacity while a job is filling.
        with lock(Path(root) / ".admission.lock", timeout=300):
            record = base.store.get(job_id)
            if record["state"] == "pending":
                held = [item for item in base.store.jobs() if item.get("tab_id") and item["state"] != "submitted"]
                if len(held) >= resources.get("held_tab_cap", 5):
                    reason = "Held-tab cap reached; finish held forms before opening another job"
                    break
                if browser.available_memory_mb() < 2048 + lane_count * 1024:
                    reason = "Insufficient available memory for new browser work; held tabs are preserved"
                    break
                if lanes is None:
                    lanes = browser.launch_chrome(
                        root,
                        executable=resources.get("chrome_override") or None,
                        lanes=lane_count,
                        opt_in=resources.get("multiple_lanes_opt_in", lane_count > 1),
                        available_memory_mb=None,
                        held_tabs=held,
                        held_tab_cap=resources.get("held_tab_cap", 5),
                    )
                    atomic_write_json(Path(root) / "browser/active-lanes.json", lanes)
                owner = lanes[cursor % len(lanes)]
                cursor += 1
            else:
                lane_path = Path(root) / "browser/active-lanes.json"
                saved = read_json(lane_path) if lane_path.exists() else []
                owner = next(
                    (item for item in saved if str(item.get("lane", 1)) == str(record.get("lane", 1))),
                    {"lane": record.get("lane", 1), "debug_url": "http://127.0.0.1:9222"},
                )
                owner = {**owner, "debug_url": record.get("debug_url", owner["debug_url"])}
            key = (str(owner.get("lane", 1)), owner["debug_url"])
            if key not in runners:
                options = {"approve_browser_tools": True} if approve_browser_tools else {}
                runners[key] = _workflow(root, engine, lane=owner, **options)[0]
            runner = runners[key]
            pdf = pdfs[job_id]
            results.append(runner.apply([job_id], pdf=pdf, engine=selected))
    return {"results": results, **({"reason": reason} if reason else {}), **base.status()}


def review(*, root, selection, approve_browser_tools=False):
    if type(approve_browser_tools) is not bool:
        raise ValueError("approve_browser_tools must be a bool")
    from .workflow import Workflow

    base = Workflow(root, browser=None, engines={})
    ids = base._selection(selection, "review")
    lane_path = Path(root) / "browser/active-lanes.json"
    lanes = read_json(lane_path) if lane_path.exists() else []
    runners, reviews = {}, []
    for job_id in ids:
        record = base.store.get(job_id)
        owner = next(
            (item for item in lanes if str(item.get("lane", 1)) == str(record.get("lane", 1))),
            {"lane": record.get("lane", 1), "debug_url": "http://127.0.0.1:9222"},
        )
        owner = {**owner, "debug_url": record.get("debug_url", owner["debug_url"])}
        key = (str(owner.get("lane", 1)), owner["debug_url"])
        if key not in runners:
            options = {"approve_browser_tools": True} if approve_browser_tools else {}
            runners[key] = _workflow(root, lane=owner, **options)[0]
        reviews.extend(runners[key].review([job_id])["reviews"])
    return {"reviews": reviews, **base.status()}


def submit(*, root, selection, approve_browser_tools=False):
    if type(approve_browser_tools) is not bool:
        raise ValueError("approve_browser_tools must be a bool")
    from .workflow import Workflow

    base = Workflow(root, browser=None, engines={})
    ids = base._selection(selection, "submit")
    lane_path = Path(root) / "browser/active-lanes.json"
    lanes = read_json(lane_path) if lane_path.exists() else []
    runners, jobs = {}, []
    for job_id in ids:
        record = base.store.get(job_id)
        owner = next(
            (item for item in lanes if str(item.get("lane", 1)) == str(record.get("lane", 1))),
            {"lane": record.get("lane", 1), "debug_url": "http://127.0.0.1:9222"},
        )
        owner = {**owner, "debug_url": record.get("debug_url", owner["debug_url"])}
        key = (str(owner.get("lane", 1)), owner["debug_url"])
        if key not in runners:
            options = {"approve_browser_tools": True} if approve_browser_tools else {}
            runners[key] = _workflow(root, lane=owner, **options)[0]
        jobs.extend(runners[key].submit([job_id])["jobs"])
    return {"jobs": jobs, **base.status()}


def tailor(*, root, jd, engine=None):
    from .engines import Engine
    from .tailor import Tailor

    config = configuration(root)
    selected = selected_engine(config, engine, "tailor")
    profile = read_json(Path(root) / "profile.json")
    return Tailor(root, engines={selected: Engine(selected, cwd=root)}).run(
        jd, profile, engine=selected, config=config.get("tailor", {})
    )


class NativeMailRuntime:
    """Use selected-runtime native connectors; no browser or custom OAuth fallback."""

    def __init__(self, engine):
        self.engine = engine
        self.capabilities = None

    def _run(self, kind, prompt, schema, tools):
        return self.engine.run({"kind": kind, "prompt": prompt, "schema": schema, "allowed_tools": tools})

    def discover_capabilities(self):
        schema = {
            "type": "object",
            "required": ["gmail"],
            "additionalProperties": False,
            "properties": {
                "gmail": {
                    "type": "object",
                    "required": ["accounts", "permissions", "search_tool", "read_tool"],
                    "additionalProperties": False,
                    "properties": {
                        "accounts": {"type": "array", "items": {"type": "string"}},
                        "permissions": {"type": "array", "items": {"type": "string", "enum": ["search", "read"]}},
                        "search_tool": {"type": "string"},
                        "read_tool": {"type": "string"},
                    },
                }
            },
        }
        response = self._run(
            "mail_discovery",
            "Discover native Gmail connector tools and explicitly available accounts. "
            "Use tool discovery scoped to Gmail, then read-only Gmail account/profile metadata to verify accounts. "
            "Do not search or read any messages, use a browser, configure OAuth, or infer an account. "
            "Return the exact discovered callable search/read tool names, never resource-listing names. "
            "If metadata or a callable read-only connector is unavailable return empty arrays and tool names.",
            schema,
            [
                "ToolSearch" if self.engine.name == "claude" else "search_tool_bm25",
                "mcp__gmail__get_profile",
                "mcp__gmail__list_accounts",
            ],
        )
        from .tool_gate import mail_tool_action

        capabilities = response["gmail"]
        if (
            mail_tool_action(capabilities.get("read_tool")) != "read"
            or mail_tool_action(capabilities.get("search_tool")) != "search"
        ):
            self.capabilities = None
            raise PermissionError("Native Gmail tools/account metadata unavailable")
        self.capabilities = capabilities
        return response

    def search_mail(self, account, query, lookback_days):
        from .tool_gate import mail_tool_action

        capabilities = self.capabilities or self.discover_capabilities()["gmail"]
        tool = capabilities.get("search_tool")
        if (
            mail_tool_action(tool) != "search"
            or account not in capabilities["accounts"]
            or "search" not in capabilities.get("permissions", [])
        ):
            raise PermissionError("Native Gmail search/account unavailable")
        schema = {
            "type": "object",
            "required": ["messages"],
            "additionalProperties": False,
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "account"],
                        "additionalProperties": False,
                        "properties": {"id": {"type": "string"}, "account": {"type": "string"}},
                    },
                }
            },
        }
        return self._run(
            "mail_search",
            json.dumps(
                {
                    "action": "search only",
                    "account": account,
                    "query": query,
                    "lookback_days": lookback_days,
                    "no_write_actions": True,
                }
            ),
            schema,
            [tool],
        )["messages"]

    def read_mail(self, account, message_id):
        from .tool_gate import mail_tool_action

        capabilities = self.capabilities or self.discover_capabilities()["gmail"]
        tool = capabilities.get("read_tool")
        if (
            mail_tool_action(tool) != "read"
            or account not in capabilities["accounts"]
            or "read" not in capabilities.get("permissions", [])
        ):
            raise PermissionError("Native Gmail read/account unavailable")
        schema = {
            "type": "object",
            "required": ["id", "account", "subject", "body"],
            "additionalProperties": False,
            "properties": {key: {"type": "string"} for key in ("id", "account", "subject", "body", "received_at")},
        }
        return self._run(
            "mail_read",
            json.dumps({"action": "read only", "account": account, "message_id": message_id, "no_write_actions": True}),
            schema,
            [tool],
        )


def mail_check(*, root, engine=None):
    from .engines import Engine
    from .gmail import MailChecker

    config = configuration(root)
    resources = config.get("resources", {})
    if not resources.get("gmail_opt_in"):
        return {"status": "disabled", "reason": "Gmail is opt-in"}
    selected = selected_engine(config, engine)
    return MailChecker(root, runtime=NativeMailRuntime(Engine(selected, cwd=root))).check(resources)


def status(*, root):
    jobs = Store(root).jobs()
    return {
        "held_tabs": [
            {"job_id": item["id"], "tab_id": item["tab_id"], "state": item["state"]}
            for item in jobs
            if item.get("tab_id") and item["state"] != "submitted"
        ],
        "unresolved": [
            item
            for item in jobs
            if item["state"] in ("claimed", "needs_manual", "submit_intent", "needs_reconciliation")
        ],
        "counts": {state: sum(item["state"] == state for item in jobs) for state in {item["state"] for item in jobs}},
    }


def services():
    return {
        name: globals()[name]
        for name in (
            "setup",
            "profile_update",
            "boards_list",
            "boards_import",
            "source_extract",
            "history_import",
            "doctor",
            "fetch",
            "apply",
            "tailor",
            "review",
            "submit",
            "mail_check",
            "status",
        )
    }
