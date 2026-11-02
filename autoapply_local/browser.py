"""Local isolated Chrome lanes and non-destructive MCP configuration.

Giả định của người thực thi: 2 GiB base reserve plus 1 GiB per lane is an
admission heuristic, not an 8 GB benchmark; MCP 0.14.0 is pinned for reproducible
configuration and has not been live validated by this offline release.
"""

import copy
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from .storage import atomic_write_json, lock, read_json


MCP_VERSION = "0.14.0"


def discover_chrome(override=None):
    if override is not None:
        path = Path(override).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Chrome override is not a file: {path}")
        return path
    candidates = []
    if platform.system() == "Windows":
        candidates.extend(
            Path(os.environ[key]) / "Google/Chrome/Application/chrome.exe"
            for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
            if os.environ.get(key)
        )
    elif platform.system() == "Darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        candidates.append(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    for executable in ("google-chrome", "chrome", "chromium", "chromium-browser"):
        located = shutil.which(executable)
        if located:
            candidates.append(Path(located))
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("Chrome not found; configure chrome_override with an executable path")


def available_memory_mb():
    """Best-effort available RAM; unknown RAM denies admission rather than guessing."""
    if platform.system() == "Windows":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong)
                for name in (
                    "total",
                    "available",
                    "page_total",
                    "page_available",
                    "virtual_total",
                    "virtual_available",
                    "extended",
                )
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.available // (1024 * 1024)
    elif platform.system() == "Darwin":
        try:
            import re

            output = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True, timeout=5).stdout
            page = int(re.search(r"page size of (\d+) bytes", output).group(1))
            pages = sum(int(count) for count in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)", output))
            return page * pages // (1024 * 1024)
        except (OSError, subprocess.SubprocessError, AttributeError):
            return 0
    else:
        try:
            return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // (1024 * 1024)
        except (ValueError, OSError, AttributeError):
            return 0
    return 0


