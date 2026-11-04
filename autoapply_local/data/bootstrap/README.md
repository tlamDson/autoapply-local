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

Edit `user/profile-intake.md`, then preview and confirm your facts:

```text
python autoapply-local.py profile update user/profile-intake.md
python autoapply-local.py profile update user/profile-intake.md --confirm
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

## Document drop folders / Thư mục tài liệu

Setup JSON returns absolute `resume_dir` and `additional_document_dir` paths
inside this runtime. Put resume PDFs and TEX/DOCX resume source files in
`user/resume/`; put transcripts, certificates, cover letters and other supporting
documents in `user/additional-document/`. Each folder's general README is created
only when absent. Repeat setup preserves all documents and edited guides.
Folder/file conflicts and symlinks escaping the runtime are rejected before
writing either guide. Keep runtime `user/` data out of Git and release artifacts.

Storing documents does not select a PDF, extract or confirm facts, enable tailor,
or upload files. Explicitly set `main_pdf` in the intake's `resumes` group to the
PDF's absolute path; relative paths resolve from the caller's working directory.
Preview profile update, check facts, then explicitly confirm. PDF-only quick
apply needs no LaTeX. Additional documents are not uploaded automatically.

Để resume PDF và nguồn TEX/DOCX vào `user/resume/`; để bảng điểm, chứng chỉ,
cover letter và tài liệu bổ sung vào `user/additional-document/`. Chọn rõ PDF
bằng đường dẫn tuyệt đối trong intake, xem preview rồi xác nhận fact.
Chỉ lưu file không tự chọn PDF, trích fact, xác nhận fact, bật tailor hay upload.
Quick apply với PDF có sẵn không cần LaTeX; tài liệu bổ sung không tự upload.

Live ATS, Gmail, both-engine operation, macOS hardware and an 8 GB machine remain
untested. See AGENTS.md for runtime constraints. Back up your local runtime data.
