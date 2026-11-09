# AutoApply Local runtime

Read README.md and run workflows through `python autoapply-local.py COMMAND` from
this runtime root. The launcher selects its own root before the subcommand,
regardless of AUTOAPPLY_LOCAL_HOME. Use only the user's selected engine with its
normal approvals; never switch engines as fallback or change global settings.

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

If setup becomes stale, invoke native Reload latest confirmed profile / Keep
current draft choices and wait. Run `setup --restart --ui UI` only after explicit
Reload; Keep, timeout or unavailable native UI makes no changes. Restart saves
separate old progress/intake backups and creates a new session from current facts,
without confirming anything. Old answers/previews stay invalid. Use the numbered
native existing-record picker for education, experience or authorization; Cancel
keeps the original record. Unchanged facts keep their metadata.

Use confirmed facts and exact scoped answers. Source extraction produces drafts;
preview profile updates and import only after explicit confirmation. Unknown
authorization, sponsorship, consent and EEO answers must remain unknown.

Apply fills existing role-selected PDFs and holds forms. Review rereads fresh
evidence. Only an explicit submit request for reviewed IDs or all-reviewed permits
the durable submission workflow. Keep unsubmitted tabs open for sign-in, account
creation, CAPTCHA, missing answers and errors. Reconcile uncertain submission
outcomes manually without retrying a click.

Gmail requires separate opt-in, a verified selected account and native search/read
tools. Never send, draft, archive, label, scrape browser Gmail, persist codes or
access another workspace's private data. Report held tabs and unresolved work.
