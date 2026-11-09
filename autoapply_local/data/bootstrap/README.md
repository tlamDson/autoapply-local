# AutoApply Local runtime — experimental

Automated Gmail requires account binding enforced on every call. Currently only
Claude supports search/read with an explicit account; Codex search/read remains
`manual_required` until a transport can enforce that binding. Quick apply remains
available; handle mail manually with Codex. Native browser fill still carries
real-engine risks and needs a live ATS gate before it can be considered stable.

Gmail tự động cần ép đúng account ở từng lời gọi. Hiện chỉ Claude hỗ trợ search/read
với account tường minh; Codex giữ `manual_required` tới khi transport ép được account.
Quick apply vẫn chạy; dùng Codex thì xử lý mail tay. Fill browser native còn rủi ro
với engine thật, cần qua cổng ATS live sau và chưa được coi là ổn định.

Open this exact directory in your selected engine. Activate the Python environment
where autoapply-local is installed. Commands and the Codex skill here use
`python autoapply-local.py COMMAND`; the launcher always binds this directory,
including when AUTOAPPLY_LOCAL_HOME names a different root. Existing user files
are preserved by setup. Keep this launcher in this runtime root.

For first setup, ask for setup in this host; the local command/skill follows the
native onboarding loop without requiring JSON editing. It discovers the actual
top-level question tool, runs `python autoapply-local.py setup --guided --ui UI`,
invokes its packet and waits for real user answers. UI is `codex-async` for
available request_user_input_async, `codex` for permitted request_user_input, or
`claude` for available AskUserQuestion. These flags only choose the packet schema.
Without a permitted native tool, `--ui none` returns `ui_required` and preserves
progress; reopen a supported host/mode. Never use chat questions, an engine
subprocess, another engine/mode or a new server/browser form as fallback.

The host binds tool-call ID and question index to stable question/option IDs,
saves a private response JSON and calls `setup --answer PATH --ui UI`. Timeouts,
interruptions and preselected defaults are not answers. At most three questions
appear at a time; use More/Add/Done for choices and records. Names and paths are
text entries. Unknown/skip does not mean No; optional EEO can be declined.
`setup --guided --edit QUESTION_ID --ui UI` revisits a catalog or issued record
question. Draft answers never silently establish confirmed facts.

To change existing education, experience or authorization, select Edit an existing
record, choose its numbered option, and answer individual fields. Done keeps draft
changes; Cancel and keep original record discards them. Skip leaves facts intact,
including their metadata. Add is for a new record, not for changing an old one.

If dependencies change, the host asks Reload latest confirmed profile / Keep
current draft in a native popup. Only explicit Reload runs `setup --restart --ui UI`.
Keep, timeout or an unavailable native tool preserves every draft. Restart reports
separate backups of the old progress and generated intake, then reloads current
confirmed facts into a new session without changing them. Old answers/previews
cannot be reused; finish and inspect a new preview before Confirm.

`setup --finish --ui UI` saves an intake draft and the exact import preview. The
host shows it and invokes native Confirm/Keep draft choices. Only explicit
Confirm permits `setup --confirm` using that saved preview. Keep draft, timeout
or interruption preserves unconfirmed progress. Changed dependencies require
fresh inspection, not silent preview recomputation. Advanced intake editing and
`profile update PATH` remain available separately; onboarding does not require
editing `user/profile-intake.md`. [Official structured UI reference](https://learn.chatgpt.com/docs/app-server)
describes tool/requestUserInput and MCP elicitation. The CLI does not display
popups or install an app server; offline packet tests do not certify host UI.

After guided confirmation, continue:

```text
python autoapply-local.py boards list
python autoapply-local.py fetch
python autoapply-local.py apply --quick 1
python autoapply-local.py review all
python autoapply-local.py status
```

Choose your installed engine in the intake or pass its name to engine-assisted
commands. Use only that engine; there is no cross-engine fallback. Source files
can be extracted with `python autoapply-local.py source extract PATH`; extracted
text is a draft, never confirmed facts. Public board examples are in
`examples/boards.json`; boards import previews changes before `--confirm`.

Run `doctor` to inspect dependency presence and resolved CLI arguments; login
and connector/capability readiness remain unverified. Windows npm JS bins need
Node; native bins run directly, with UTF-8 arguments and no shell shim execution.
Both global npm prefixes and local `node_modules/.bin` layouts are supported.
Claude external-tool tasks need CLI >=2.1.295; older versions require a manual
update. Nonempty native permission denials return `permission_required` before
structured output is consumed; approve tools through the normal project flow
and retry manually. The product never writes approval rules. Codex uses read-only,
outside-Git runs and task-scoped capability overrides. It probes effective MCP
identifiers with `mcp list --json` on Windows, macOS and Linux before inference,
retaining only names/flags. Invalid/unavailable inventory, detected unverifiable
managed policy and duplicate inherited task browser servers fail closed. No auth
files are read. Never bypass a configuration refusal; overrides do not establish
live capability readiness.

Browser config fragments are for inspection; task calls supply the owned lane
endpoint. Windows Codex MCP uses a pinned package, fixed command arguments and
a startup timeout. Errors report login, quota, timeout, configuration, permission required, unsupported
version, invalid response or engine failure without exposing raw CLI diagnostics.
There is no automatic submission retry. Keep held forms open after any error.

Quick apply reports target roles, recruiting season/window and selected PDFs on
stderr before browser work. It fills and holds existing PDFs without tailoring
or submitting. Keep held tabs open. Submit requires your explicit request for
reviewed IDs or all-reviewed. Unknown click outcomes need manual reconciliation.
Gmail needs separate opt-in, verified account and native search/read tools.

Live ATS, Gmail, both-engine operation, macOS hardware and an 8 GB machine remain
untested. See AGENTS.md for runtime constraints. Back up your local runtime data.
