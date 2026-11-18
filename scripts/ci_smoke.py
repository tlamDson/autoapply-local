"""CI smoke after install.ps1 / install.sh: doctor, scripted setup, demo fill.

Run on a clean runner after the installer built ``.venv``. It drives the
installed launcher (``.venv`` Python + ``autoapply-local.py``) exactly as a user
would, but in a fresh runtime of its own (``<work-dir>/runtime``, or an empty
``--user-dir``). It only reads ``<repo>/user/config.json`` to confirm the
installer started setup; it never writes an existing runtime, and it stops only
the Chrome its own demo started, never one that may hold application forms.

0. ``setup`` creates the smoke's runtime, as the installer does.
1. ``doctor`` finds Chrome, Node and npx.
2. ``setup --terminal --no-launch`` with scripted stdin. An offline fake
   ``claude`` CLI on PATH answers the login/quota probe, so no engine account
   or network is needed; Gmail is skipped.
3. ``doctor`` again resolves the selected engine.
4. ``profile update --confirm`` with the setup's own intake, filled with an
   invented candidate (the engine part of setup needs native popups).
5. ``demo`` fills the offline demo form in the tool's Chrome; that Chrome is
   stopped afterwards. The demo page has no Submit control.
"""

import argparse
import json
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from install import venv_python  # noqa: E402

JOBS_PER_RUN = 3
CANDIDATE = {
    "first_name": "Alex",
    "last_name": "Rivera",
    "application_email": "alex.rivera@example.com",
    "phone": "+1 555 0100",
}
DEMO_FIELDS = ("first_name", "last_name", "email", "phone")
FAKE_ENGINE_JS = """
// Offline stand-in for the engine CLI: a signed-in account with quota left.
const rows = [
  {type: "rate_limit_event", rate_limit_info: {unifiedWindows: {
    five_hour: {utilization: 0.12}, seven_day: {utilization: 0.34}}}},
  {type: "result", subtype: "success", is_error: false, result: "ok"},
];
for (const row of rows) process.stdout.write(JSON.stringify(row) + "\\n");
"""


class SmokeError(RuntimeError):
    """A smoke step did not produce the expected result."""


def fake_engine(folder, system=None):
    """Write a fake ``claude`` CLI into ``folder`` in the layout the OS resolves."""
    folder = Path(folder)
    system = system or platform.system()
    folder.mkdir(parents=True, exist_ok=True)
    if system == "Windows":
        package = folder / "node_modules/@anthropic-ai/claude-code"
        package.mkdir(parents=True, exist_ok=True)
        (package / "package.json").write_text(
            json.dumps({"name": "@anthropic-ai/claude-code", "bin": {"claude": "cli.js"}}), encoding="utf-8")
        (package / "cli.js").write_text(FAKE_ENGINE_JS, encoding="utf-8")
        (folder / "claude.cmd").write_text(
            '@node "%~dp0node_modules\\@anthropic-ai\\claude-code\\cli.js" %*\r\n', encoding="utf-8")
        return folder / "claude.cmd"
    script = folder / "claude"
    script.write_text("#!/usr/bin/env node\n" + FAKE_ENGINE_JS, encoding="utf-8")
    script.chmod(0o755)
    return script


def write_resume(path):
    """A minimal one-page PDF for an invented candidate."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )
    return path


def setup_answers(resume):
    """Stdin for the terminal setup: English, Claude, default stop %, one SWE resume, jobs per run, no Gmail."""
    lines = ["1", "1", "", str(resume), "SWE", "n", str(JOBS_PER_RUN), "n"]
    return "\n".join(lines) + "\n"


def fill_intake(text):
    """Return the intake document with the invented candidate's contact facts filled in."""
    blocks = re.findall(r"```json\s*\n(.*?)\n```", text, re.S)
    if len(blocks) != 1:
        raise SmokeError("The setup intake has no single JSON block")
    data = json.loads(blocks[0])
    data["groups"]["contact"].update(CANDIDATE)
    return text.replace(blocks[0], json.dumps(data, indent=2, ensure_ascii=False), 1)


def last_json(text):
    """The JSON object a command printed last (terminal setup prints its prompts first)."""
    # A prompt answered from a pipe ends without a newline, so the result can start mid-line.
    decoder, text = json.JSONDecoder(), text.rstrip()
    index = text.rfind("{")
    while index >= 0:
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            value, end = None, -1
        if isinstance(value, dict) and end == len(text):
            return value
        index = text.rfind("{", 0, index)
    raise SmokeError("The command printed no JSON result")


def check_doctor(report, engine=None):
    missing = [key for key in ("chrome", "node", "npx") if not report.get(key)]
    if missing:
        raise SmokeError("doctor did not find: " + ", ".join(missing))
    if engine and (report.get("engine") != engine or report.get("executable_status") != "resolved"):
        raise SmokeError(f"doctor did not resolve the {engine} executable: {report.get('executable_status')}")


