# AutoApply Local

This experimental standalone product keeps runtime user data outside tracked
source. Follow the user's selected engine per task without model pinning or
fallback. Use only confirmed facts and exact question-scoped answers. Never
infer work authorization, sponsorship or consent from student status or filters.

The terminal part of setup, `setup --terminal`, runs in a plain terminal before any
engine opens: language, engine with one small login/quota probe call, quota stop
percent, resume PDFs tagged with roles (copied to `resumes/`) and jobs per run,
saved to `config.json`; then it opens the tool's clean Chrome profile
(`chrome/lane-1`) on Gmail for the user to sign in and scans 183 days of
confirmation emails into the tracker (deduplicated). It then hands the terminal to the selected engine with
`/setup` (Codex: `$autoapply-local setup`); that interactive handoff is not an
interview subprocess. Inside an engine, never run `setup --terminal`.

For first setup, including when the user only says setup, follow the native
onboarding loop in `.claude/commands/setup.md` or
`.codex/skills/autoapply-local/SKILL.md`. Bootstrap safely, discover the actual
question tool in the top-level host, then run `setup --guided --ui UI` through
the runtime's `python autoapply-local.py` launcher. Invoke its native packet,
wait for the user's real answer, map stable IDs, and pass a private response file
to `setup --answer PATH`. Finish previews a draft; only a native explicit Confirm
permits `setup --confirm` for the saved preview. No native tool or forbidden mode
means `--ui none` / `ui_required`, preserving progress. Never ask setup questions
as chat prose, launch an engine subprocess for the interview, change mode/engine
or infer an answer from timeout/preselection. The advanced intake remains an
optional editor/export. This native setup flow never permits Submit or Gmail
access; only the terminal Gmail step reads the Gmail tab.

If setup becomes stale, use the native Reload latest confirmed profile / Keep
current draft popup. Run `setup --restart --ui UI` only after explicit Reload;
Keep, timeout or no native tool preserves progress. Restart backs up old progress
and the generated intake inside the runtime, then creates a fresh session without
changing confirmed facts. Old answers/previews remain invalid. Existing education,
experience and authorization records have a numbered native picker and field
editor; Cancel keeps the original record, and unchanged facts retain metadata.

`apply` fills and holds; `review` checks fresh live evidence. The tool never
clicks a Submit control: no command, workflow, browser call or engine task may
request one, and the user submits each held form by hand, then records it with
`submitted ID,ID`, which frees its held-tab slot. `track` (also run first by
`apply`) records a held form whose tab shows the ATS confirmation page or whose
confirmation email is in the Gmail tab; it only lists or closes tabs already on
a confirmation page and never runs a script in a job tab. Preserve held forms for
sign-in, account creation, CAPTCHA, unknown questions, errors and manual
inspection. There is no Gmail command or OAuth. Gmail is read only in the Gmail
tab of the tool's own Chrome profile, signed in by the user, through the
read-only `adapters/gmail.cjs` (search and result rows); never open, send,
draft, archive or label a message, store a verification email, or persist
verification codes. Tests use synthetic Gmail DOM and rows only.

Workday sign-in credentials live only in `user/secrets.json` (`autoapply_local/wd_cred.py`): never
print, log or echo a password, mask text with `wd_cred.redact`, and never track `user/` or any
secrets file; `scripts/check_release.py` fails when git tracks one.

The Workday driver (`workday_driver.py`) fills each step from confirmed facts, uploads the
selected resume on the step that asks for it and presses Next only through the guarded
`press()` of `adapters/workday_steps.cjs` (fixed actions, never a control that says Submit,
never on the last step); it stops on Review. Its account step (`workday_account.py`) runs over
loopback CDP in the held tab, never through an engine: Apply, Apply Manually, sign-in with the
stored credential, account creation when sign-in fails, and the one-time code read from the
subject/snippet of Gmail result rows. The credential and code are typed only on the page just
checked and only when the tab's host is the job's tenant; the code is typed and dropped, never
stored or returned. The tenant's account terms box is never ticked: an unticked box holds the
filled create-account form for the user. A resume upload counts only when the browser reports it
and a fresh audit shows the file. CAPTCHA, a link-only verification mail, a rejected code, another
host or an unknown page holds the tab.

Use temporary synthetic fixtures for tests. Never run real external engines,
browser automation or Gmail during offline tests. Run Python unittest discovery,
Node adapter tests and scripts/check_release.py from this release root. Do not
claim live ATS, Gmail, both-engine, macOS hardware or 8 GB validation from mocks.

## Giả định của người thực thi

- Runtime CLI trả exit 2 cho input/schema không hợp lệ, exit 3 cho thiếu dependency/lỗi engine; doctor chỉ báo executable và trạng thái login/connector chưa xác minh.
- Runtime chọn theo --user-dir > AUTOAPPLY_LOCAL_HOME > source đã nhận diện/user (aa-04; formerly .autoapply-local) > thư mục người dùng .autoapply-local; giả định PR1: marker package là autoapply_local/__init__.py, TOML thiếu/hỏng không nhận diện source, launcher nguồn không đối số chạy setup cạnh nó; launcher runtime giữ root riêng.
- Giữ JSON schema nhỏ, kiểm đệ quy type/required/properties/enum/items; payload không khớp bị từ chối trước khi dùng.
- Bổ sung lối CLI source extract và history import để người dùng thường truy cập workflow đã có; test dispatch mới đỏ trước khi thêm lệnh, không thay assertion cũ.
- Rewrite dùng đối chiếu từ có thứ tự và giữ qualifier/ownership, từ chối thay đổi ngữ nghĩa chưa có nguồn; đáp án hết hiệu lực giữ provenance và valid=False.
- Lịch sử nhập có trạng thái chưa biết bị từ chối ở preview; trạng thái biết được chuẩn hóa lowercase. Metadata sdist cố định được allowlist riêng và vẫn quét nội dung.
- Intake đã confirm với lanes=2 hoặc 3 là opt-in nhiều lane; runtime vẫn kiểm RAM và giữ mọi tab chưa nộp.
- Fixture ZIP đặt ZipInfo.filename trực tiếp để giữ nguyên dấu backslash trên Windows; zipfile tự chuẩn hóa tên khi nhận chuỗi nên ca traversal trước đó không chứa member mà assertion cần kiểm. Không đổi assertion.

- Installer (aa-04): install.ps1/install.sh only locate Python 3.11+ and git, then hand off to scripts/install.py; a missing dependency prints its winget/brew command and exits 3, a failed pip install exits 4; system software is never installed.

- Update (aa-21): `update` works only from a source checkout; it backs up user/ (skipping backups/, browser/, chrome/) before any change, pulls with `git pull --ff-only` or extracts the latest release zip (never into user/, .venv/ or .git/), reinstalls, then runs migrations recorded in user/migrations.json. `apply`'s release lookup is skipped when AUTOAPPLY_LOCAL_NO_UPDATE_CHECK is set; the offline suite sets it.

## Câu hỏi của người thực thi

Không có câu hỏi chặn trong phạm vi triển khai offline.
