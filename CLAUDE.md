# AutoApply Local

Read AGENTS.md for this standalone product's scope and safety invariants.
Use the installed module for initial bootstrap and the generated runtime's
`python autoapply-local.py` launcher for workflows. All candidates begin with
unknown facts; there are no personal defaults.

For first setup or a plain setup request, follow `.claude/commands/setup.md`:
discover the real native question tool in the top-level host, run
`setup --guided --ui UI`, invoke the returned packet, wait for actual user input,
and map its stable IDs into a private file for `setup --answer PATH`. Show the
saved finish preview and native Confirm/Keep draft choices; only explicit Confirm
permits `setup --confirm`. No native tool or forbidden mode returns `ui_required`
with progress kept. Never replace the popup with chat prose, use an engine
subprocess to interview, switch modes/engines or treat defaults/timeouts as input.
The complete intake is an optional advanced editor/export, not required setup.

For stale dependencies, invoke native Reload latest confirmed profile / Keep
current draft choices and wait. Only explicit Reload permits `setup --restart
--ui UI`; Keep or timeout does nothing. Restart preserves separate draft backups,
loads current facts into a new session, and requires a new preview before Confirm.
Existing records use the numbered native picker and field editor; Cancel keeps
the original record, and unchanged facts keep their metadata.

Do not submit through raw browser actions: explicit submit must go through the
durable reviewed workflow. Follow normal engine/MCP approval prompts.
