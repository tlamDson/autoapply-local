# AutoApply Local runtime

Read README.md and run workflows through `python autoapply-local.py COMMAND` from
this runtime root. The launcher selects its own root before the subcommand,
regardless of AUTOAPPLY_LOCAL_HOME. Use only the user's selected engine with its
normal approvals; never switch engines as fallback or change global settings.

The terminal part of setup, `setup --terminal`, runs in a plain terminal before any
engine opens: language, engine with one small login/quota probe call, quota stop
percent, resume PDFs tagged with roles (copied to `resumes/`) and jobs per run,
saved to `config.json`. It then hands the terminal to the selected engine with
`/setup` (Codex: `$autoapply-local setup`); that interactive handoff is not an
interview subprocess. Inside an engine, never run `setup --terminal`.

For first setup, including a plain setup request, follow the native loop in
`.claude/commands/setup.md` or `.codex/skills/autoapply-local/SKILL.md`. Discover
the actual question tool in this top-level host, run `setup --guided --ui UI`
through the launcher, invoke the native packet and wait for a real user answer.
Map stable question/option IDs into a private response for `setup --answer PATH`;
repeat or resume without losing progress. `setup --finish --ui UI` saves a draft
and its preview. Show it, invoke native Confirm/Keep draft choices and run
`setup --confirm` only after an explicit Confirm. Without a permitted native
tool use `--ui none` / `ui_required` and keep progress. Never substitute chat
prose, an engine subprocess, another engine/mode or preselected/default/timeout
answers. The intake is an optional advanced editor/export. Setup grants no
Submit permission and performs no Gmail access.

Guided setup after terminal setup begins with quick steps: resume fact groups
(contact, education, experience, skills) for Confirm/Edit, then facts a resume
lacks (work authorization, sponsorship, address, EEO, season, roles), then job
filters derived only from explicit answers for Confirm/Edit. An unknown
sponsorship answer never produces a sponsorship filter.
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

If setup becomes stale, invoke native Reload latest confirmed profile / Keep
current draft choices and wait. Run `setup --restart --ui UI` only after explicit
Reload; Keep, timeout or unavailable native UI makes no changes. Restart saves
separate old progress/intake backups and creates a new session from current facts,
without confirming anything. Old answers/previews stay invalid. Use the numbered
native existing-record picker for education, experience or authorization; Cancel
keeps the original record. Unchanged facts keep their metadata.

Your data stays in this runtime root (gitignored): config, confirmed facts,
`resumes/` from terminal setup, tracker, ledger, `secrets.json` (Workday sign-in,
never printed) and Chrome profiles. Setup creates two drop folders here:
`user/resume/` for resume PDFs and TEX/DOCX sources and `user/additional-document/`
for transcripts, certificates and cover letters. A file dropped there is only
stored: it is never selected, confirmed or uploaded. `update` backs up this root
to `backups/<date-time>/` before it pulls the new release and runs migrations.

Use confirmed facts and exact scoped answers. Source extraction produces drafts;
preview profile updates and import only after explicit confirmation. Unknown
authorization, sponsorship, consent and EEO answers must remain unknown.

Apply fills existing role-selected PDFs and holds forms. Review rereads fresh
evidence. Nothing ever clicks Submit; the user submits each held form by hand
and records it with `submitted ID,ID`, which frees its held-tab slot.
Keep unsubmitted tabs open for sign-in, account creation, CAPTCHA, missing
answers and errors.

Required questions no confirmed fact or answer-bank entry covers are held,
collected for the whole `apply` run and asked once at the end: `questions --ui UI`
returns one native popup packet (a single native call; questions that do not
fit are `deferred` to the next popup); after the user's actual answers, `questions
--answer PATH` saves them to `user/answer-bank.json` and refills the held tabs.
Skip, timeout or no native tool keeps them pending; a failed refill is retried
with `questions --refill`.

There is no Gmail command. Never send, draft, archive, label, scrape browser
Gmail, persist codes or access another workspace's private data. Report held tabs and unresolved work.

Live ATS, Gmail, both-engine operation and an 8 GB machine are not validated.
macOS has not been tested live.
