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


def choose_resume(profile, config, job, *, memory=None, ask=None):
    """Role-tag resume choice (resume_choice.choose); the chosen file must be a real PDF."""
    from . import resume_choice

    choice = resume_choice.choose(config, job, memory=memory, ask=ask)
    path = choice.path
    if not path.is_file() or not path.read_bytes().startswith(b"%PDF-"):
        raise ValueError("The selected resume must be an existing PDF")
    return choice


def describe_choice(choice):
    why = {
        "role_tag": f"role tag {choice.tag}",
        "keyword_score": f"role tag {choice.tag}, best JD keyword score {choice.score}",
        "only_resume": "only resume",
        "remembered": "your earlier answer",
        "asked": "your answer",
    }[choice.reason]
    return f"{choice.path} ({why})"


def select_resume(profile, config, job, *, memory=None, ask=None):
    return choose_resume(profile, config, job, memory=memory, ask=ask).path


def setup(*, root, guided=False, answer=None, finish=False, confirm=False, restart=False, edit=None, ui="none",
          terminal=False, launch=True):
    from . import browser, intake
    from .bootstrap import install

    root = Path(root)
    if terminal:
        from . import terminal_setup
        from .gmail_tab import GmailSession

        setup(root=root)
        return terminal_setup.run(
            root, launch=terminal_setup.launch_engine if launch else None, gmail=GmailSession(root)
        )
    if restart:
        from .guided import run

        return run(root, restart=True, ui=ui)
    guided_action = guided or answer is not None or finish or confirm
    if guided_action and (root / "user/setup-progress.json").exists():
        from .guided import run

        return run(root, answer=answer, finish=finish, confirm=confirm, edit=edit, ui=ui)
    if guided_action and not guided:
        raise ValueError("Start setup --guided before answering, finishing or confirming")
    result = intake.setup(root)
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
    if guided_action:
        from .guided import run

        return run(root, edit=edit, ui=ui)
    return {
        **result,
        "next": f"Open this runtime root: {root}. Use python autoapply-local.py COMMAND. "
        f"Place resume PDFs and source files in {result['resume_dir']}; "
        f"place transcripts, certificates, cover letters and supporting documents in {result['additional_document_dir']}. "
        "Select resumes.main_pdf explicitly using an absolute PDF path in the intake. "
        "Storing documents does not extract or confirm facts, enable tailoring, or upload files. "
        "Quick apply with an existing PDF does not require LaTeX. "
        "For first setup discover a native question tool, then run setup --guided --ui UI. "
        "Without a native tool use --ui none (ui_required). "
        "Advanced editing: user/profile-intake.md; preview profile update, then confirm. "
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
    from .extraction import extract_source

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


def terminal_ask(role, options):
    """Ask which resume fits a role no tag matches; None when the answer is blank or not a number."""
    print(f"No resume tag matches '{role}'. Which resume should be used?", file=sys.stderr)
    for number, item in enumerate(options, 1):
        print(f"  {number}. {item['id']}: {item['path']}", file=sys.stderr)
    print("Number (blank to stop): ", end="", file=sys.stderr, flush=True)
    try:
        answer = input().strip()
    except EOFError:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1]["id"]
    return None


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
            *(f"{record['id']} | {record['role']} -> {describe_choice(pdfs[record['id']])}" for record in records),
        ]
    )


def apply(*, root, selection, engine=None, approve_browser_tools=False, resume_ask=None):
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
    from .resume_choice import Memory

    memory = Memory(Path(root) / "user/resume-choices.json")
    ask = resume_ask or (terminal_ask if sys.stdin is not None and sys.stdin.isatty() else None)
    choices = {record["id"]: choose_resume(profile, config, record, memory=memory, ask=ask) for record in records}
    pdfs = {job_id: choice.path for job_id, choice in choices.items()}
    print(_apply_summary(profile, records, choices), file=sys.stderr, flush=True)
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
                    reason = "Held-tab cap reached; submit held forms by hand and record them with submitted ID before opening another job"
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


def submitted(*, root, selection):
    """Record held forms the user submitted by hand, freeing their held-tab slots."""
    from . import manual_submission

    store = Store(root)
    ids = list(dict.fromkeys(selection))
    if not ids or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("Choose explicit job IDs")
    for job_id in ids:
        manual_submission.check(store.get(job_id))
    return {"jobs": [manual_submission.record(store, job_id) for job_id in ids], **status(root=root)}


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
            "review",
            "submitted",
            "status",
        )
    }
