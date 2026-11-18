# AutoApply Local 0.1.0a1 — experimental

Quick apply for internship and new-grad job seekers, international students first,
on Windows and macOS. It finds jobs on public ATS boards, fills each application
with your own resume PDF (the right one for the role), and leaves the filled form
open in a browser tab. The tool never clicks Submit: you review and submit each
held form yourself. A local tracker is fed by the forms you submitted and by
confirmation emails in your Gmail tab.
English and Vietnamese instructions: [Tiếng Việt](README.vi.md).

The flow in short: install once, answer the terminal setup, confirm your facts in
your AI coding engine (Claude Code or Codex), then type `apply` whenever you want
a batch of applications filled.

## Status and limits

Offline unit, synthetic DOM and installed-package checks are automated. Live ATS,
Gmail, both-engine end-to-end operation, macOS hardware and an 8 GB machine have
**not** been validated. This is an experimental release, not a stable automation
claim. Unsupported controls and account challenges leave the form open for you.
macOS has not been tested live: on macOS only the offline tests and the CI install
job run.

Native browser fill still carries
real-engine risks and needs a live ATS gate before it can be considered stable.

## Install

One line. Windows PowerShell:

```text
irm https://raw.githubusercontent.com/tlamDson/autoapply-local/main/install.ps1 | iex
```

macOS:

```text
curl -fsSL https://raw.githubusercontent.com/tlamDson/autoapply-local/main/install.sh | bash
```

