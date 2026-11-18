---
description: Fill role-selected PDFs and hold forms without submitting.
---

Read this runtime's AGENTS.md and README.md. Run
`python autoapply-local.py apply $ARGUMENTS` from this generated runtime
root. The launcher binds this root explicitly before the subcommand, even if
AUTOAPPLY_LOCAL_HOME conflicts. Route related CLI commands through the same launcher.
Use the selected engine only; do not launch another engine as fallback.
Arguments: `apply [ENGINE] [N|ID,ID]` (no N = the configured jobs per run;
`--quick` still works). One run tracks submitted tabs and Gmail confirmations,
fetches and filters new jobs, reopens held tabs Chrome lost, picks one PDF per
job, fills with the script driver, and ends with a table on stderr: job rows,
fields to check, confirmations, skipped jobs, the submit-by-hand list (ATS with
no driver: never opened) and required questions still unanswered. Quick apply
uses existing PDFs; it never submits.
This tool never clicks Submit; the user submits held forms by hand. Preserve any held form tab and return manual-interaction states to the user.
Use confirmed facts/exact scoped answers and normal MCP approvals. Never use
private parent-workspace data, browser Gmail scraping or mail write actions.
When the result shows `questions.pending`, run `python autoapply-local.py questions --ui UI`
once, make its one native call as the popup, wait for the user's actual answers, write
them to a private file and run `questions --answer PATH` to save them to the answer bank
and refill the held tabs. Never answer for the user; skip or timeout keeps them pending. If a refill fails,
run `questions --refill` to retry it from the answer bank. When the packet shows `deferred`,
run `questions --ui UI` again after the refill for the questions that did not fit.

Report the command result and held tabs or unresolved items.