def _launch_chrome(
    root,
    executable=None,
    lanes=1,
    opt_in=False,
    available_memory_mb=8192,
    held_tabs=(),
    held_tab_cap=5,
    spawn=subprocess.Popen,
):
    if type(lanes) is not int or lanes not in (1, 2, 3):
        raise ValueError("Lane count must be 1, 2 or 3")
    if lanes > 1 and not opt_in:
        raise ValueError("Multiple lanes require explicit opt-in")
    memory = available_memory_mb
    if memory is None:
        memory = globals()["available_memory_mb"]()
    if memory < 2048 + lanes * 1024:
        raise ValueError("Insufficient available memory for new browser work; held tabs are preserved")
    if held_tab_cap < 1 or len(held_tabs) >= held_tab_cap:
        raise ValueError("Held-tab cap reached; review or submit held tabs before starting new work")
    if executable is None:
        executable = discover_chrome()
    root = Path(root).resolve()
    result = []
    for index in range(lanes):
        profile = root / "browser" / f"lane-{index + 1}"
        profile.mkdir(parents=True, exist_ok=True)
        ownership = profile / "autoapply-owner.json"
        owner = read_json(ownership) if ownership.exists() else {}
        if owner and (owner.get("root") != str(root) or owner.get("executable") != str(executable)):
            raise ValueError("Browser lane belongs to another installation or executable")
        port = owner.get("port", 0)
        with socket.socket() as available:
            try:
                available.bind(("127.0.0.1", port))
            except OSError:
                # Reuse only the owned live process; never attach to an unrelated
                # service merely because it is listening on a remembered port.
                if not owner.get("pid"):
                    raise ValueError("Browser debugging port is owned by another process")
                try:
                    os.kill(int(owner["pid"]), 0)
                    with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
                        version = json.load(response)
                    if not str(version.get("Browser", "")).startswith(("Chrome/", "Chromium/")):
                        raise ValueError("Owned browser endpoint is unavailable")
                except (OSError, ValueError) as error:
                    raise ValueError("Browser lane ownership requires manual reconciliation") from error
                result.append(
                    {
                        "lane": index + 1,
                        "profile_dir": str(profile),
                        "debug_url": f"http://127.0.0.1:{port}",
                        "pid": owner["pid"],
                    }
                )
                continue
            port = available.getsockname()[1]
        command = [
            str(executable),
            f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        process = spawn(command, shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atomic_write_json(
            ownership, {"root": str(root), "executable": str(executable), "port": port, "pid": process.pid}
        )
        result.append(
            {
                "lane": index + 1,
                "profile_dir": str(profile),
                "debug_url": f"http://127.0.0.1:{port}",
                "command": command,
                "pid": process.pid,
            }
        )
    return result


def launch_chrome(
    root,
    executable=None,
    lanes=1,
    opt_in=False,
    available_memory_mb=8192,
    held_tabs=(),
    held_tab_cap=5,
    spawn=subprocess.Popen,
):
    with lock(Path(root).resolve() / ".browser-launch.lock"):
        return _launch_chrome(root, executable, lanes, opt_in, available_memory_mb, held_tabs, held_tab_cap, spawn)


def _url_identity(url):
    from .identity import canonical_url

    return canonical_url(url)


class HeldPageError(ValueError):
    def __init__(self, message, tab_id):
        super().__init__(message)
        self.tab_id = str(tab_id)


def validate_quote(text, evidence, label=None, value=None):
    if not isinstance(text, str) or not isinstance(evidence, dict):
        raise ValueError("Static Review evidence is missing")
    start, end = evidence.get("start"), evidence.get("end")
    quote = evidence.get("quote")
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start < end <= len(text)
        or text[start:end] != quote
        or (label and label not in quote)
        or (value is not None and str(value) not in quote)
    ):
        raise ValueError("Static Review quote/span does not prove the observed value")


class EngineMCPClient:
    """Selected-engine MCP actions with Chrome's loopback tab list as authority.

    Runtime actions require ordinary connector approvals. No approval bypass,
    global configuration replacement, browser Gmail or cross-engine fallback.
    """

    def __init__(self, engine, debug_url):
        parsed = urlsplit(debug_url)
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1") or not parsed.port:
            raise ValueError("Browser MCP requires a loopback debug URL")
        self.engine, self.debug_url = engine, debug_url.rstrip("/")

    def _run(self, operation, payload, properties, required, tools):
        if "tab_id" in payload:
            matches = [page for page in self.list_pages() if str(page["id"]) == str(payload["tab_id"])]
            if len(matches) != 1:
                raise ValueError("Native Chrome target disappeared")
            payload = {
                **payload,
                "target_url": matches[0]["url"],
                "target_instructions": "Match this native target URL against MCP list_pages, select its existing page ID, and fail if ambiguous. The native tab_id is a Chrome target ID, not a numeric MCP page index.",
            }
        return self.engine.run(
            {
                "kind": "submit" if operation == "submit" else "browser_" + operation,
                "prompt": "Use only the approved autoapply-local-browser MCP. Return the actual tool result as JSON. "
                "Never invent observations, open duplicate forms, or submit except the explicit submit operation. "
                + json.dumps({"operation": operation, **payload}, ensure_ascii=False),
                "allowed_tools": ["mcp__autoapply-local-browser__" + tool for tool in tools],
                "schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            }
        )

    def list_pages(self):
        with urlopen(self.debug_url + "/json/list", timeout=5) as response:
            pages = json.load(response)
        return [{"id": str(page["id"]), "url": page["url"]} for page in pages if page.get("type") == "page"]

    def new_page(self, url):
        before = {page["id"] for page in self.list_pages()}
        self._run(
            "open",
            {"url": url},
            {"status": {"type": "string", "enum": ["opened"]}},
            ["status"],
            ["new_page", "list_pages"],
        )
        new = [page for page in self.list_pages() if page["id"] not in before]
        if len(new) != 1:
            raise ValueError("Native Chrome tab identity is missing or ambiguous; inspect held tabs")
        # Return the real target even for a login redirect so the bridge can hold
        # its ID rather than lose the only tab opened by this operation.
        return new[0]

    def evaluate_script(self, tab_id, function):
        return self._run(
            "evaluate",
            {"tab_id": tab_id, "function": function},
            {"result": {"type": "object"}},
            ["result"],
            ["list_pages", "select_page", "evaluate_script"],
        )["result"]

    def upload_file(self, tab_id, path):
        return self._run(
            "upload",
            {
                "tab_id": tab_id,
                "path": path,
                "instructions": "Inspect DOM snapshot for the actual Resume file input, select this existing tab and use upload_file. Never use JS to fake an attachment.",
            },
            {"status": {"type": "string", "enum": ["uploaded", "needs_manual"]}},
            ["status"],
            ["list_pages", "select_page", "take_snapshot", "upload_file"],
        )

    def interact(self, tab_id, profile, job, blocked, pdf=None):
        return self._run(
            "native_fill",
            {
                "tab_id": tab_id,
                "profile": profile,
                "job": job,
                "blocked": blocked,
                "pdf": str(pdf) if pdf is not None else None,
                "instructions": "Use only confirmed exact scoped facts. Inspect the existing tab's DOM snapshot and identify actual fields and required questions. Fill supported controls; use native keyboard fill/type then blur for Workday names, employee ID, fieldOfStudy, phone code, textareas and dates. Scope option selection to the field's aria-controls listbox and verify selectedItem chips. Inspect Add/Remove controls before use; opaque record IDs are not profile indexes. Upload the selected PDF on the actual Resume page before Next advances or leaves that page. Observe the actual browser File name, size and SHA256 from File.arrayBuffer with crypto.subtle.digest; return that observed upload_receipt, never a hash copied from profile/job/local expectations. Missing file input or receipt means needs_manual. For multipage Workday, continue with Next/Back only, wait for current step transition or validation errors, and read values back on Review. Stop for account sign-in/creation challenges or unknown questions before uploading, returning needs_manual with blocked reasons. Preserve the tab. Never click Submit. Do not open another tab.",
            },
            {
                "filled": {"type": "array", "items": {"type": "string"}},
                "blocked": {"type": "array", "items": {"type": "object"}},
                "status": {"type": "string", "enum": ["filled", "needs_manual"]},
                "upload_receipt": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "size": {"type": "integer"},
                        "sha256": {"type": "string"},
                    },
                    "required": ["name", "size", "sha256"],
                    "additionalProperties": False,
                },
            },
            ["filled", "blocked", "status"],
            [
                "list_pages",
                "select_page",
                "take_snapshot",
                "fill",
                "fill_form",
                "press_key",
                "click",
                "evaluate_script",
                "upload_file",
            ],
        )

    def submit(self, tab_id, job_id):
        return self._run(
            "submit",
            {
                "tab_id": tab_id,
                "job_id": job_id,
                "instructions": "Explicit submit authorization applies only to this reviewed tab/job. Click Submit once, wait for and read confirmation. Return unknown if no definite matched receipt; do not retry.",
            },
            {
                "status": {"type": "string", "enum": ["confirmed", "unknown"]},
                "job_id": {"type": "string"},
                "url": {"type": "string"},
                "text": {"type": "string"},
            },
            ["status", "job_id", "url", "text"],
            ["list_pages", "select_page", "take_snapshot", "click", "wait_for"],
        )

    def close_page(self, tab_id):
        raise ValueError("Filled tabs are preserved; close only after verified submission")


