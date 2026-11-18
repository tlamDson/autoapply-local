# AutoApply Local

This experimental standalone product is a quick-apply tool for internship and
new-grad job seekers (international students first) on Windows and macOS: script
filling with the user's own resume PDFs, the right resume picked per job, held tabs
the user submits by hand, and a tracker fed by submitted-tab detection and Gmail
confirmation emails. Resume tailoring, a Submit command and any local model are out of
scope. Follow the user's selected engine per task without model pinning or
fallback. Use only confirmed facts and exact question-scoped answers. Never
infer work authorization, sponsorship or consent from student status or filters.

Install is `install.ps1` / `install.sh` (dependency check that prints the per-OS
install command, repo `.venv`, `pip install -e .`, then `setup`). All user data
lives in the runtime root, `<repo>/user/` by default (gitignored, never written
outside the repository): config, confirmed facts, `resumes/`, tracker, ledger,
`secrets.json`, Chrome profiles and backups. Setup also creates two drop folders
inside the runtime root: `user/resume/` (resume PDFs and TEX/DOCX sources) and
`user/additional-document/` (transcripts, certificates, cover letters); storing
a file there never selects a resume, confirms a fact or uploads anything.
`update` backs up the runtime root to `backups/<date-time>/` (without Chrome
profiles or secrets), runs `git pull --ff-only` or extracts the release zip,
reinstalls and runs each pending migration once.

The terminal part of setup, `setup --terminal`, runs in a plain terminal before any
engine opens: language, engine with one small login/quota probe call, quota stop
percent, resume PDFs tagged with roles (copied to `resumes/`) and jobs per run,
saved to `config.json`; then it opens the tool's clean Chrome profile
(`chrome/lane-1`) on Gmail for the user to sign in and scans 183 days of
confirmation emails into the tracker (deduplicated). It then hands the terminal to the selected engine with
`/setup` (Codex: `$autoapply-local setup`); that interactive handoff is not an
interview subprocess. Inside an engine, never run `setup --terminal`.

For first setup, including when the user only says setup, follow the native
onboarding loop in `.claude/commands/setup.md` or
`.codex/skills/autoapply-local/SKILL.md`. Bootstrap safely, discover the actual
question tool in the top-level host, then run `setup --guided --ui UI` through
the runtime's `python autoapply-local.py` launcher. Invoke its native packet,
wait for the user's real answer, map stable IDs, and pass a private response file
to `setup --answer PATH`. Finish previews a draft; only a native explicit Confirm
permits `setup --confirm` for the saved preview. No native tool or forbidden mode
means `--ui none` / `ui_required`, preserving progress. Never ask setup questions
as chat prose, launch an engine subprocess for the interview, change mode/engine
or infer an answer from timeout/preselection. The advanced intake remains an
optional editor/export. This native setup flow never permits Submit or Gmail
access; only the terminal Gmail step reads the Gmail tab.

Guided setup after terminal setup begins with quick steps: resume fact groups
(contact, education, experience, skills) for Confirm/Edit, then facts a resume
lacks (work authorization, sponsorship, address, EEO, season, roles), then job
filters derived only from explicit answers for Confirm/Edit. An unknown
sponsorship answer never produces a sponsorship filter.
The advanced walk then skips what it already has or cannot use: language,
engine and resumes saved by terminal setup, every tailoring question unless
the mode includes tailoring, the Gmail account, lookback and permission
questions unless Gmail is opted in (a gate never shares a popup with the
questions it turns off), and `international_student.status`, which takes the
answer to `authorization.current_status` when blank.
When changed answers reopen the filter review, a filter the user edited is
shown and kept as edited; only unedited filters follow the answers.
The confirmed intake records what the answers derived (`derived_filters`), so a
filter cleared in an earlier session stays cleared. Only an intake without that
record treats an empty filter as never derived and gives it the derived value.
An edited filter is also marked in the intake (`edited_filters`) and the mark
stays: an edit that later equals what changed answers derive, such as a cleared
filter while sponsorship is Unknown, is still kept when the answers change back.

