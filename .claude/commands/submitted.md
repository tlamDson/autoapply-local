---
description: Record held forms you already submitted by hand.
---

Read this runtime's AGENTS.md and README.md. Run
`python autoapply-local.py submitted $ARGUMENTS` from this generated runtime
root with the explicit job IDs the user says they submitted. The launcher binds
this root explicitly before the subcommand, even if AUTOAPPLY_LOCAL_HOME
conflicts. Route related CLI commands through the same launcher.
This tool never clicks Submit; the user submits held forms by hand. Only record
IDs the user names; never infer a submission from the page. Recording writes
the tracker row and frees the held-tab slot; it does not close the tab.
If recording fails part-way, run the same command again; it resumes.
Report the recorded jobs and the remaining held tabs.
