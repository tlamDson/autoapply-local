---
name: autoapply-local
description: Run standalone AutoApply Local setup, job discovery, PDF applications, review and local status in its generated runtime root.
---

Read this runtime's AGENTS.md and README.md. Run requested workflows from this
generated runtime root through `python autoapply-local.py COMMAND`. The launcher
selects its own root before the subcommand, regardless of AUTOAPPLY_LOCAL_HOME.
Preserve explicit engine overrides and use only the selected/configured engine
with normal MCP approvals. Never switch engines as fallback or edit global config.

Route setup, profile update, source extract, boards list/import, history import,
doctor, fetch, apply, tailor, review, explicit submit, mail-check and status through
that launcher. Pass the user's arguments unchanged.

Setup preserves existing files and board choices, including `[]`. Edit
`user/profile-intake.md`, preview `profile update PATH`, and import only after
explicit `--confirm`. `source extract PATH` produces draft text; help draft
contact, education, experience and skills with references to supplied sources.
Unknown facts remain unknown. Never infer authorization, sponsorship, visa or
EEO from resumes; sensitive answers require exact question/employer/time scope.

Public board examples are in `examples/boards.json`. `boards import PATH` previews
normalized additions/changes before a confirmed merge. Intake resources.boards
absent/null preserves existing choices; [] disables them. Discovery seeds do not
guarantee eligibility or sponsorship. Fetch needs sources or --url and only the
fields relevant to discovery; configured sources can legitimately yield no jobs.

`apply --quick N|ID,ID` reports roles, recruiting season/window and PDF mapping
before browser work, then fills existing role-selected PDFs and holds tabs.
It never tailors or submits. Review rereads fresh form/profile/PDF evidence.
Only a user's explicit submit request for reviewed IDs or all-reviewed permits
the durable submit workflow. Preserve forms for manual interaction and errors;
reconcile uncertain click outcomes manually without retries.

Tailor runs separately without opening forms. Gmail needs separate opt-in, a
verified selected account and native search/read tools; otherwise report manual
fallback. Never scrape browser Gmail, send/draft/archive/label mail, persist
verification codes or access another workspace's private data. Report held tabs
and unresolved work.
