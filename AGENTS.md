# AutoApply Local

This experimental standalone product keeps runtime user data outside tracked
source. Follow the user's selected engine per task without model pinning or
fallback. Use only confirmed facts and exact question-scoped answers. Never
infer work authorization, sponsorship or consent from student status or filters.

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
- Runtime chọn theo --user-dir > AUTOAPPLY_LOCAL_HOME > source đã nhận diện/.autoapply-local > thư mục người dùng .autoapply-local; giả định PR1: marker package là autoapply_local/__init__.py, TOML thiếu/hỏng không nhận diện source, launcher nguồn không đối số chạy setup cạnh nó; launcher runtime giữ root riêng.
- Giữ JSON schema nhỏ, kiểm đệ quy type/required/properties/enum/items; payload không khớp bị từ chối trước khi dùng.
- Bổ sung lối CLI source extract và history import để người dùng thường truy cập workflow đã có; test dispatch mới đỏ trước khi thêm lệnh, không thay assertion cũ.
- Rewrite dùng đối chiếu từ có thứ tự và giữ qualifier/ownership, từ chối thay đổi ngữ nghĩa chưa có nguồn; đáp án hết hiệu lực giữ provenance và valid=False.
- Lịch sử nhập có trạng thái chưa biết bị từ chối ở preview; trạng thái biết được chuẩn hóa lowercase. Metadata sdist cố định được allowlist riêng và vẫn quét nội dung.
- Intake đã confirm với lanes=2 hoặc 3 là opt-in nhiều lane; runtime vẫn kiểm RAM và giữ mọi tab chưa nộp.
- Fixture ZIP đặt ZipInfo.filename trực tiếp để giữ nguyên dấu backslash trên Windows; zipfile tự chuẩn hóa tên khi nhận chuỗi nên ca traversal trước đó không chứa member mà assertion cần kiểm. Không đổi assertion.

## Câu hỏi của người thực thi

Không có câu hỏi chặn trong phạm vi triển khai offline.
