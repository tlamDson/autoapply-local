# AutoApply Local 0.1.0a1 — experimental

Local job-search tools with one complete editable intake, confirmed facts,
role-selected PDF resumes and an explicit fill → review → submit workflow.
English and Vietnamese instructions: [Tiếng Việt](README.vi.md).

Changed dependent facts require fresh confirmation of affected answers; analysis failures before browser actions return newly claimed jobs to `pending` for a manual retry, while held tabs stay open.

Offline unit, synthetic DOM and installed-package checks are automated. Live ATS,
Gmail, both-engine end-to-end operation, macOS hardware and an 8 GB machine have
**not** been validated. This is an experimental release, not a stable automation
claim. Unsupported controls and account challenges leave the form open for you.

Automated Gmail requires account binding enforced on every call. Currently only
Claude supports search/read with an explicit account; Codex search/read remains
`manual_required` until a transport can enforce that binding. Quick apply remains
available; handle mail manually with Codex. Native browser fill still carries
real-engine risks and needs a live ATS gate before it can be considered stable.

Gmail tự động cần ép đúng account ở từng lời gọi. Hiện chỉ Claude hỗ trợ search/read
với account tường minh; Codex giữ `manual_required` tới khi transport ép được account.
Quick apply vẫn chạy; dùng Codex thì xử lý mail tay. Fill browser native còn rủi ro
với engine thật, cần qua cổng ATS live sau và chưa được coi là ổn định.

## Repository