class BrowserBridge:
    """MCP bridge whose native tab list and selected-file readback bind proof.

    Generic native DOM primitives remain available for ordinary form controls.
    Observed Workday controls require native keyboard/blur and persisted Review
    readback. Unsupported dynamic controls stay blocked with their tab held.
    """

    def __init__(self, client=None, *, root=None, engine=None, debug_url=None):
        if client is None or isinstance(client, (str, Path)):
            if engine is None or debug_url is None:
                raise ValueError("Select an engine and approved local browser MCP endpoint")
            client = EngineMCPClient(engine, debug_url)
        self.client = client
        self.tabs, self.uploads = {}, {}
        self.observed_uploads = {}
        self.restore_errors = {}

    @property
    def native_uploads(self):
        return isinstance(self.client, EngineMCPClient)

    def _native(self, tab_id):
        pages = self.client.list_pages()
        matches = [page for page in pages if str(page.get("id")) == str(tab_id)]
        if len(matches) != 1:
            raise ValueError("Held browser tab is closed or ambiguous")
        return matches[0]

    def open(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Application URL must be HTTP(S)")
        opened = self.client.new_page(url)
        tab_id = str(opened["id"])
        self.tabs[tab_id] = {"url": url}
        if _url_identity(self._native(tab_id)["url"]) != _url_identity(url):
            raise HeldPageError("Complete sign-in/redirect in the held tab, then apply its job ID again", tab_id)
        return tab_id

    def _script(self, operation, ats="greenhouse", answers=None):
        if ats not in ("greenhouse", "rippling", "workday"):
            raise ValueError("Unsupported ATS requires manual interaction")
        directory = Path(__file__).with_name("adapters")
        code = (
            (directory / "common.cjs").read_text(encoding="utf-8")
            + "\n"
            + (directory / (ats + ".cjs")).read_text(encoding="utf-8")
        )
        adapter = {"greenhouse": "AutoApplyGreenhouse", "rippling": "AutoApplyRippling", "workday": "AutoApplyWorkday"}[
            ats
        ]
        if operation == "fill":
            action = (
                f"return await globalThis.{adapter}.fill(document, {json.dumps(answers or {}, ensure_ascii=False)});"
            )
        else:
            action = (
                f"const result = globalThis.{adapter}.audit(document);"
                + """
for (const field of result.fields) {
  const element = globalThis.AutoApplyDOM.resolve(document, field.id);
  if (element?.type === 'file') {
    field.files = [];
    for (const file of element.files || []) {
      const bytes = await file.arrayBuffer();
      const digest = await crypto.subtle.digest('SHA-256', bytes);
      const sha256 = Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, '0')).join('');
      field.files.push({name: file.name, size: file.size, sha256});
    }
  }
  if (field.type !== 'summary') field.observed = Boolean(element);
}
result.attachments = result.fields.filter(field => field.type === 'summary' && /^(resume|resume pdf)$/i.test(field.label))
  .map(field => ({name: field.value, observed: true, evidence: field.evidence}));
result.url = location.href;
return result;"""
            )
        return "async () => {\n" + code + "\n" + action + "\n}"

    def fill(self, tab_id, profile, job, pdf=None):
        self.tabs.setdefault(str(tab_id), {"url": job["url"]}).update(job=copy.deepcopy(job))
        self._native(tab_id)
        if isinstance(self.client, EngineMCPClient):
            # One engine-assisted fill operation owns scan, native interactions
            # and persisted Review readback; no duplicate pre-fill LLM pass.
            result = self.client.interact(tab_id, profile, job, [], pdf=pdf)
            if result.get("status") == "filled" and pdf is not None:
                path = Path(pdf)
                receipt = result.get("upload_receipt")
                expected = {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                if not isinstance(receipt, dict) or any(receipt.get(key) != val for key, val in expected.items()):
                    raise ValueError("Observed Resume upload receipt is missing or differs from selected PDF")
                self.uploads[str(tab_id)] = copy.deepcopy(receipt)
                self.observed_uploads[str(tab_id)] = copy.deepcopy(receipt)
            return result
        # Map only supported confirmed facts; all other questions require an
        # exact scoped answer and are left to the native-agent interaction.
        scanned = self.client.evaluate_script(tab_id, self._script("audit", job.get("ats", "greenhouse")))
        if "blocked" in scanned and "fields" not in scanned:
            return scanned
        answers = {}
        from .profile import resolve_field

        for field in scanned.get("fields", []):
            supplied = resolve_field(profile, field, job)
            if supplied.get("status") == "confirmed":
                answers[field["id"]] = supplied
        result = self.client.evaluate_script(tab_id, self._script("fill", job.get("ats", "greenhouse"), answers))
        return result

    def upload_file(self, tab_id, path):
        path = Path(path).resolve()
        if not path.is_file() or not path.read_bytes().startswith(b"%PDF-"):
            raise ValueError("Upload requires an existing PDF")
        self._native(tab_id)
        self.client.upload_file(tab_id, str(path))
        self.uploads[str(tab_id)] = {
            "name": path.name,
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def snapshot(self, tab_id, job_id):
        if str(tab_id) in self.restore_errors:
            raise ValueError("Selected PDF missing/unreadable")
        native = self._native(tab_id)
        binding = self.tabs.get(str(tab_id))
        if binding is None:
            # Restarted commands recover the binding from the job ledger, not
            # from an engine's page_id assertion.
            raise ValueError("Restore the held job binding before reading browser proof")
        if _url_identity(native["url"]) != _url_identity(binding["url"]):
            raise ValueError("Native current URL belongs to another application")
        live = self.client.evaluate_script(
            tab_id, self._script("audit", binding.get("job", {}).get("ats", "greenhouse"))
        )
        if not isinstance(live, dict) or not isinstance(live.get("fields"), list):
            raise ValueError("Live form readback unavailable")
        if _url_identity(self._native(tab_id)["url"]) != _url_identity(native["url"]):
            raise ValueError("Browser navigated during readback")
        observed = copy.deepcopy(live)
        observed.update(
            tab_id=str(tab_id),
            page_id=str(tab_id) + "@" + _url_identity(native["url"]),
            url=native["url"],
            job_id=job_id,
        )
        expected = self.uploads.get(str(tab_id))
        observed["resume_sha256"] = None
        for field in live["fields"]:
            if field.get("type") == "summary":
                validate_quote(live.get("text"), field.get("evidence"), field.get("label"), field.get("value"))
        if expected:
            files = [file for field in live["fields"] if field.get("type") == "file" for file in field.get("files", [])]
            matching = [file for file in files if all(file.get(key) == value for key, value in expected.items())]
            receipt = self.observed_uploads.get(str(tab_id))
            if not matching and receipt:
                for attachment in live.get("attachments", []):
                    validate_quote(live.get("text"), attachment.get("evidence"), value=attachment.get("name"))
                    if (
                        attachment.get("observed") is True
                        and attachment.get("name") == receipt.get("name")
                        and all(attachment.get(key, value) == value for key, value in receipt.items())
                    ):
                        matching.append(receipt)
            if not matching:
                raise ValueError("Fresh attachment evidence does not match an observed upload receipt")
            observed["resume_sha256"] = matching[0]["sha256"]
        return observed

    def restore(self, record):
        tab_id = str(record["tab_id"])
        self.tabs[tab_id] = {"url": record["url"], "job": record}
        if record.get("upload_receipt"):
            self.observed_uploads[tab_id] = copy.deepcopy(record["upload_receipt"])
        if record.get("pdf"):
            path = Path(record["pdf"])
            try:
                self.uploads[tab_id] = {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            except OSError as error:
                self.uploads.pop(tab_id, None)
                self.restore_errors[tab_id] = str(error)
                return {"restored": True, "pdf_ok": False}
        self.restore_errors.pop(tab_id, None)
        return {"restored": True, "pdf_ok": True}

    def submit(self, tab_id, job_id):
        if str(tab_id) in self.restore_errors:
            raise ValueError("Selected PDF missing/unreadable")
        native = self._native(tab_id)
        binding = self.tabs.get(str(tab_id))
        if not binding or _url_identity(native["url"]) != _url_identity(binding["url"]):
            raise ValueError("Live job changed before explicit Submit")
        if not isinstance(self.client, EngineMCPClient):
            raise ValueError("Explicit submit needs an approved runtime browser connector")
        evidence = self.client.submit(tab_id, job_id)
        if evidence.get("status") == "confirmed":
            receipt = self._native(tab_id)
            if (
                evidence.get("job_id") != job_id
                or not evidence.get("text")
                or _url_identity(evidence.get("url", "")) != _url_identity(receipt["url"])
                or urlsplit(receipt["url"]).netloc != urlsplit(binding["url"]).netloc
            ):
                raise ValueError("Submit receipt lacks native matching job/page evidence")
        return evidence

    def close(self, tab_id):
        raise ValueError("Filled application tabs are preserved until confirmed submission")


def mcp_config(engine, debug_url, existing=None):
    parsed = urlsplit(debug_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Chrome debugging must use a loopback HTTP endpoint")
    if (
        parsed.username
        or parsed.password
        or not parsed.port
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or re.fullmatch(r"http://(?:127\.0\.0\.1|localhost|\[::1\]):[0-9]+/?", debug_url) is None
    ):
        raise ValueError("Use a plain loopback debugging URL with an explicit port")
    if engine not in ("claude", "codex"):
        raise ValueError("Unsupported engine")
    key = "mcpServers" if engine == "claude" else "mcp_servers"
    result = copy.deepcopy(existing or {})
    servers = result.setdefault(key, {})
    name = "autoapply-local-browser"
    if name in servers:
        raise ValueError(f"MCP server {name} already exists; existing configuration was preserved")
    servers[name] = {
        "command": "npx",
        "args": ["-y", f"chrome-devtools-mcp@{MCP_VERSION}", f"--browserUrl={debug_url}"],
    }
    if engine == "codex" and (os.name == "nt" or platform.system() == "Windows"):
        servers[name].update(
            command="cmd",
            args=["/c", "npx", *servers[name]["args"]],
            env={
                "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
                "PROGRAMFILES": os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            },
            startup_timeout_sec=20,
        )
    return result