Setup then fills an offline demo form (`demo`: a local page with no Submit
control, in the tool's own Chrome) to prove the stack works, and runs a first
`fetch` whose `report.summary` says how many jobs from how many boards passed
the filters. Job sources are the shipped public boards in two groups, S&P 500
(`sp500`) and unicorns (`unicorn`): `boards groups` lists them, `boards group
NAME on|off` turns a group off or on without deleting it, and `boards import
PATH` adds your own boards, which are always fetched.

If setup becomes stale, use the native Reload latest confirmed profile / Keep
current draft popup. Run `setup --restart --ui UI` only after explicit Reload;
Keep, timeout or no native tool preserves progress. Restart backs up old progress
and the generated intake inside the runtime, then creates a fresh session without
changing confirmed facts. Old answers/previews remain invalid. Existing education,
experience and authorization records have a numbered native picker and field
editor; Cancel keeps the original record, and unchanged facts retain metadata.

`apply` fills and holds; `review` checks fresh live evidence. The tool never
clicks a Submit control: no command, workflow, browser call or engine task may
request one, and the user submits each held form by hand, then records it with
`submitted ID,ID`, which frees its held-tab slot. `track` (also run first by
`apply`) records a held form whose tab shows the ATS confirmation page or whose
confirmation email is in the Gmail tab; it only lists or closes tabs already on
a confirmation page and never runs a script in a job tab. Preserve held forms for
sign-in, account creation, CAPTCHA, unknown questions, errors and manual
inspection. There is no Gmail command or OAuth. Gmail is read only in the Gmail
tab of the tool's own Chrome profile, signed in by the user, through the
read-only `adapters/gmail.cjs` (search and result rows); never open, send,
draft, archive or label a message, store a verification email, or persist
verification codes. Tests use synthetic Gmail DOM and rows only.

Required questions no confirmed fact or answer-bank entry covers are held,
collected for the whole `apply` run and asked once at the end: `questions --ui UI`
returns one native popup packet (a single native call; questions that do not
fit are `deferred` to the next popup); after the user's actual answers, `questions
--answer PATH` saves them to `user/answer-bank.json` and refills the held tabs.
Skip, timeout or no native tool keeps them pending; a failed refill is retried
with `questions --refill`. A banked answer is reused only for the same question
wording, options, country and job type, and crosses employers only for a
question known to be about the candidate alone (education, skills, languages,
clubs, projects). Every other question keeps employer and start date, and so
does any required checkbox, yes/no or agree/decline question, question that
refers to the employer or the role, and authorization, sponsorship, consent,
agreement, contact or data permission, EEO and pay question (including a money
amount in the options); it is never inferred. A refill never writes to a tab showing another application; Workday
is refilled by native engine interaction. A refilled field counts only when the
fresh readback of the tab shows the user's own answer for it (a review row is
read as an option label only); a wrong, empty, ambiguous or missing value keeps
that field for `questions --refill`.

Workday sign-in credentials live only in `user/secrets.json` (`autoapply_local/wd_cred.py`): never
print, log or echo a password, mask text with `wd_cred.redact`, and never track `user/` or any
secrets file; `scripts/check_release.py` fails when git tracks one.

The Workday driver (`workday_driver.py`) fills each step from confirmed facts, uploads the
selected resume on the step that asks for it and presses Next only through the guarded
`press()` of `adapters/workday_steps.cjs` (fixed actions, never a control that says Submit,
never on the last step); it stops on Review. Its account step (`workday_account.py`) runs over
loopback CDP in the held tab, never through an engine: Apply, Apply Manually, sign-in with the
stored credential, account creation when sign-in fails, and the one-time code read from the
subject/snippet of Gmail result rows. The credential and code are typed only on the page just
checked and only when the tab's host is the job's tenant; the code is typed and dropped, never
stored or returned. The tenant's account terms box is never ticked: an unticked box holds the
filled create-account form for the user. A resume upload counts only when the browser reports it
and a fresh audit shows the file. CAPTCHA, a link-only verification mail, a rejected code, another
host or an unknown page holds the tab.

Use temporary synthetic fixtures for tests. Never run real external engines,
browser automation or Gmail during offline tests. Run Python unittest discovery,
Node adapter tests and scripts/check_release.py from this release root. Do not
claim live ATS, Gmail, both-engine, macOS hardware or 8 GB validation from mocks.
macOS has not been tested live; README.md and README.vi.md say so.

## Implementation notes

- The runtime CLI returns exit 2 for invalid input or schema and exit 3 for a missing dependency or engine error; `doctor` reports only executables and leaves login/connector state unverified.
- The runtime root is chosen by `--user-dir` > `AUTOAPPLY_LOCAL_HOME` > the detected source checkout's `user/` (aa-04; formerly `.autoapply-local`) > `~/.autoapply-local`. A source checkout is recognized by `autoapply_local/__init__.py` and a readable `pyproject.toml`; the source launcher with no arguments runs setup next to itself; the generated runtime launcher keeps its own root.
- JSON schemas stay small and are checked recursively (type, required, properties, enum, items); a payload that does not match is rejected before use.
- `source extract` and `history import` are CLI entry points to existing workflows.
- Rewrites compare words in order, keep qualifiers and ownership, and refuse meaning changes without a source; an expired answer keeps its provenance with `valid=False`.
- Imported history with an unknown state is rejected at preview; known states are normalized to lowercase. Fixed sdist metadata has its own allowlist and is still content-scanned.
- A confirmed intake with `lanes` 2 or 3 opts in to several lanes; the runtime still checks RAM and keeps every unsubmitted tab.
- ZIP fixtures set `ZipInfo.filename` directly to keep backslashes on Windows, because `zipfile` normalizes names given as strings.
- Installer (aa-04): install.ps1/install.sh only locate Python 3.11+ and git, then hand off to scripts/install.py; a missing dependency prints its winget/brew command and exits 3, a failed pip install exits 4; system software is never installed.
- Rippling driver (aa-13): `rippling_driver.RipplingDriver` uploads the resume first, waits for Rippling's own parse, then writes confirmed facts into the Formsy model (`adapters/rippling.cjs`), so they win over the parse; every written fact is read back (`modelCheck`) until two checks in a row are clean, a fact a late parse changed is rewritten at most twice and then reported `value_overwritten`, and the final scan must still show each written value or it is reported the same way; location is typed, the old place id is cleared first, and only the single matching Places suggestion is picked (mousedown first, click only if needed, never inside a button, link, label or other activating control), succeeding only with a fresh place id and matching location text; anything unknown, ambiguous or still required-empty is reported and the tab is held. No adapter action clicks a button.
- Apply run (aa-19): `apply [ENGINE] [N|ID,ID]` (no N = `runtime.jobs_per_run`) runs track, reopens held forms whose tab Chrome lost (state back to pending, `previous_tab_id` kept; Chrome is started first when it was closed), fetches active boards, then `apply_run.pick`: pending jobs matching a Gmail/imported history row by company and role become `skipped`, jobs on an ATS with no driver become `by_hand` and are never opened; named IDs are not deduplicated. The end table goes to stderr; stdout stays JSON.
- Update (aa-21): `update` works only from a source checkout; it backs up user/ (skipping backups/, browser/, chrome/) before any change, pulls with `git pull --ff-only` or extracts the latest release zip (never into user/, .venv/ or .git/), reinstalls, then runs migrations recorded in user/migrations.json. `apply`'s release lookup is skipped when AUTOAPPLY_LOCAL_NO_UPDATE_CHECK is set; the offline suite sets it.
