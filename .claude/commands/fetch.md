---
description: Fetch configured public ATS boards or a supported manual job URL.
---

Read this runtime's AGENTS.md and README.md. Run
`python autoapply-local.py fetch $ARGUMENTS` from this generated runtime
root. The launcher binds this root explicitly before the subcommand, even if
AUTOAPPLY_LOCAL_HOME conflicts. Route related CLI commands through the same launcher.
Use the selected engine only; do not launch another engine as fallback.
Inspect `boards list` when sources need configuration. Use `boards import PATH`
to validate/normalize/deduplicate a JSON list and preview additions/changes;
apply the merge with `--confirm` only when requested. The editable public defaults
are Anthropic/Greenhouse, NVIDIA/Workday (`software` search), SpreeAI/Rippling;
they do not guarantee eligibility or sponsorship. Users can also set
`resources.boards` in the full intake: absent/null preserves existing sources,
while `[]` intentionally disables them. Setup preserves existing source choices.
Without configured sources or `--url`, report the actionable error and guide the
user through boards list/import/setup. Configured sources may return zero jobs.
Boards/fetch need only relevant fields, not unrelated complete onboarding.
This command does not authorize Submit. Preserve any held form tab and return manual-interaction states to the user.
Use confirmed facts/exact scoped answers and normal MCP approvals. Never use
private parent-workspace data, browser Gmail scraping or mail write actions.
Report the command result and held tabs or unresolved items.