Source repository: [tlamDson/autoapply-local](https://github.com/tlamDson/autoapply-local).

```text
git clone https://github.com/tlamDson/autoapply-local.git
cd autoapply-local
```

## Install

Use Python 3.11+, Chrome, Node.js/npm and your selected installed engine CLI.
From this release checkout, install and create a separate runtime directory.
Windows PowerShell:

```powershell
python -m venv ../autoapply-venv
& ../autoapply-venv/Scripts/Activate.ps1
python -m pip install .
python -m autoapply_local --user-dir ../autoapply-user setup
Set-Location ../autoapply-user
```

macOS:

```bash
python3 -m venv ../autoapply-venv
source ../autoapply-venv/bin/activate
python -m pip install .
python -m autoapply_local --user-dir ../autoapply-user setup
cd ../autoapply-user
```

For a downloaded wheel, replace `python -m pip install .` with
`python -m pip install path/to/autoapply_local-0.1.0a1-py3-none-any.whl`; the
installed package can run setup from any directory without the checkout.
Dependencies are pinned in pyproject.toml and requirements.txt. Optional tailoring
needs a TeX installation with pdflatex plus Poppler (`pdftoppm`, `pdfinfo`,
`pdftotext`). Quick application with your existing PDF does not need TeX.

The browser connector pins chrome-devtools-mcp 0.14.0. npm may download that
version when you first approve and start the connector. Do not use a private
everyday Chrome profile. The product creates persistent clean profiles per lane.

## Setup and your facts

Initial setup is `python -m autoapply_local --user-dir PATH setup`. Open that
exact generated runtime directory in your selected engine, with the installed
package's virtual environment active. Setup installs its local commands, skill,
support files and `autoapply-local.py` launcher without replacing existing files.
The launcher always selects its own directory, including when
`AUTOAPPLY_LOCAL_HOME` points elsewhere. Quote paths containing spaces.
Without an explicit root, setup uses `AUTOAPPLY_LOCAL_HOME` or the default
`~/.autoapply-local`; open whichever directory it creates.

Edit `user/profile-intake.md` as one complete form; all 14 groups are visible at
once. Preview with `python autoapply-local.py profile update PATH`, then use the
same command with `--confirm` only after checking the proposed facts. A changed
intake needs a new preview. Setup is repeatable and preserves existing files.

The form covers runtime/language, contact, resume alternatives and role mapping,
multiple education records, roles and job types, schedule/locations, work
authorization, CPT/OPT/STEM_OPT mechanisms, eligibility filters, experience and
skill evidence, compensation/EEO preferences, application history, tailoring
policy, Gmail and hardware. Blank facts stay unknown. Planned authorization does
not establish authorization; current and future sponsorship stay separate.
Exact employer/question/time scopes are required for sensitive answers.

The packaged bilingual questionnaire supplies actual questions for all 14 groups.
Confirm given/first and family/last names separately; their order is never guessed
from the legal full name. Put exact answers in the intake's top-level `answers`
list, with `question`, `value`, `status`, `provenance`, `confirmed_at` and
`scope` (`country`, `job_type`, `employer`, `question`, `effective_at`). The normal
`profile update PATH` preview displays these changes; `--confirm` imports them.
The form explains the allowed states and choices. Blank immigration facts and
unanswered questions remain unknown.

Source extraction from PDF/TEX/DOCX produces a draft, never confirmed facts.
With either Codex or Claude selected, use `source extract PATH` to obtain draft
text, then ask that engine to help draft the intake's contact, education,
experience and skills from the supplied sources. Check the source for each fact,
preview `profile update PATH`, then explicitly confirm it with `--confirm`.
Never infer authorization, visa status, sponsorship or EEO answers from a resume.
Generated resumes cannot become new fact sources.
Profile changes invalidate dependent answers and reviews.

## Document drop folders

Setup creates two folders inside the selected runtime. Its JSON output includes
`resume_dir` and `additional_document_dir`, both absolute paths to existing folders:

- `user/resume/`: resume PDFs and resume source files such as TEX or DOCX.
- `user/additional-document/`: transcripts, certificates, cover letters and other
  supporting documents.

Each folder has a general README created only when absent. Repeating setup
preserves your documents and edited guides; it does not move, copy, rename or
delete documents. A file in place of a folder, a directory in place of a guide,
or a symlink escaping the runtime is rejected before either guide is written.
Keep runtime `user/` contents out of Git and release artifacts.

Dropping documents here only stores them. It does not select a resume, extract
or confirm facts, enable tailoring, or upload anything. Select `main_pdf`
explicitly in the intake's `resumes` group using the PDF's **absolute path**.
Do not rely on a relative `user/resume/...` PDF path: relative selection resolves
from the caller's working directory. Preview `profile update` and explicitly
confirm after checking the facts. Quick apply with an existing PDF does not
require LaTeX; additional documents are not uploaded automatically.

## Quickstart: setup, sources, fetch and review

After the installation/setup steps above, stay in the generated runtime root.
Its launcher binds every command to that root; changing the working directory
alone does not select a root for `python -m autoapply_local`.

```text
python autoapply-local.py source extract path/to/resume.pdf
```

Edit `user/profile-intake.md`: select your installed engine, set the existing
resume PDF path and supply your confirmed facts. Use only that selected engine
to help draft the intake from the extracted text and source documents as described
above. Inspect the preview before confirming:

```text
python autoapply-local.py profile update user/profile-intake.md
python autoapply-local.py profile update user/profile-intake.md --confirm
python autoapply-local.py boards list
python autoapply-local.py boards import examples/boards.json
python autoapply-local.py boards import examples/boards.json --confirm
python autoapply-local.py fetch
python autoapply-local.py apply --quick 1
python autoapply-local.py review all
```

Setup installs editable discovery seeds for Anthropic (Greenhouse), NVIDIA
(Workday, search `software`) and SpreeAI (Rippling) only when config has no
`boards` key. These public sources are not eligibility or sponsorship guarantees.
Repeated setup preserves your source choices, including an explicit empty list.
The example import above previews and merges the same seeds; copy/edit that JSON
list to add or change sources, then preview and confirm your own file. Imports
match stable ATS identities and retain unrelated settings; changed input/config
requires a fresh preview. Supported endpoint overrides must use public HTTPS ATS
endpoints.

You can also edit `resources.boards` in the full intake: absent or `null` preserves
current sources, while `[]` intentionally disables configured sources. Fetch
without sources requires `boards list` / `boards import` / `setup` guidance or an
explicit supported `--url`; it does not silently succeed with zero jobs. A
configured source can legitimately return no matching jobs. Listing/importing
boards and fetching do not require unrelated onboarding fields. Apply fills and
holds the tab, and review inspects it; keep unsubmitted forms open.

## Runtime and browser approvals

The Part2 Windows runtime smoke results are historical, from an earlier engine
revision before the engine, tool-gate and profile safety changes. Those checks
passed with Codex 0.165.0-alpha.1.2 and Claude 2.1.312: structured JSON/Unicode
outside Git and browser MCP on two isolated lanes per engine. The Claude positive
fixture explicitly approved only the read-only `list_pages` tool; an unapproved
invocation correctly stopped. These historical results do not validate the full
current runtime; the current changes have offline test coverage. They do not
establish live ATS, Gmail, macOS or 8 GB readiness, or stable operation.

Choose one engine per task in the intake; command-line engine overrides that
choice. Sign in to the selected engine using its normal sign-in flow. Run
`python autoapply-local.py doctor` with the configured selected engine
to inspect executable and optional dependency presence. Executable presence is
not login or connector readiness.

Setup writes project-local `.mcp.json` and `mcp-codex.json` in your runtime
directory without replacing existing files. Open/approve the runtime directory
in the selected engine and approve the local browser MCP normally. Runtime
tasks pass the actual persistent lane endpoint to the selected CLI; no global
config or hook is installed, no model is pinned, and there is no fallback engine.
Do not bypass approvals. The Codex fragment uses `mcp_servers` and is for
inspection; task invocations configure their own lane endpoint. An inherited
entry named `autoapply-local-browser` must be removed before using task-local
transport, since merging it could inherit unrelated environment settings.

External-tool tasks on Claude require CLI 2.1.295 or newer for the blocking
command-hook boundary. The runtime probes the resolved selected executable
before dispatch. Older or unparseable versions return `unsupported_version` and
need a manual update through the CLI's official installation instructions.
Tool-free analysis/tailoring can run without that external-tool hook. Only the
exact internal `StructuredOutput` tool is admitted for JSON formatting; output
must be a successful CLI envelope with schema-valid structured output.
Nonempty native `permission_denials` returns `permission_required` before any
structured output is accepted. Approve the tool through the CLI's normal project
approval flow and retry manually; the product never writes approval rules.

Executable arguments, prompts and JSON use UTF-8, including paths with spaces
and Unicode. Supported Windows npm `.cmd` installations of the two known CLI
packages resolve their declared JavaScript bin through Node, or their native
`.exe` bin directly, with `shell=False`. Both global npm prefixes and local
`node_modules/.bin` layouts are supported. Other shims or incomplete installations
return `configuration`; complete native postinstall/setup manually. Codex's
Windows browser MCP uses fixed `cmd /c npx` arguments, system environment and a
20-second startup timeout, with the pinned MCP version and a validated loopback
URL. Claude's browser transport remains separate.

Codex works outside Git with `--skip-git-repo-check` and a read-only sandbox.
Session-only TOML overrides disable inherited connector names, shell, native
browser/computer tools, code execution and multi-agent capabilities; browser
tasks expose only their selected MCP operations. Model/provider selection,
authentication storage and ordinary approvals are preserved.
Codex enables `features.code_mode_host` and `features.workspace_dependencies`
only for explicitly requested MCP/native connector tools. These are broker
infrastructure; analysis/tailoring and empty tool lists keep both off, while
`features.code_mode` and the other restricted capabilities remain disabled.
Before inference,
`mcp list --json` probes the effective MCP inventory through the same resolved
CLI, working directory and task policy, including both broker flags, on Windows,
macOS and Linux.
Only connector identifiers and enabled flags are retained; transport, environment
and authentication metadata are discarded. Invalid or unavailable inventory,
unreadable config, detected unverifiable managed policy and unsupported legacy
profiles fail closed. Managed requirements are never bypassed; configuration
refusals require manual attention. Overrides are not a live capability check.
No global configuration or credential file is copied, and auth files are not read.

Diagnostics distinguish `login_required`, `quota_exhausted`, `timeout`,
`configuration`, `permission_required`, `unsupported_version`, `invalid_response` and `engine_failure`
without echoing sensitive CLI output. Probes are bounded to 10 seconds and task
calls to 300 seconds. CLI runtime failures return exit 3; invalid response/schema
returns exit 2. There is no automatic engine fallback or submission retry.
`doctor` reports executable presence, resolved launch arguments and dependency
presence separately; login and connector/capability checks remain `not_verified`.
Offline tests do not establish live CLI, MCP handshake, ATS or Gmail readiness.

One lane is the default. Two or three require explicit opt-in in the intake.
Admission checks available RAM and held tabs; the memory rule is a heuristic,
not an 8 GB benchmark. Review/submit held forms before opening more when the cap
is reached. Persistent clean profiles retain sign-in state between commands.
New jobs rotate through the opted-in lanes. Each job retains its lane and debug
endpoint, so later commands restore its own browser. Admission rechecks RAM and
held-tab capacity before every new job and retains partial results on refusal.
Held forms can still be inspected or resumed when capacity is exhausted.

## Commands

From the generated runtime root, run `python autoapply-local.py COMMAND`.
The local slash commands and Codex skill use this same root-bound launcher.
If invoking the installed module or entrypoint directly, put `--user-dir PATH`
before the subcommand.

For `apply`, `review` and `submit`, `--approve-browser-tools` explicitly opts in
for the current command only. It approves only the local browser MCP tools that
each task requests and is never saved. Existing policy, including managed policy,
permission denials and the review gate, still applies. It does not approve Gmail,
analysis or tailor tools. `apply --quick` and `review` never submit automatically;
submission still requires an explicit `submit` command after review.

- `setup`: create local intake/templates and blank state safely.
- `boards list`: show configured discovery sources.
- `boards import PATH [--confirm]`: validate, normalize and deduplicate a JSON
  source list; preview additions/changes, then explicitly confirm the merge.
- `profile update PATH [--confirm]`: preview or import the complete intake.
- `source extract PATH`: extract PDF/TEX/DOCX text to a draft JSON under
  `user/drafts`; check and transfer facts into the full intake, then preview and
  explicitly confirm `profile update`. Extraction cannot confirm itself.
- `history import PATH [--confirm]`: preview CSV/XLSX application history, then
  import on explicit confirmation while preserving historical stages and dedup.
- `doctor [ENGINE]`: inspect dependencies; login/connectors remain unverified.
- `fetch [ENGINE] [--url URL]`: fetch configured public Greenhouse, Workday CXS
  and Rippling boards, or import a supported manual URL. Configure sources through
  `boards import` or intake `resources.boards`. Unknown eligibility
  follows your configured policy and never becomes an inferred fact.
- `apply [ENGINE] --quick N|ID,ID`: choose a role-mapped existing PDF, fill,
  upload, and hold tabs. Target roles, recruiting season/window and each selected
  job's PDF mapping appear on stderr before browser work; final stdout stays JSON.
  This command never submits or tailors.
- `tailor [ENGINE] JD_FILE_OR_URL`: produce a unique new TEX/PDF/changes run
  from confirmed facts using your tailoring scope/policy, with at most one repair.
  No form is opened. Missing visual inspection leaves `needs_review`.
- `review ID,ID|all`: read held forms again and bind job/tab/values/profile/PDF
  freshness. It never submits or closes forms.
- `submit ID,ID|all-reviewed`: explicitly permit one Submit click per fresh
  reviewed job. Durable intent precedes clicking; tracker is written only after
  a definite matched confirmation. An uncertain outcome blocks automatic retry.
- `mail-check [ENGINE]`: opt-in native connector search/read for one verified
  account. Missing connector/account permission returns manual fallback.
- `status`: list held forms, counts and unresolved applications without an engine.

For account creation, sign-in, CAPTCHA, MFA or unsupported dynamic controls,
complete the requested interaction in the held clean Chrome tab and resume.
Resume with an explicit `apply [ENGINE] --quick ID` after resolving the blocker.
It uses the same held tab, and unresolved login/CAPTCHA/data keeps its manual
state. A submission intent or uncertain outcome always needs reconciliation.
Passwords are not collected or stored by this package. Do not close unsubmitted
forms. Unknown required questions need your exact scoped confirmation.

Native multipage filling receives the chosen PDF and uploads on the Resume step
before advancing. Review needs fresh observed label/value text and an attachment
that matches the browser-observed upload receipt; an expected local hash alone
is insufficient. Missing evidence leaves the form held for manual inspection.

Gmail never falls back to browser scraping or custom OAuth. Enable account,
read/search and optional current-job verification separately in the intake.
Verification codes are transient; no send/draft/archive/label action is allowed.
Ambiguous messages and missing mail never mean an application failed.
Analysis/tailoring disable external tools; browser operations use the selected
lane's connector and operation tools. Native Gmail discovery may inspect account
metadata only after opt-in; message reads wait for the selected account and
read/search tools to be verified. Ordinary runtime approvals still apply. Missing
metadata or a usable read-only connector returns manual fallback.

The local XLSX tracker is authoritative for application history. JSON ledger
recovery reconciles from all historical tracker stages. CSV/XLSX history import
uses preview then explicit confirmation; unknown stages fail instead of returning
to pending. Keep both the local tracker and ledger backups.

## Test and export

From this release directory run:

```text
python -m unittest discover -s tests -p "test_*.py" -v
node --test tests/adapters.test.cjs
python scripts/check_release.py
```

Tests use temporary synthetic data and mocked engines/browser/network. The real
synthetic TeX smoke runs only when optional tools are installed. Source export
uses export-allowlist.json and content scanning, not .gitignore alone. Runtime
data, credentials and local build outputs are excluded. Audited synthetic test
sources are included in the public repository for CI; wheels and sdists exclude tests.
This is an experimental public prerelease; check
[Actions](https://github.com/tlamDson/autoapply-local/actions) for recorded CI results.
Local checks reported here were run on Windows; the workflow defines Windows/macOS jobs with Python
3.11 and 3.12, without claiming a remote CI run or macOS hardware validation.

`pyproject.toml` is the source of the project name, current version, dependencies
and wheel package-data patterns. Artifact filenames and metadata must match that
version. `--artifacts DIR` checks every final wheel, sdist and source ZIP in the
directory for its exact file inventory, shipped source bytes, packaging metadata,
wheel RECORD hashes/sizes and the existing privacy/path safety rules. Stale
artifacts are rejected even when a current artifact is present beside them.
`--expect wheel,sdist` is the default; use `--expect wheel,sdist,zip` to require
all three kinds. Any missing expected kind fails the audit.

The wheel contains package Python modules and declared package data, plus wheel
metadata and license files. The sdist contains the export allowlist except tests,
plus generated source-distribution metadata. The source ZIP contains exactly the
entire allowlist, including synthetic tests, under its versioned root directory.
Tests are included only in that source ZIP among these three artifacts.
`--source-zip DIR` first audits the source, creates a sorted ZIP with fixed
timestamps, then audits the ZIP. It refuses to overwrite an existing artifact.

For a new export and a separate final-artifact directory, run from the release
directory (the example export and install-work directories must not already exist):

```text
python scripts/check_release.py --export ../autoapply-export-new
cd ../autoapply-export-new
python -m pip wheel --no-deps --no-build-isolation --wheel-dir ../autoapply-artifacts-new .
python -m build --sdist --no-isolation --outdir ../autoapply-artifacts-new
python scripts/check_release.py --source-zip ../autoapply-artifacts-new
python scripts/check_release.py --artifacts ../autoapply-artifacts-new --expect wheel,sdist,zip
python scripts/check_install.py --artifacts ../autoapply-artifacts-new --work-dir "../install thử ünïcode new"
```

The build commands require the pinned build requirements from `pyproject.toml`
and the `build` package. The install smoke audits final artifacts before creating
two fresh venvs with dependencies, then checks wheel and sdist installations from
outside the source tree. Its work directory contains commands/results; it is
separate from the public source. To use a prepared offline dependency wheelhouse,
add `--wheelhouse PATH` to `check_install.py`; dependencies are still installed.
These commands document the integration procedure, not a completed integration
result for the current artifacts.

MIT license; see NOTICE for layout and dependency attribution.