Or clone the [repository](https://github.com/tlamDson/autoapply-local) and run the
installer from it (`./install.sh` on macOS):

```text
git clone https://github.com/tlamDson/autoapply-local.git
cd autoapply-local
./install.ps1
```

The script clones the repository into `./autoapply-local` (or uses the checkout it
runs from). It checks Python 3.11+, git, Node.js/npm and Google Chrome. When one is
missing it prints the exact `winget install ...` or `brew install ...` command and
exits with code 3; it never installs system software itself. With everything
present it creates `.venv` inside the repository, runs `pip install -e .` and runs
`setup` once, which creates the runtime folder `<repo>/user/` with its local
commands, skill and `autoapply-local.py` launcher. Rerunning the installer is safe:
the existing `.venv` is reused and setup keeps every file you edited. Pass
`--no-setup` to install only. Nothing is written outside the repository; all your
data stays in `<repo>/user/` (gitignored, see [Your data](#your-data)).

Manual install, Windows PowerShell:

```powershell
python -m venv .venv
& .venv/Scripts/Activate.ps1
python -m pip install .
python -m autoapply_local --user-dir ./user setup
Set-Location user
```

macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m autoapply_local --user-dir ./user setup
cd user
```

Every command below runs from `<repo>/user/` with the repository's `.venv`
activated, as `python autoapply-local.py COMMAND`. That generated launcher always
uses its own folder as the runtime root. The source launcher in the repository
root (`python autoapply-local.py` there) also defaults to `<repo>/user/`; with no
arguments it runs `setup`. For an installed module or entry point, put
`--user-dir PATH` before the command: the runtime root is `--user-dir`, then
`AUTOAPPLY_LOCAL_HOME`, then the detected source checkout's `user/`, then
`~/.autoapply-local` outside a source checkout. Existing runtimes are never moved.

Quick apply uses your existing PDFs and does not need TeX; `source extract` on a
PDF needs Poppler's `pdftotext`. Dependencies are pinned in `pyproject.toml` and
`requirements.txt`. The browser connector pins chrome-devtools-mcp 0.14.0; npm may
download it when you first approve the connector.

## Setup, part 1: terminal

Run the terminal part in a plain terminal, before any engine is open (rerun it any
time; it offers your current answers as defaults):

```text
python autoapply-local.py setup --terminal
```

1. Language: English or Vietnamese.
2. Engine: Claude Code or Codex. One small call checks that you are signed in and
   shows the 5-hour and weekly quota used. Not signed in: it prints how to sign in
   and waits for you to retry or quit. After 3 failed checks it shows the engine's
   error and stops setup unless you type r to retry.
3. Quota stop percent (default 90): the share of the engine's 5-hour or weekly
   quota at which `apply` stops (see [Daily use](#daily-use-apply)). It is saved
   in `config.json`.
4. Resume PDFs: add one or more and tag each with roles (SWE, Data, ML or any
   text). They are copied into `resumes/` of the runtime root and become
   `main_pdf`/`main_tags` and `alternatives` in `config.json`.
5. Jobs per run (default 10). The held-tab cap is computed from free RAM and shown.
6. Gmail: the tool opens its own clean Chrome profile (`chrome/lane-1`) on Gmail;
   you sign in there yourself. It then searches the last 183 days for
   application-confirmation emails and adds each application to the tracker once.
   You can skip this step.
7. The selected engine opens in this terminal with `/setup` (Codex:
   `$autoapply-local setup`) for part 2. `--no-launch` prints that step instead.

Answers are saved to `config.json` as soon as you give them, so a stopped setup
keeps what you answered. Inside an engine, never run `setup --terminal`.

## Setup, part 2: in your engine

The engine's `/setup` command (Codex: `$autoapply-local setup`) asks the rest
through the engine's own native question popups, up to three questions at a
time. No JSON editing is needed.

1. Facts read from your resumes, one group at a time (contact, education,
   experience, skills), each with Confirm / Edit.
2. What a resume lacks: work authorization, sponsorship need, address, EEO
   (declinable), recruiting season and target roles. Skip or unknown never
   becomes No; authorization, visa, sponsorship and EEO are never guessed from a
   resume.
3. Job filters derived from those answers, with Confirm / Edit: needing
   sponsorship drops "no sponsorship", U.S. citizen, clearance and ITAR jobs;
   season and job types shape the title filter (for example interns only, no
   senior or full-time titles); postings within 7 days; your excluded companies.
   A filter you edited stays edited when answers change later.
4. A saved preview of everything, then a native Confirm / Keep draft choice. Only
   your explicit Confirm saves the facts.
5. An offline demo form (`demo`: a local page with no Submit control, in the
   tool's own Chrome) proves the stack works.
6. A first `fetch`, whose `report.summary` says how many jobs from how many boards
   passed your filters. Setup is done.

Behind the popups the engine runs these commands; the answer file is written from
your actual popup replies, not typed by you (`claude` is the UI for Claude Code's
AskUserQuestion; Codex uses `codex` or `codex-async`):

```text
python autoapply-local.py setup --guided --ui claude
python autoapply-local.py setup --answer user/setup-response.json --ui claude
python autoapply-local.py setup --finish --ui claude
python autoapply-local.py setup --confirm
python autoapply-local.py demo
python autoapply-local.py fetch
```

The engine discovers its real native question tool first. Without a permitted one
it uses `--ui none`, which returns `ui_required` and keeps your progress so you can
reopen a supported engine or mode. A timeout, an interruption or a preselected
default is never an answer, and there is no chat-question fallback.
`setup --guided --edit QUESTION_ID --ui UI` revisits one question. Existing
education, experience and authorization records have a numbered Edit an existing
record picker; Cancel keeps the original record. If your confirmed facts change
while a draft is open, the engine offers Reload latest confirmed profile / Keep
current draft; only an explicit Reload runs `setup --restart --ui UI`, which backs
up the old draft and starts a fresh session (finish and confirm a new preview).

Job sources are the shipped public boards in two groups, S&P 500 (`sp500`) and
unicorns (`unicorn`). `boards groups` lists them, `boards group NAME on|off` turns
a group off or on without deleting it, and `boards import PATH` previews your own
boards, then `--confirm` adds them; your boards are always fetched. These sources
are no eligibility or sponsorship guarantee.

```text
python autoapply-local.py boards groups
python autoapply-local.py boards group unicorn off
python autoapply-local.py boards import examples/boards.json
python autoapply-local.py boards import examples/boards.json --confirm
```

For advanced editing, `user/profile-intake.md` holds every fact group in one form:
preview it with `profile update PATH`, then add `--confirm`. `source extract PATH`
turns a PDF/TEX/DOCX into draft text for checking; a draft is never a confirmed
fact. A profile change invalidates dependent answers and reviews.

## Daily use: apply

Nothing runs on a schedule. A run starts only when you type `apply [N]`: `/apply 5`
in Claude Code, `$autoapply-local apply 5` in Codex, or in a terminal:

```text
python autoapply-local.py apply 5
```

Without N it fills the jobs-per-run number from setup. One run, in order:

1. Track: held tabs now on their ATS confirmation page and confirmation emails in
   the Gmail tab become tracker rows (see `track` under [Commands](#commands)).
   Filled but unsubmitted tabs are listed, kept and counted toward the tab cap. A
   held form whose tab Chrome lost (for example after a restart) is reopened at the
   same URL and filled again.
2. Fetch new jobs from the active boards.
3. Filter with your confirmed filters and skip jobs already in the tracker or in
   the Gmail/imported history (same company and role).
4. Pick the resume: a PDF whose role tag is a whole word of the job title; several
   match → the one naming the most of the job description's technology keywords
   (no model; ties keep configuration order); none matches and you have more than
   one → `apply` asks once per role in the terminal and remembers it in
   `user/resume-choices.json`. Each choice and its reason are printed before filling.
5. Fill with the ATS script driver: Greenhouse, Workday, Rippling, Ashby, Lever and
   Workable. Workday signs in with the account stored for that Workday site in
   `secrets.json`, creates the account when sign-in fails, and types the
   verification code read from the Gmail tab. No setup step stores that account
   yet; without one the Workday tab is held for you to sign in.
6. Required questions with no confirmed answer do not stop the run: they are
   collected for the whole run and the result shows `questions.pending`. The engine
   then asks them in one native popup (the same question from several jobs is asked
   once), saves your answers to `user/answer-bank.json` and fills them into the
   held tabs. A banked answer is reused only for the same question wording,
   options, country and job type; it crosses employers only for a question about
   you alone (education, skills, languages, clubs, projects). Authorization,
   sponsorship, consent, EEO, pay and employer-specific questions are never
   inferred. A refill counts only when a fresh readback of the tab shows your
   answer; otherwise `questions --refill` retries it.
7. Never Submit. You review each held tab and submit it yourself.
8. Jobs on an ATS with no driver (iCIMS, SmartRecruiters, Taleo, ...) are never
   opened: they go to a submit-by-hand list with link and suggested resume. A
   CAPTCHA, sign-in or unsupported control holds the tab; solve it there and run
   `apply ID` for that job.
9. Quota guard: before each job one small probe call reads the engine's 5-hour
   and weekly usage (every third job, or every job within 10 points of the stop).
   When either reaches the quota stop percent from setup, or the engine reports
   its usage limit, the run stops: held tabs stay open, unstarted jobs stay
   pending for the next run, and the table ends with the reason and how many jobs
   are left (`quota` and `left` in the JSON). Unknown usage never stops a run.
10. The end of the run prints a table on stderr, one row per job (company, role,
   resume, tab, fields to check), then the confirmations found in Gmail, skipped
   jobs with reasons, the submit-by-hand list and the questions still unanswered.
   Final stdout stays JSON.

```text
python autoapply-local.py questions --ui claude
python autoapply-local.py questions --answer user/question-answers.json
python autoapply-local.py questions --refill
python autoapply-local.py status
python autoapply-local.py review all
python autoapply-local.py submitted greenhouse:acme:4012345
python autoapply-local.py track
python autoapply-local.py apply greenhouse:acme:4012345
```

A popup holds as many questions as the engine's tool takes at once (Claude 4,
Codex 3); the rest wait for the next popup and `deferred` says how many. Skip,
timeout or no native tool keeps them pending. A tab that shows another
application is never written to.

When the held-tab cap is reached, submit held forms by hand and record them with
`submitted ID,ID` (or let `track` find them) before opening more. Do not close
unsubmitted forms. Named job IDs are not deduplicated. Analysis failures before
browser actions return newly claimed jobs to `pending` for a manual retry, while
held tabs stay open.

## Update

```text
python autoapply-local.py update
```

`update` copies the runtime folder `<repo>/user/` (except `backups/`, Chrome
profiles and `secrets.json`) to `<repo>/user/backups/<date-time>/`, then runs
`git pull --ff-only` (without git it downloads the latest release zip, which never
writes `user/`, `.venv/` or `.git/`), reinstalls with `pip install -e .`, runs each
pending data migration once and prints what changed. `apply` prints one line on
stderr when a newer release exists; set `AUTOAPPLY_LOCAL_NO_UPDATE_CHECK=1` to
skip that lookup.

## Your data

Everything you add or the tool records stays in the runtime root, `<repo>/user/`
by default, which Git ignores and release artifacts exclude. Paths below are
relative to that root:

- `config.json`, `profile.json`, `answers.json`: your settings, confirmed facts and
  confirmed answers.
- `resumes/`: the resume PDFs you added in terminal setup.
- `user/resume/`: a drop folder for resume PDFs and resume sources (TEX, DOCX).
- `user/additional-document/`: a drop folder for transcripts, certificates, cover
  letters and other supporting documents.
- `user/answer-bank.json`, `user/resume-choices.json`: answers you gave to form
  questions and your resume choice per role.
- `tracker.xlsx` and `ledger.json`: the tracker (authoritative history) and job
  states.
- `secrets.json`: Workday account email and password per Workday site. It is
  never printed or logged, never backed up by `update`, and the release audit
  fails if Git tracks it.
- `chrome/`: the tool's own clean Chrome profiles; never use your everyday
  profile.
- `backups/`: copies made by `update`.

The two drop folders are created by setup with a short README each (created only
when absent; your documents and edited guides are kept). Dropping a file there
only stores it: it does not select a resume, extract or confirm facts, or upload
anything. Resumes used by `apply` are the ones added in terminal setup;
additional documents are never uploaded automatically.

There is no Gmail command and no OAuth. The only Gmail access is the Gmail tab of
the tool's own Chrome profile, where you signed in yourself: the tool searches it
and reads result rows (sender, subject, snippet, date). It never opens, sends,
drafts, archives or labels a message, verification emails are skipped, and
nothing but the confirmation subject, company, role and date is stored.
`history import PATH` previews a CSV/XLSX application history; `--confirm` imports
it with deduplication. Keep backups of the tracker and ledger.

## Commands

From the runtime root run `python autoapply-local.py COMMAND`. The local slash
commands and Codex skill use the same launcher. No command clicks Submit.

- `setup`: create or refresh the runtime (local commands, skill, launcher,
  document folders, browser config) without replacing your files.
  `setup --terminal [--no-launch]` is part 1; `setup --guided`, `--answer PATH`,
  `--finish`, `--confirm` and `--restart` with `--ui UI` are part 2 (one action
  at a time); `--edit QUESTION_ID` revisits a question.
- `boards list|groups`, `boards group NAME on|off`, `boards import PATH
  [--confirm]`: show, switch and add job sources.
- `demo`: fill the offline demo form in the tool's Chrome.
- `profile update PATH [--confirm]`: preview, then import, the advanced intake.
- `source extract PATH`: draft text from a PDF/TEX/DOCX under `user/drafts`.
- `history import PATH [--confirm]`: preview, then import, CSV/XLSX history.
- `doctor [ENGINE]`: check executables and dependencies; sign-in and connectors
  stay `not_verified`.
- `fetch [ENGINE] [--url URL]`: fetch the active boards, or one supported job URL.
- `apply [ENGINE] [N|ID,ID]`: one run as described in
  [Daily use](#daily-use-apply); `--quick N` still works and
  `--approve-browser-tools` approves the browser tools for this command only.
- `review ID,ID|all`: read held forms again and check job, tab, values, profile and
  PDF are current. It never submits or closes a form.
- `submitted ID,ID`: record held forms you submitted by hand; this writes the
  tracker row and frees the held-tab slot without touching the browser.
- `questions --ui UI`, `questions --answer PATH`, `questions --refill`: ask, save
  and refill required questions held by `apply`.
- `track`: record held forms you already submitted, from the ATS confirmation page
  in their tab (Greenhouse, Workday, Rippling, Lever) or a confirmation email in the
  Gmail tab naming the job's exact company (and role when the email has one),
  dated on or after the day the form was held. An email is matched only when no
  other held, filled or recently recorded job of that company could have sent it;
  otherwise it is held back. A Workday page that names no job waits for its email.
  Tabs still on their form are listed as waiting; tabs gone without evidence are
  listed with their URL. It never runs a script in a job tab and changes nothing
  when the tool's Chrome is not running.
- `status`: list held forms, counts and unresolved applications without an engine.
- `update`: back up, update and migrate (see [Update](#update)).

## Engines and browser approvals

Use one engine per task, the one chosen in setup or named on the command line;
there is no fallback engine and no model is pinned. Sign in to it through its
normal flow. Setup writes project-local `.mcp.json` and `mcp-codex.json` in the
runtime root without replacing existing files; open the runtime root in your
engine and approve the local browser MCP normally. Nothing changes global
settings or writes approval rules, and approvals are never bypassed.

For `apply`, `review` and `questions`, `--approve-browser-tools` approves only the
local browser MCP tools that each task requests, for that command only; it is
never saved and does not approve Gmail or analysis tools. Existing and managed
policy and permission denials still apply.

```text
python autoapply-local.py doctor
```

Claude external-tool tasks need CLI 2.1.295 or newer; older versions return
`unsupported_version`. Codex runs read-only, outside Git, with session-only
overrides that turn off inherited connectors, shell, native browser and code
execution; it probes the effective MCP inventory with `mcp list --json` first
and fails closed on unreadable or managed configuration it cannot verify.
Errors are reported as `login_required`, `quota_exhausted`, `timeout`,
`configuration`, `permission_required`, `unsupported_version`, `invalid_response`
or `engine_failure` without echoing sensitive CLI output. Probes are bounded to
10 seconds and tasks to 300 seconds; runtime failures exit 3, invalid input or
response exits 2. An earlier Windows smoke run of both engines does not validate
the current runtime.

One browser lane is the default; two or three need an explicit opt-in in the
intake. Admission checks free RAM and held tabs before every new job; the memory
rule is a heuristic, not an 8 GB benchmark. Each job keeps its lane, so later
commands find its tab again.

## Test and export

From the repository root:

```text
python -m unittest discover -s tests -p "test_*.py" -v
node --test tests/adapters.test.cjs tests/gmail.test.cjs
python scripts/check_release.py
```

Tests use temporary synthetic data and mocked engines, browser and network.
Source export uses `export-allowlist.json` plus content scanning, not `.gitignore`
alone; runtime data, credentials and build outputs are excluded. Synthetic test
sources ship in the public repository for CI; wheels and sdists exclude tests.
This is an experimental public prerelease; check
[Actions](https://github.com/tlamDson/autoapply-local/actions) for recorded CI results.
The workflow defines Windows and macOS jobs with Python 3.11 and 3.12. A second
job, install-smoke, runs `install.ps1` (Windows) or `install.sh` (macOS) on a clean
runner, then `scripts/ci_smoke.py`: doctor, terminal setup with scripted answers
and an offline stand-in for the engine CLI, a confirmed profile for an invented
candidate, and the offline demo form fill.

`pyproject.toml` is the source of the project name, version, dependencies and
wheel package data. `--artifacts DIR` audits every final wheel, sdist and source
ZIP in a directory (file inventory, shipped bytes, metadata, wheel RECORD hashes
and the privacy/path rules) and rejects stale artifacts; `--expect wheel,sdist`
is the default, `--expect wheel,sdist,zip` requires all three. `--source-zip DIR`
audits the source, then writes and audits a reproducible ZIP; it never
overwrites an artifact. From the repository root (the export and work
directories must not exist yet):

```text
python scripts/check_release.py --export ../autoapply-export-new
cd ../autoapply-export-new
python -m pip wheel --no-deps --no-build-isolation --wheel-dir ../autoapply-artifacts-new .
python -m build --sdist --no-isolation --outdir ../autoapply-artifacts-new
python scripts/check_release.py --source-zip ../autoapply-artifacts-new
python scripts/check_release.py --artifacts ../autoapply-artifacts-new --expect wheel,sdist,zip
python scripts/check_install.py --artifacts ../autoapply-artifacts-new --work-dir "../install space ünïcode new"
```

The build commands need the pinned build requirements and the `build` package.
`check_install.py` installs the wheel and the sdist into two fresh venvs from
outside the source tree; add `--wheelhouse PATH` for an offline dependency
wheelhouse. These commands document the procedure, not a completed result for
the current artifacts.

MIT license; see NOTICE for layout and dependency attribution.
