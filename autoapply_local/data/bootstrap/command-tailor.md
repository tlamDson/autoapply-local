---
description: Tailor a standalone JD from confirmed facts into a new output run.
---

Read this runtime's AGENTS.md and README.md. Run
`python autoapply-local.py tailor $ARGUMENTS` from this generated runtime
root. The launcher binds this root explicitly before the subcommand, even if
AUTOAPPLY_LOCAL_HOME conflicts. Route related CLI commands through the same launcher.
Use the selected engine only; do not launch another engine as fallback.
This command does not authorize Submit. Preserve any held form tab and return manual-interaction states to the user.
Use confirmed facts/exact scoped answers and normal MCP approvals. Never use
private parent-workspace data, browser Gmail scraping or mail write actions.
Report the command result and held tabs or unresolved items.
