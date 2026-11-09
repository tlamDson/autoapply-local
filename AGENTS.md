# AutoApply Local

This experimental standalone product keeps runtime user data outside tracked
source. Follow the user's selected engine per task without model pinning or
fallback. Use only confirmed facts and exact question-scoped answers. Never
infer work authorization, sponsorship or consent from student status or filters.

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
optional editor/export. This setup flow never permits Submit or Gmail access.

If setup becomes stale, use the native Reload latest confirmed profile / Keep
current draft popup. Run `setup --restart --ui UI` only after explicit Reload;
Keep, timeout or no native tool preserves progress. Restart backs up old progress
and the generated intake inside the runtime, then creates a fresh session without
changing confirmed facts. Old answers/previews remain invalid. Existing education,
experience and authorization records have a numbered native picker and field
editor; Cancel keeps the original record, and unchanged facts retain metadata.

`apply` fills and holds; `review` checks fresh live evidence; only an explicit
`submit` command permits one Submit click for a reviewed selection. Preserve held
forms for sign-in, account creation, CAPTCHA, unknown questions, errors and manual
inspection. Unknown submission outcomes require manual reconciliation, never retry.
Gmail is opt-in, selected-account, native connector search/read only. Do not send,
draft, archive, label, scrape browser Gmail, or persist verification codes.

Use temporary synthetic fixtures for tests. Never run real external engines,
browser automation or Gmail during offline tests. Run Python unittest discovery,
Node adapter tests and scripts/check_release.py from this release root. Do not
claim live ATS, Gmail, both-engine, macOS hardware or 8 GB validation from mocks.

## Giả định của người thực thi

- Runtime CLI trả exit 2 cho input/schema không hợp lệ, exit 3 cho thiếu dependency/lỗi engine; doctor chỉ báo executable và trạng thái login/connector chưa xác minh.
- Runtime dữ liệu mặc định ở thư mục người dùng `.autoapply-local`; có thể đổi bằng AUTOAPPLY_LOCAL_HOME hoặc --user-dir.
- Giữ JSON schema nhỏ, kiểm đệ quy type/required/properties/enum/items; payload không khớp bị từ chối trước khi dùng.
- Bổ sung lối CLI source extract và history import để người dùng thường truy cập workflow đã có; test dispatch mới đỏ trước khi thêm lệnh, không thay assertion cũ.
- Rewrite dùng đối chiếu từ có thứ tự và giữ qualifier/ownership, từ chối thay đổi ngữ nghĩa chưa có nguồn; đáp án hết hiệu lực giữ provenance và valid=False.
- Lịch sử nhập có trạng thái chưa biết bị từ chối ở preview; trạng thái biết được chuẩn hóa lowercase. Metadata sdist cố định được allowlist riêng và vẫn quét nội dung.
- Intake đã confirm với lanes=2 hoặc 3 là opt-in nhiều lane; runtime vẫn kiểm RAM và giữ mọi tab chưa nộp.
- Fixture ZIP đặt ZipInfo.filename trực tiếp để giữ nguyên dấu backslash trên Windows; zipfile tự chuẩn hóa tên khi nhận chuỗi nên ca traversal trước đó không chứa member mà assertion cần kiểm. Không đổi assertion.

## Câu hỏi của người thực thi

Không có câu hỏi chặn trong phạm vi triển khai offline.
