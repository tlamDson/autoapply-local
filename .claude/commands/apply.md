---
description: Fill role-selected PDFs and hold forms without submitting.
---

Read this runtime's AGENTS.md and README.md. Run
`python autoapply-local.py apply $ARGUMENTS` from this generated runtime
root. The launcher binds this root explicitly before the subcommand, even if
AUTOAPPLY_LOCAL_HOME conflicts. Route related CLI commands through the same launcher.
Use the selected engine only; do not launch another engine as fallback.
Use --quick with a count or explicit IDs. The CLI shows target roles, recruiting
season/window and the selected PDF mapping on stderr before browser work. Quick
apply uses existing PDFs; it never tailors or submits.
This command does not authorize Submit. Preserve any held form tab and return manual-interaction states to the user.
Use confirmed facts/exact scoped answers and normal MCP approvals. Never use
private parent-workspace data, browser Gmail scraping or mail write actions.
Report the command result and held tabs or unresolved items.