def check_setup(root, result):
    expected = {"status": "ready", "engine": "claude", "gmail": "skipped", "jobs_per_run": JOBS_PER_RUN}
    wrong = {key: result.get(key) for key, value in expected.items() if result.get(key) != value}
    if wrong:
        raise SmokeError(f"terminal setup result is unexpected: {wrong}")
    try:
        config = json.loads((Path(root) / "config.json").read_text(encoding="utf-8"))
        resume = Path(config["resumes"]["main_pdf"])
        tags = config["resumes"]["main_tags"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SmokeError(f"terminal setup saved no resume: {error!r}") from None
    if not resume.is_file() or resume.parent.resolve() != (Path(root) / "resumes").resolve() or tags != ["SWE"]:
        raise SmokeError("terminal setup did not copy and tag the resume")


def check_demo(result):
    if result.get("ok") is not True or "submit" in str(result.get("status", "")).casefold():
        raise SmokeError(f"demo fill failed: status {result.get('status')}")
    missing = [field for field in DEMO_FIELDS if field not in (result.get("filled") or [])]
    if missing:
        raise SmokeError("demo fill missed: " + ", ".join(missing))


def _kill(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _owner_pid(root):
    owner = Path(root) / "chrome/lane-1/autoapply-owner.json"
    try:
        pid = json.loads(owner.read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError, AttributeError):
        return None
    return pid if isinstance(pid, int) else None


def _stop_chrome(root, kill, before):
    """Stop the Chrome the demo started; a Chrome already running before it (``before``) may hold forms."""
    pid = _owner_pid(root)
    if pid is not None and pid != before:
        kill(pid)


def _is_empty(folder):
    return not folder.exists() or (folder.is_dir() and not any(folder.iterdir()))


def main(argv=None, *, run=subprocess.run, kill=_kill, out=None):
    out = out if out is not None else sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=ROOT, help="installed checkout (default: this one)")
    parser.add_argument("--user-dir", type=Path,
                        help="new, empty runtime root for the smoke (default: <work-dir>/runtime)")
    parser.add_argument("--work-dir", type=Path,
                        help="new, empty scratch folder for the runtime, fake engine, resume and intake")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    work = (args.work_dir or Path(tempfile.mkdtemp(prefix="autoapply-smoke-"))).resolve()
    root = (args.user_dir or work / "runtime").resolve()
    installed = repo / "user"
    python, launcher = venv_python(repo), repo / "autoapply-local.py"
    if not python.is_file() or not launcher.is_file():
        print(f"No installed .venv in {repo}; run install.ps1 / install.sh first.", file=out)
        return 1
    if not (installed / "config.json").is_file():
        print(f"No runtime in {installed}: the installer did not start setup.", file=out)
        return 1
    # The smoke writes a config, a profile and a Chrome profile: never into a runtime that holds anything.
    for label, folder in (("--work-dir", work), ("--user-dir", root)):
        if not _is_empty(folder):
            print(f"{label} {folder} must be a new, empty folder; the smoke never touches an existing runtime.",
                  file=out)
            return 2
    work.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key.upper() not in {"PYTHONHOME", "PYTHONPATH"}}
    env.update(PYTHONIOENCODING="utf-8", AUTOAPPLY_LOCAL_NO_UPDATE_CHECK="1")

    def cli(*command, stdin=None):
        print("$ autoapply-local.py " + " ".join(command), file=out, flush=True)
        done = run([str(python), str(launcher), "--user-dir", str(root), *command], cwd=str(repo), env=dict(env),
                   input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if done.returncode != 0:
            tail = "\n".join(str(done.stderr or "").splitlines()[-20:])
            raise SmokeError(f"{command[0]} exited {done.returncode}: {tail}")
        return last_json(str(done.stdout or ""))

    chrome_before, demo_started = None, False
    try:
        cli("setup")  # the same first setup the installer starts, in the smoke's own runtime
        check_doctor(cli("doctor"))
        fake_engine(work / "engine")
        env["PATH"] = str(work / "engine") + os.pathsep + env.get("PATH", "")
        resume = write_resume(work / "Alex_Rivera_Resume.pdf")
        check_setup(root, cli("setup", "--terminal", "--no-launch", stdin=setup_answers(resume)))
        check_doctor(cli("doctor"), engine="claude")
        intake = work / "intake.md"
        intake.write_text(fill_intake((root / "user/profile-intake.md").read_text(encoding="utf-8")),
                          encoding="utf-8")
        if cli("profile", "update", str(intake), "--confirm").get("applied") is not True:
            raise SmokeError("profile update did not apply the confirmed facts")
        chrome_before, demo_started = _owner_pid(root), True
        result = cli("demo")
        check_demo(result)
        print("Demo filled: " + ", ".join(result["filled"]), file=out)
    except (SmokeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"Smoke failed: {error}", file=out)
        return 1
    finally:
        if demo_started:
            _stop_chrome(root, kill, before=chrome_before)
    print("Install smoke passed.", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
