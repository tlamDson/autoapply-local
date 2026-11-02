---
description: Create or inspect the complete local onboarding intake.
---

Read this runtime's AGENTS.md and README.md. Run
`python autoapply-local.py setup $ARGUMENTS` from this generated runtime
root. The launcher binds this root explicitly before the subcommand, even if
AUTOAPPLY_LOCAL_HOME conflicts. Route related CLI commands through the same launcher.
Use the selected engine only; do not launch another engine as fallback.
Setup installs editable Anthropic/Greenhouse, NVIDIA/Workday (`software` search)
and SpreeAI/Rippling discovery seeds only if config has no `boards` key. Preserve
existing source choices, explicit `[]`, and all user edits on repeated setup.
These sources do not guarantee eligibility or sponsorship.

For either selected engine (Claude or Codex), offer `source extract PDF_PATH` to
obtain draft text, then help draft contact, education, experience and skills in
the single full intake with references to the supplied sources. Extraction is
draft only. Never infer authorization, visa, sponsorship or EEO from a resume.
Have the user inspect `profile update INTAKE_PATH`, then import only on explicit
`--confirm`. Sources can be set in intake `resources.boards`: absent/null keeps
existing sources and `[]` intentionally disables them. Show `boards list` and
`boards import JSON_PATH` preview, then `--confirm` when requested; public examples
are in `examples/boards.json`. Continue with fetch, apply (fill and hold), review.
This command does not authorize Submit. Preserve any held form tab and return manual-interaction states to the user.
Use confirmed facts/exact scoped answers and normal MCP approvals. Never use
private parent-workspace data, browser Gmail scraping or mail write actions.
Report the command result and held tabs or unresolved items.
