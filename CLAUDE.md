# AutoApply Local

Read AGENTS.md for this standalone product's scope and safety invariants.
Use the source launcher or installed module for initial bootstrap and the generated
runtime's `python autoapply-local.py` launcher for workflows. All candidates begin
with unknown facts; there are no personal defaults.
Module commands choose --user-dir, then AUTOAPPLY_LOCAL_HOME, then a detected
source checkout's user/, then ~/.autoapply-local outside source.
Install with install.ps1 / install.sh (dependency check, repo .venv, editable
install, auto-start setup). The source autoapply-local.py defaults to setup in
<repo>/user; open the generated
runtime and use its own root-bound launcher for subsequent commands.

The product is quick apply (README.md, README.vi.md): install, terminal setup,
engine setup, then `apply [N]` only when the user types it, and `update`. All
user data stays in the runtime root, `<repo>/user/` by default (gitignored):
config, confirmed facts, `resumes/` from terminal setup, tracker, ledger,
`secrets.json` and Chrome profiles. Setup also creates the drop folders
`user/resume/` (resume PDFs and TEX/DOCX sources) and `user/additional-document/`
(transcripts, certificates, cover letters) inside the runtime root; a file there
is only stored, never selected, confirmed or uploaded.
macOS has not been tested live; never claim it from offline tests.

Job fetch (boards and `--url`) supports Greenhouse, Workday and Rippling only
in v0.2.0a1; Ashby, Lever and Workable have fill drivers but no job fetch until
the next version.

The terminal part of setup, `setup --terminal`, runs in a plain terminal before any
engine opens: language, engine with one small login/quota probe call, quota stop
percent, resume PDFs tagged with roles (copied to `resumes/`) and jobs per run,
saved to `config.json`; then it opens the tool's clean Chrome profile
(`chrome/lane-1`) on Gmail for the user to sign in on the web page only (tell
the user to do NOT turn on Chrome sync in that profile: sync installs their own
extensions and opens their tabs) and scans 183 days of
confirmation emails into the tracker (deduplicated). It then hands the terminal to the selected engine with
`/setup` (Codex: `$autoapply-local setup`); that interactive handoff is not an
interview subprocess. Inside an engine, never run `setup --terminal`.

For first setup or a plain setup request, follow `.claude/commands/setup.md`:
discover the real native question tool in the top-level host, run
`setup --guided --ui UI`, invoke the returned packet, wait for actual user input,
and map its stable IDs into a private file for `setup --answer PATH`. Show the
saved finish preview and native Confirm/Keep draft choices; only explicit Confirm
permits `setup --confirm`. No native tool or forbidden mode returns `ui_required`
with progress kept. Never replace the popup with chat prose, use an engine
subprocess to interview, switch modes/engines or treat defaults/timeouts as input.
The complete intake is an optional advanced editor/export, not required setup.
After terminal setup, guided setup first shows resume fact groups, then the facts
a resume lacks, then job filters derived from explicit answers, each with
Confirm/Edit; extracted values stay drafts until the saved preview is confirmed.
The advanced walk then skips what it already has or cannot use: language,
engine and resumes saved by terminal setup, every tailoring question unless
the mode includes tailoring, the Gmail account, lookback and permission
questions unless Gmail is opted in (a gate never shares a popup with the
questions it turns off), and `international_student.status`, which takes the
answer to `authorization.current_status` when blank.

Setup then fills an offline demo form (`demo`: a local page with no Submit
control, in the tool's own Chrome) to prove the stack works, and runs a first
`fetch` whose `report.summary` says how many jobs from how many boards passed
the filters. Job sources are the shipped public boards in two groups, S&P 500
(`sp500`) and unicorns (`unicorn`): `boards groups` lists them, `boards group
NAME on|off` turns a group off or on without deleting it, and `boards import
PATH` adds your own boards, which are always fetched.

For stale dependencies, invoke native Reload latest confirmed profile / Keep
current draft choices and wait. Only explicit Reload permits `setup --restart
--ui UI`; Keep or timeout does nothing. Restart preserves separate draft backups,
loads current facts into a new session, and requires a new preview before Confirm.
Existing records use the numbered native picker and field editor; Cancel keeps
the original record, and unchanged facts keep their metadata.

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

Never click a Submit control, through any command or raw browser action: the
user reviews and submits each held form by hand. Follow normal engine/MCP
approval prompts.
