"""Terminal part of setup: language, engine login/quota, quota stop percent, resumes, jobs per run, Gmail.

Every answer is written to ``config.json`` as soon as it is given, so a stopped
setup keeps what was answered and a rerun offers the current values as defaults.
Resume PDFs are copied into ``resumes/`` of the runtime root and tagged with
roles for ``resume_choice``. The Gmail step opens the tool's clean Chrome
profile, waits for the user to sign in to Gmail there and scans six months of
confirmation emails into the tracker. The last step hands the terminal to the selected
engine with its ``/setup`` command for the native-popup part.
"""

import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from .engines import resolve_executable
from .storage import atomic_write_json, read_json
from .terminal_text import MESSAGES

ENGINES = ("claude", "codex")
LANGUAGES = ("en", "vi")
SETUP_PROMPTS = {"claude": "/setup", "codex": "$autoapply-local setup"}


class SetupStopped(ValueError):
    """The user quit or input ended; answers given so far are saved."""


def held_tab_cap(memory_mb):
    """Held tabs the free RAM allows: 2 GB kept for the system, about 512 MB per tab, 1 to 10."""
    return max(1, min(10, (int(memory_mb or 0) - 2048) // 512))


def launch_engine(engine, cwd, runner=subprocess.run):
    """Open the engine interactively in this terminal, starting its setup command."""
    return runner(resolve_executable(engine) + [SETUP_PROMPTS[engine]], cwd=cwd, shell=False).returncode


class Wizard:
    def __init__(self, root, ask, out, probe, memory_mb):
        self.root = Path(root).resolve()
        self.path = self.root / "config.json"
        self.ask_line, self.out, self.probe, self.memory_mb = ask, out, probe, memory_mb
        self.config = read_json(self.path) if self.path.exists() else {}
        self.language = self.runtime.get("language") if self.runtime.get("language") in LANGUAGES else "en"

    @property
    def runtime(self):
        return self.config.setdefault("runtime", {})

    def text(self, key, **values):
        return MESSAGES[self.language][key].format(**values)

    def say(self, key, **values):
        print(self.text(key, **values), file=self.out, flush=True)

    def save(self):
        atomic_write_json(self.path, self.config)

    def ask(self, prompt):
        try:
            return self.ask_line(prompt).strip()
        except EOFError:
            raise SetupStopped(self.text("stopped")) from None

    def choose(self, key, options, current):
        """Numbered choice; blank keeps the current value (or the first option)."""
        default = options.index(current) + 1 if current in options else 1
        while True:
            answer = self.ask(f"{self.text(key)} [{default}]: ")
            if not answer:
                return options[default - 1]
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
            self.say("invalid_choice", count=len(options))

    def number(self, key, current, default, low, high):
        default = current if isinstance(current, int) and low <= current <= high else default
        while True:
            answer = self.ask(f"{self.text(key)} [{default}]: ")
            if not answer:
                return default
            if answer.isdigit() and low <= int(answer) <= high:
                return int(answer)
            self.say("invalid_number", low=low, high=high)

    def yes(self, key, default):
        answer = self.ask(f"{self.text(key)} [{'Y/n' if default else 'y/N'}]: ").casefold()
        return default if not answer else answer[0] in ("y", "c")

    def step_language(self):
        self.language = self.choose("language", LANGUAGES, self.runtime.get("language"))
        self.runtime["language"] = self.language
        self.save()

    def step_engine(self):
        engine = self.choose("engine", ENGINES, self.runtime.get("apply_engine"))
        while True:
            self.say("probing", engine=engine)
            report = self.probe(engine, cwd=self.root)
            status = report.get("status")
            if status in ("ready", "quota_exhausted"):
                break
            if status == "login_required":
                from .quota import login_hint

                self.say("login_required", hint=login_hint(engine))
            else:
                self.say("engine_problem", engine=engine, status=status)
            if self.ask(self.text("retry")).casefold().startswith("q"):
                raise SetupStopped(self.text("stopped"))
        if status == "quota_exhausted":
            self.say("quota_exhausted")
        elif report.get("five_hour_pct") is None and report.get("seven_day_pct") is None:
            self.say("quota_unknown")
        else:
            unknown = "?"
            self.say(
                "quota",
                five=unknown if report.get("five_hour_pct") is None else report["five_hour_pct"],
                week=unknown if report.get("seven_day_pct") is None else report["seven_day_pct"],
            )
        self.runtime["apply_engine"] = engine
        self.save()

    def step_quota_stop(self):
        self.runtime["quota_stop_percent"] = self.number(
            "quota_stop", self.runtime.get("quota_stop_percent"), 90, 1, 100
        )
        self.save()

    def _pdf(self, first):
        while True:
            answer = self.ask(self.text("resume_path")).strip("\"'")
            if not answer:
                if first:
                    self.say("resume_needed")
                    continue
                return None
            path = Path(answer).expanduser()
            try:
                valid = path.is_file() and path.read_bytes()[:5] == b"%PDF-"
            except OSError:
                valid = False
            if valid:
                return path.resolve()
            self.say("resume_invalid")

    def _tags(self):
        while True:
            tags = [tag.strip() for tag in self.ask(self.text("resume_tags")).split(",") if tag.strip()]
            if tags:
                return list(dict.fromkeys(tags))
            self.say("tags_needed")

    def _copy(self, source, used):
        folder = self.root / "resumes"
        folder.mkdir(parents=True, exist_ok=True)
        target, count = folder / source.name, 1
        while target in used or (target.exists() and target.read_bytes() != source.read_bytes()):
            count += 1
            target = folder / f"{source.stem}-{count}{source.suffix}"
        if target.resolve() != source:
            shutil.copyfile(source, target)
        used.add(target)
        return target.resolve()

    def step_resumes(self):
        resumes = self.config.get("resumes") or {}
        if resumes.get("main_pdf"):
            names = [Path(resumes["main_pdf"]).name] + [
                Path(item.get("path", "")).name for item in resumes.get("alternatives") or [] if isinstance(item, dict)
            ]
            self.say("resumes_current", names=", ".join(names))
            if self.yes("resumes_keep", True):
                return
        chosen, used = [], set()
        while True:
            source = self._pdf(first=not chosen)
            if source is None:
                break
            chosen.append((self._copy(source, used), self._tags()))
            if not self.yes("resume_more", False):
                break
        resumes = {key: value for key, value in resumes.items() if key not in ("role_mapping", "alternatives")}
        (main, main_tags), rest = chosen[0], chosen[1:]
        resumes.update(main_pdf=str(main), main_tags=main_tags,
                       alternatives=[{"id": path.stem, "path": str(path), "tags": tags} for path, tags in rest])
        self.config["resumes"] = resumes
        self.save()

    def step_jobs(self):
        self.runtime["jobs_per_run"] = self.number("jobs_per_run", self.runtime.get("jobs_per_run"), 10, 1, 100)
        cap = held_tab_cap(self.memory_mb)
        self.config.setdefault("resources", {})["held_tab_cap"] = cap
        self.say("held_cap", cap=cap)
        self.save()

    def _gmail_state(self, status, **values):
        self.config["gmail"] = {**(self.config.get("gmail") or {}), "status": status, **values}
        self.save()
        return status

    def step_gmail(self, gmail):
        """Open the clean profile, wait for Gmail sign-in, add six months of confirmations to the tracker."""
        if not self.yes("gmail_connect", True):
            return self._gmail_state("skipped")
        try:
            profile = gmail.open()
        except (OSError, ValueError) as error:
            self.say("gmail_failed", error=error)
            return self._gmail_state("unavailable")
        self.say("gmail_opened", profile=profile)
        while not gmail.signed_in():
            if self.ask(self.text("gmail_wait")).casefold().startswith("s"):
                return self._gmail_state("skipped")
            if gmail.signed_in():
                break
            self.say("gmail_not_signed_in")
        from .mail_history import HISTORY_DAYS, import_confirmations
        from .storage import Store

        self.say("gmail_scanning", days=HISTORY_DAYS)
        try:
            rows = gmail.history(progress=lambda page: self.say("gmail_page", page=page))
        except (OSError, ValueError, RuntimeError) as error:
            self.say("gmail_failed", error=error)
            return self._gmail_state("signed_in")
        report = import_confirmations(Store(self.root), rows)
        self.say("gmail_history", added=report["added"], duplicates=report["duplicates"])
        return self._gmail_state("signed_in", history_days=HISTORY_DAYS, history_added=report["added"],
                                 history_scanned_at=date.today().isoformat())


def run(root, *, ask=input, out=None, probe=None, launch=None, memory_mb=None, gmail=None):
    """Run steps 1-5, 7-8 and 9 of terminal setup.

    ``launch(engine, cwd)`` None means print the next step; ``gmail`` None skips
    the Gmail step (the CLI passes a ``gmail_tab.GmailSession``).
    """
    out = out if out is not None else sys.stdout
    if probe is None:
        from .quota import probe
    if memory_mb is None:
        from .browser import available_memory_mb

        memory_mb = available_memory_mb()
    wizard = Wizard(root, ask, out, probe, memory_mb)
    for step in (wizard.step_language, wizard.step_engine, wizard.step_quota_stop, wizard.step_resumes, wizard.step_jobs):
        step()
    gmail_status = wizard.step_gmail(gmail) if gmail is not None else None
    engine = wizard.runtime["apply_engine"]
    summary = {
        "language": wizard.language,
        "engine": engine,
        "quota_stop_percent": wizard.runtime["quota_stop_percent"],
        "jobs_per_run": wizard.runtime["jobs_per_run"],
        "held_tab_cap": wizard.config["resources"]["held_tab_cap"],
        "config": str(wizard.path),
    }
    if gmail_status is not None:
        summary["gmail"] = gmail_status
    command = SETUP_PROMPTS[engine]
    if launch is None:
        return {"status": "ready", **summary, "next": wizard.text("next", engine=engine, command=command, root=wizard.root)}
    wizard.say("launching", engine=engine, command=command)
    return {"status": "launched", **summary, "engine_exit": launch(engine, wizard.root)}
