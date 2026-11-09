---
name: autoapply-local
description: Run standalone AutoApply Local setup, job discovery, PDF applications, review and local status in its generated runtime root.
---

Read this runtime's AGENTS.md and README.md. Run requested commands from this
runtime root through `python autoapply-local.py COMMAND`. The launcher binds its
own directory even when AUTOAPPLY_LOCAL_HOME conflicts. Preserve explicit engine
choices and use only the selected engine with normal approvals; no fallback.

For a plain setup request or first setup, run
`python autoapply-local.py setup $ARGUMENTS`, then perform the native onboarding
loop below. Do not ask the user to edit JSON to complete onboarding. The complete
intake remains available for advanced editing/export.

1. In this top-level chat, discover the actual native question tool and its mode
   permissions. Select `--ui codex-async` only for available
   `request_user_input_async`, `--ui codex` for permitted
   `request_user_input`, or `--ui claude` for available `AskUserQuestion`.
   Run `python autoapply-local.py setup --guided --ui UI`. These flags select
   a packet schema, never grant tool permission. If no native tool is available
   or the current mode forbids it, use `--ui none`, report `ui_required` and
   preserve progress. Explain that the user can reopen a host/mode supporting the
   tool. Never replace native questions with chat prose. Never launch Engine.run
   or a subprocess to conduct this interview. Do not switch to Plan mode, change
   engines, install a server or create a browser form.
2. Invoke the discovered tool itself with the returned `ui.arguments`; the CLI
   only prepares a packet and does not display a popup. Keep an in-memory binding
   of the actual tool-call ID and question index to the packet's schema_version,
   session_id, revision, batch_id and `ui.bindings` (question_id/option_ids).
   Use the returned prompts and choices, at most three questions. Respect option
   caps and short headers; More/Add/Done consume option slots. Do not add Other
   when the host already supplies free text. Do not mark factual Yes/No as
   recommended. Names and file paths are text entries, not upload requests.
3. Wait for the real response to that exact tool call. For asynchronous tools,
   yield/wait for the user message; returning from the tool does not supply an
   answer. A timeout, interruption, blank response or preselected default is not
   consent or an answer. Keep the current batch and resume later. Map explicitly
   selected labels/indices through the saved bindings to stable option IDs.
   Never infer factual enum values from prose. Accept custom text only where the
   question permits text/custom; convert numeric input to its declared JSON type.
   Preserve unknown, not_applicable, declined and notasked separately; skipping is
   not No. Optional EEO may be declined. Repeated records use Add, individual
   field questions and Done; multiple roles use native multiSelect or repeated
   single choices with pagination, never a request to type a raw array.
4. Write a new private response JSON file under this runtime's user directory,
   using the host's permitted file tool (not public source or an engine prompt).
   Its only top-level fields are the packet's schema_version, session_id,
   revision, batch_id and answers. Each answer contains question_id and status
   (`answered`, `unknown`, `not_applicable`, `declined` or `notasked`).
   For answered choices, add `option_ids: [STABLE_OPTION_ID]` (multiple explicit
   selections when supported); for permitted text/number/date/path input, add
   `value: TYPED_VALUE`. Non-answer statuses have neither value nor option_ids.
   Do not include paths controlling outputs, arbitrary field paths or model
   guesses. Run `python autoapply-local.py setup --answer PRIVATE_PATH --ui UI`.
   Invoke the next returned native packet and repeat. Resume with `setup
   --guided --ui UI`; use `setup --guided --edit QUESTION_ID --ui UI` only
   with a catalog or previously issued record-question ID to revise an answer.
5. When the user chooses to finish, run `python autoapply-local.py setup --finish
   --ui UI`. Display the returned saved preview, including changed facts and
   unresolved fields, then invoke its native Confirm/Keep draft popup. Bind and
   wait for that response as above. Only an explicit Confirm selection permits
   `python autoapply-local.py setup --confirm`. Keep draft, timeout or
   interruption leaves the preview unconfirmed. Never silently recompute the
   preview or use ordinary `profile update --confirm` inside this loop. A stale
   dependency requires a fresh valid session/preview and user inspection, not
   an overwrite or automatic retry.

If a dependency changes, invoke a native popup with exactly two options:
Reload latest confirmed profile / Keep current draft. Bind that tool call and
wait for its actual response. Run `setup --restart --ui UI` only after the user
explicitly chooses Reload latest confirmed profile. Keep current draft, timeout,
interruption or no permitted native tool makes no changes; do not run restart.
Restart keeps a separate backup of the old progress and generated intake, reports
the backup paths, and opens a new session from current confirmed facts and intake.
Old answers and previews cannot apply to that new session. Inspect a new finish
preview before Confirm; restart never changes confirmed facts or reviews.

For seeded education, experience or authorization, select Edit an existing record
and choose its numbered native record option, then answer individual fields.
Similar record names have distinct numbered labels. Skip fields to keep their
existing facts, use Done to keep draft edits, or Cancel and keep original record
to discard that record's edits. Add remains for a new record. Do not
invent a record ID or use a nested --edit ID until a native packet has issued it.
Unchanged facts retain their metadata; changed facts require the saved preview
and explicit Confirm. No raw JSON or duplicate record is needed to make a change.

Answers and finish only save the private draft/progress. Existing confirmed facts,
profile revision, reviews and exact scoped answers change only through the saved
preview's explicit confirmation. Do not derive visa, authorization, sponsorship,
EEO or consent from resumes, student status or filter preferences. Internship and
full-time authorization/sponsorship are independent; exact form answers still
require country, job type, employer, question and effective time.

Setup preserves existing files and board choices, including explicit [].
Discovery seeds are Anthropic/Greenhouse, NVIDIA/Workday (software search) and
SpreeAI/Rippling only when config has no boards key; they do not guarantee
eligibility or sponsorship. `source extract PATH` produces draft source text,
never confirmed facts. `boards list` and `boards import PATH` preview sources
before explicit --confirm; public examples are in examples/boards.json. Advanced
intake resources.boards absent/null preserves choices; [] disables them.

Apply fills existing role-selected PDFs and holds tabs. Review rereads live
evidence; only an explicit submit request for reviewed IDs or all-reviewed permits
the durable submission workflow. Setup never authorizes Submit. Preserve every
held form for sign-in, account creation, CAPTCHA, unknown facts and errors.
Uncertain click outcomes require manual reconciliation without retries.
Gmail needs separate opt-in, selected-account verification and native search/read
tools. Setup does not access Gmail. Never send/draft/archive/label mail, scrape
browser Gmail, persist verification codes or read another workspace's private data.
Report command results, held tabs and unresolved work.
