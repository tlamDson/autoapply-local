# AutoApply Local 0.1.0a1 — bản thử nghiệm

Công cụ tìm và điền đơn cục bộ, dùng một form intake đầy đủ, fact được xác nhận,
resume PDF theo role, và luồng điền → kiểm → yêu cầu nộp rõ ràng.
[Hướng dẫn tiếng Anh](README.md).

Fact phụ thuộc thay đổi thì câu trả lời cần xác nhận lại; lỗi phân tích trước thao tác browser trả job vừa claim về `pending` để thử lại thủ công, còn tab đang giữ vẫn mở.

Các kiểm tra offline, DOM tổng hợp và package cài đặt được tự động hóa. Chưa xác
minh ATS thật, Gmail thật, toàn bộ luồng trên cả hai engine, phần cứng macOS hoặc
máy 8 GB. Những trường chưa hỗ trợ hoặc bước đăng nhập cần người dùng sẽ giữ tab.

Gmail tự động cần ép đúng account ở từng lời gọi. Hiện chỉ Claude hỗ trợ search/read
với account tường minh; Codex giữ `manual_required` tới khi transport ép được account.
Quick apply vẫn chạy; dùng Codex thì xử lý mail tay. Fill browser native còn rủi ro
với engine thật, cần qua cổng ATS live sau và chưa được coi là ổn định.

Automated Gmail requires account binding enforced on every call. Currently only
Claude supports search/read with an explicit account; Codex search/read remains
`manual_required` until a transport can enforce that binding. Quick apply remains
available; handle mail manually with Codex. Native browser fill still carries
real-engine risks and needs a live ATS gate before it can be considered stable.

Kết quả smoke test runtime Part2 trên Windows là kết quả lịch sử, trên revision
engine cũ hơn, trước các thay đổi engine, tool-gate và an toàn profile. Khi đó,
kiểm tra đã đạt với Codex 0.165.0-alpha.1.2 và Claude 2.1.312: JSON/Unicode ngoài
Git và MCP trình duyệt trên hai làn riêng mỗi engine. Fixture Claude được cấp rõ
quyền chỉ đọc `list_pages`; lượt chưa được cấp quyền dừng đúng. Kết quả lịch sử
này không xác nhận toàn bộ runtime hiện tại; các thay đổi hiện tại có kiểm thử
offline. Kết quả chưa xác nhận ATS thực tế, Gmail, phần cứng macOS, máy 8 GB hay
khả năng vận hành ổn định.

## Kho mã

Kho mã nguồn: [tlamDson/autoapply-local](https://github.com/tlamDson/autoapply-local).

```text
git clone https://github.com/tlamDson/autoapply-local.git
cd autoapply-local
```

## Cài đặt và khởi tạo

Cần Python 3.11+, Chrome, Node.js/npm và CLI engine đã chọn. Từ thư mục mã nguồn
release, cài package rồi tạo thư mục runtime riêng. Windows PowerShell:

```powershell
python -m venv ../autoapply-venv
& ../autoapply-venv/Scripts/Activate.ps1
python -m pip install .
python -m autoapply_local --user-dir ../autoapply-user setup
Set-Location ../autoapply-user
```

macOS:

```bash
python3 -m venv ../autoapply-venv
source ../autoapply-venv/bin/activate
python -m pip install .
python -m autoapply_local --user-dir ../autoapply-user setup
cd ../autoapply-user
```

Nếu tải wheel, thay `python -m pip install .` bằng
`python -m pip install path/to/autoapply_local-0.1.0a1-py3-none-any.whl`.
Package đã cài chạy setup từ thư mục bất kỳ, không cần checkout gốc.
Dependency được ghim phiên bản.
Tailor cần thêm pdflatex và Poppler; apply với PDF có sẵn không cần TeX.
Browser MCP ghim chrome-devtools-mcp 0.14.0; npm có thể tải phiên bản đó ở lần
đầu bạn phê duyệt connector. Không dùng profile Chrome cá nhân thường ngày.

Khởi tạo bằng `python -m autoapply_local --user-dir PATH setup`, rồi mở chính thư
mục runtime vừa tạo trong engine đã chọn, với virtual environment vẫn kích hoạt.
Setup cài command, skill, file hỗ trợ và launcher `autoapply-local.py`, giữ nguyên
file đã có và không ghi cấu hình toàn cục hay hook. Launcher luôn dùng thư mục
chứa nó, kể cả khi AUTOAPPLY_LOCAL_HOME trỏ nơi khác. Đặt đường dẫn có dấu cách
trong dấu nháy. Nếu không truyền root, setup dùng AUTOAPPLY_LOCAL_HOME hoặc mặc
định `~/.autoapply-local`; mở đúng thư mục mà setup tạo.

Sửa một form `user/profile-intake.md`, xem preview bằng
`python autoapply-local.py profile update PATH`, kiểm lại rồi thêm `--confirm`.
Intake có đủ 14 nhóm: engine/ngôn ngữ, liên hệ, PDF thay thế/role mapping, nhiều
hồ sơ học tập, role/job type, thời gian/địa điểm, work authorization, CPT/OPT/STEM
OPT, chính sách lọc, kinh nghiệm/skill, lương/EEO, lịch sử ứng tuyển, tailor,
Gmail và tài nguyên máy. Trống vẫn là unknown; không suy ra quyền làm việc hay
sponsorship từ tình trạng sinh viên. Sponsorship hiện tại/tương lai tách riêng.
Form có câu hỏi song ngữ thực tế cho đủ 14 nhóm. Tên và họ được xác nhận riêng
qua `first_name`/`last_name`, không đoán từ thứ tự họ tên pháp lý. Thêm câu trả lời
đúng nguyên văn vào danh sách `answers` ở cấp document, với `question`, `value`,
`status`, `provenance`, `confirmed_at`, `scope` gồm quốc gia, loại job, công ty,
nguyên văn câu hỏi và thời điểm. Lệnh `profile update PATH` hiện preview các đáp
án này; thêm `--confirm` sau khi kiểm để nhập. Không cần sửa profile nội bộ.

Trích PDF/TEX/DOCX chỉ tạo draft. Với Codex hoặc Claude đã chọn, chạy
`source extract PATH` để lấy text nháp, rồi nhờ engine đó giúp soạn nhóm liên hệ,
học tập, kinh nghiệm và skill trong intake, kèm nguồn cho từng fact. Bạn kiểm
nguồn, xem preview `profile update PATH`, rồi thêm `--confirm` để xác nhận.
Không suy authorization, visa, sponsorship hoặc đáp án EEO từ resume.
Resume sinh ra không phải nguồn fact mới. Đổi học tập/authorization/target
làm đáp án phụ thuộc và review cũ hết hiệu lực. Câu nhạy cảm cần xác nhận chính
xác theo câu hỏi, công ty, quốc gia, loại job và thời điểm.

## Bắt đầu: setup, cấu hình nguồn, fetch và review

Sau các bước cài đặt/setup trên, chạy từ chính thư mục runtime vừa tạo.
Launcher chọn rõ root cho mọi lệnh; chỉ đổi thư mục làm việc không chọn root
cho `python -m autoapply_local`.

```text
python autoapply-local.py source extract path/to/resume.pdf
```

Sửa `user/profile-intake.md`: chọn engine đã cài, đặt đường dẫn PDF resume có sẵn
và điền fact đã kiểm. Chỉ dùng engine đã chọn để giúp soạn intake từ text trích
và tài liệu nguồn theo hướng dẫn trên. Kiểm preview trước khi xác nhận:

```text
python autoapply-local.py profile update user/profile-intake.md
python autoapply-local.py profile update user/profile-intake.md --confirm
python autoapply-local.py boards list
python autoapply-local.py boards import examples/boards.json
python autoapply-local.py boards import examples/boards.json --confirm
python autoapply-local.py fetch
python autoapply-local.py apply --quick 1
python autoapply-local.py review all
```

Setup chỉ cài nguồn mặc định khi config chưa có khóa `boards`: Anthropic qua
Greenhouse, NVIDIA qua Workday (search `software`) và SpreeAI qua Rippling. Đây là
nguồn khám phá công khai có thể sửa, không bảo đảm eligibility hay sponsorship.
Chạy setup lại giữ lựa chọn của bạn, kể cả danh sách rỗng. Import mẫu ở trên xem
preview rồi gộp cùng bộ nguồn mặc định; muốn thêm hoặc đổi nguồn, chép/sửa JSON
list đó rồi preview và confirm file riêng. Import khớp định danh ATS ổn định, giữ
cấu hình không liên quan; input/config đổi thì cần preview mới. Endpoint override
phải là HTTPS công khai đúng ATS được hỗ trợ.

Cũng có thể sửa `resources.boards` trong intake đầy đủ: thiếu trường hoặc `null`
giữ nguồn hiện tại; `[]` chủ ý tắt nguồn đã cấu hình. Fetch khi không có nguồn cần
cấu hình qua `boards list` / `boards import` / `setup` hoặc truyền `--url` hỗ trợ,
không âm thầm thành công với zero job. Nguồn đã cấu hình vẫn có thể không có job
khớp. List/import nguồn và fetch không đòi các trường onboarding không liên quan.
Apply điền rồi giữ tab; review kiểm lại tab đó. Giữ form chưa nộp mở.

## Engine, quyền và lệnh

Đăng nhập engine đã chọn theo luồng bình thường. `doctor [ENGINE]` chỉ kiểm có
executable/dependency; không kết luận đã đăng nhập hay đã có connector. Setup
tạo `.mcp.json` và `mcp-codex.json` trong thư mục runtime để bạn kiểm và phê duyệt
browser MCP. Fragment Codex dùng `mcp_servers` để kiểm cấu hình; mỗi task tự
truyền endpoint của lane. Nếu config kế thừa đã có `autoapply-local-browser`,
cần bỏ entry trùng trước khi dùng transport riêng để tránh kế thừa env ngoài ý
muốn. Không thay cấu hình global. Mỗi task dùng một engine, CLI override có
ưu tiên, không pin model hoặc fallback sang engine khác; không bypass approvals.
Analysis/tailor tắt công cụ ngoài; browser chỉ dùng connector của lane đã chọn
và công cụ của thao tác đó. Các quyền runtime bình thường vẫn được giữ.

Task có công cụ ngoài trên Claude cần CLI từ 2.1.295 để hook chặn lỗi hoạt động.
Runtime probe đúng executable đã chọn trước khi dispatch. Bản cũ hoặc version
không đọc được trả `unsupported_version`; cập nhật thủ công theo hướng dẫn cài
đặt chính thức của CLI. Analysis/tailor không có công cụ ngoài không gắn hook
này. Chỉ tên nội bộ chính xác `StructuredOutput` được phép định dạng JSON;
runtime chỉ nhận success envelope và structured output đúng schema.
Native `permission_denials` có phần tử sẽ trả `permission_required` trước khi
nhận structured output. Phê duyệt tool bằng quy trình approval project bình
thường của CLI rồi thử lại thủ công; sản phẩm không tự ghi luật cấp quyền.

Prompt, JSON và đường dẫn có dấu cách/Unicode được truyền bằng UTF-8. Với npm
`.cmd` của hai package CLI đã biết trên Windows, resolver chạy bin JavaScript
bằng Node hoặc bin `.exe` native trực tiếp, `shell=False`. Hỗ trợ cả npm global
prefix và local `node_modules/.bin`. Shim khác hoặc bản cài
thiếu file trả `configuration`; hoàn tất cài đặt/postinstall native thủ công.
MCP Codex/Windows dùng argv cố định `cmd /c npx`, env hệ thống, timeout khởi động
20 giây, phiên bản MCP đã ghim và URL loopback được kiểm. Transport Claude tách
riêng. Các quyền phê duyệt bình thường vẫn áp dụng, không tự cấp quyền tool.

Codex dùng `--skip-git-repo-check` cho runtime ngoài Git và sandbox chỉ đọc.
Override TOML trong từng lần gọi tắt connector kế thừa, shell, browser/computer
native, thực thi code và multi-agent; browser chỉ mở thao tác MCP được chọn.
Giữ model/provider, kho đăng nhập và approval policy hiện có. Codex chỉ bật
`features.code_mode_host` và `features.workspace_dependencies` khi task yêu cầu
rõ công cụ MCP/connector native. Đây là hạ tầng broker; analysis/tailor và danh
sách tool rỗng giữ cả hai tắt, còn `features.code_mode` và các capability bị hạn
chế khác vẫn tắt. Trước inference,
probe `mcp list --json` lấy inventory MCP hiệu lực qua cùng CLI đã resolve,
thư mục làm việc và policy task, gồm cả hai cờ broker, trên Windows, macOS và
Linux. Chỉ giữ
identifier và cờ enabled; bỏ metadata transport, môi trường và xác thực.
Inventory lỗi/không dùng được, config không đọc được, policy managed đã phát
hiện nhưng chưa xác minh hoặc legacy profile không hỗ trợ đều bị chặn.
Không vượt requirements managed; lỗi cấu hình cần xử lý thủ công. Override
không chứng minh capability live. Không đọc auth file, chép credential hay cấu
hình toàn cục.

Lỗi được phân biệt `login_required`, `quota_exhausted`, `timeout`, `configuration`,
`permission_required`, `unsupported_version`, `invalid_response`, `engine_failure`, không in output
nhạy cảm từ CLI. Probe tối đa 10 giây, task tối đa 300 giây. Lỗi runtime trả exit
3; JSON/schema không hợp lệ trả exit 2. Không tự fallback engine hay retry Submit.
`doctor` báo riêng executable có tồn tại, argv đã resolve và dependency;
login/connector/capability vẫn là `not_verified`. Test offline chưa chứng minh
CLI thật, handshake MCP, ATS hay Gmail đã chạy được.

Từ runtime root, chạy `python autoapply-local.py LỆNH`; slash command và skill
Codex cục bộ cũng dùng launcher chọn đúng root này. Nếu gọi thẳng module hoặc
entrypoint đã cài, đặt `--user-dir PATH` trước tên lệnh.

Với `apply`, `review` và `submit`, `--approve-browser-tools` là opt-in tường minh
chỉ cho lệnh hiện tại. Cờ chỉ duyệt các tool MCP browser cục bộ mà từng task yêu
cầu và không được lưu lại. Policy hiện có, gồm managed policy, permission denials
và review gate, vẫn áp dụng. Cờ không duyệt tool Gmail, analysis hay tailor.
`apply --quick` và `review` không bao giờ tự nộp; vẫn cần lệnh `submit` tường minh
sau khi đã review.

- `setup`: khởi tạo intake và state trống, không ghi đè file cũ.
- `boards list`: xem nguồn khám phá job đã cấu hình.
- `boards import PATH [--confirm]`: kiểm, chuẩn hóa và bỏ trùng JSON list nguồn;
  xem preview nguồn thêm/đổi rồi xác nhận để gộp.
- `profile update PATH [--confirm]`: xem preview rồi xác nhận intake.
- `source extract PATH`: trích PDF/TEX/DOCX thành draft JSON ở `user/drafts`;
  kiểm, chuyển fact vào intake, preview rồi xác nhận `profile update`.
- `history import PATH [--confirm]`: preview lịch sử CSV/XLSX rồi xác nhận nhập,
  giữ trạng thái đơn lịch sử và chống trùng.
- `doctor [ENGINE]`: kiểm dependency và nêu phần login/connector chưa xác minh.
- `fetch [ENGINE] [--url URL]`: lấy board Greenhouse/Workday/Rippling đã cấu hình
  hoặc URL hỗ trợ nhập tay; sửa nguồn qua `boards import` hoặc `resources.boards`
  trong intake, mẫu ở examples/boards.json.
- `apply [ENGINE] --quick N|ID,ID`: chọn PDF theo role, điền và giữ tab; không nộp
  hay tailor. Trước khi mở browser, stderr hiện role mục tiêu, kỳ/khoảng tuyển
  dụng và PDF của từng job đã chọn; stdout cuối vẫn là JSON.
- `tailor [ENGINE] JD_FILE_OR_URL`: tạo run TEX/PDF/changes mới theo chính sách,
  tối đa một lần sửa; không mở form, chưa kiểm hình ảnh thì `needs_review`.
- `review ID,ID|all`: đọc lại form đang giữ, kiểm job/tab/field/fact/PDF còn mới.
- `submit ID,ID|all-reviewed`: yêu cầu nộp rõ ràng các job đã review; ghi intent
  bền vững trước click, chỉ ghi tracker sau confirmation khớp. Không rõ kết quả
  thì cần đối soát thủ công, không tự click lại.
- `mail-check [ENGINE]`: opt-in connector Gmail native, chỉ đọc/tìm đúng account.
- `status`: xem tab đang giữ và đơn chưa giải quyết, không cần gọi engine.

Mặc định một lane Chrome sạch và bền giữa các lệnh. Hai/ba lane cần opt-in.
Kiểm RAM và giới hạn tab là heuristic, chưa phải benchmark máy 8 GB. Khi cần tạo
tài khoản, đăng nhập, CAPTCHA/MFA hoặc sửa control chưa hỗ trợ, thao tác trong tab
đang giữ rồi tiếp tục. Không đóng form chưa nộp. Package không thu/lưu mật khẩu.
Chạy lại `apply [ENGINE] --quick ID` với đúng ID để tiếp tục cùng tab sau khi xử lý
blocker. Nếu login/CAPTCHA/thiếu dữ liệu còn vướng, giữ trạng thái manual. Intent
đã nộp hoặc kết quả chưa rõ cần đối soát, không đi đường tiếp tục này.

Job mới luân phiên lane trong giới hạn opt-in; mỗi job lưu lane/debug endpoint để
restore đúng browser. Kiểm RAM và trần tab trước từng job, giữ kết quả từng phần
khi hết tài nguyên; vẫn được kiểm/tiếp tục tab đang giữ lúc chạm trần.
Luồng nhiều trang truyền PDF đã chọn vào thao tác native, upload tại bước Resume
trước Next. Review kiểm text/value mới và attachment khớp receipt upload được
browser quan sát; hash dự kiến của file cục bộ không tự chứng minh đã upload.

Gmail cần opt-in read/search và account đã xác minh. Quyền lấy mã job hiện tại là
mục riêng; mã không ghi bền vững. Không gửi/draft/archive/label, không scrape Gmail
trên browser và không tạo OAuth riêng. Thiếu connector thì chuyển sang thủ công.
Mail mơ hồ hoặc không có mail không chứng minh đơn thất bại.
Chỉ sau opt-in mới được tìm connector/metadata tài khoản; chỉ đọc mail sau khi
xác minh đúng account và công cụ read/search. Thiếu metadata hoặc connector chỉ
đọc thực sự thì trả manual fallback và vẫn giữ khả năng apply.

Tracker XLSX cục bộ là nguồn chuẩn; khôi phục ledger phải đối chiếu mọi trạng thái
lịch sử. Nhập lịch sử CSV/XLSX cần preview và confirm; trạng thái không biết phải
xử lý rõ ràng, không biến thành pending. Giữ backup tracker và ledger.

## Kiểm và export

Từ thư mục release: `python -m unittest discover -s tests -p "test_*.py" -v`,
`node --test tests/adapters.test.cjs`, `python scripts/check_release.py`.
Test dùng dữ liệu tổng hợp và mock engine/browser/network; smoke TeX chỉ chạy
khi có công cụ tùy chọn. Manifest và scanner kiểm nội dung nguồn lẫn archive
wheel/sdist/ZIP cuối; .gitignore không thay thế kiểm. Không publish user data,
credential hay build output. Repo public chứa source test tổng hợp đã qua audit
để CI chạy; wheel và sdist không chứa test.

Đây là bản public prerelease thử nghiệm; xem kết quả CI được ghi nhận tại
[Actions](https://github.com/tlamDson/autoapply-local/actions).
Các kiểm tra cục bộ được ghi nhận ở đây chạy trên Windows; workflow khai báo Windows/macOS với Python 3.11 và 3.12,
chưa khẳng định CI remote đã chạy hay đã kiểm phần cứng macOS.

`pyproject.toml` là nguồn chuẩn cho tên project, version hiện tại, dependencies và
glob package-data của wheel. Tên artifact và metadata phải khớp version đó.
`--artifacts DIR` kiểm mọi wheel, sdist và ZIP source cuối trong thư mục: đúng bộ
file, byte của source đã đóng gói, metadata, hash/kích thước trong wheel RECORD và
các luật privacy/đường dẫn hiện có. Artifact cũ bị từ chối kể cả khi nằm cạnh bản
hiện tại. Mặc định là `--expect wheel,sdist`; dùng `--expect wheel,sdist,zip` để
yêu cầu đủ ba loại. Thiếu loại được yêu cầu làm audit thất bại.

Wheel chứa module Python trong package và package-data đã khai báo, cùng metadata
wheel và file license. Sdist chứa allowlist export trừ test, cộng metadata sinh
ra cho source distribution. ZIP source chứa đúng toàn bộ allowlist, kể cả test
tổng hợp, dưới thư mục gốc có version. Trong ba artifact này, chỉ ZIP source chứa
test. `--source-zip DIR` kiểm source trước, tạo ZIP theo thứ tự sort với timestamp
cố định rồi tự kiểm ZIP. Lệnh từ chối ghi đè artifact đã tồn tại.

Từ thư mục release, tạo export mới và thư mục artifact cuối tách riêng (thư mục
export và install-work trong ví dụ phải chưa tồn tại):

```text
python scripts/check_release.py --export ../autoapply-export-new
cd ../autoapply-export-new
python -m pip wheel --no-deps --no-build-isolation --wheel-dir ../autoapply-artifacts-new .
python -m build --sdist --no-isolation --outdir ../autoapply-artifacts-new
python scripts/check_release.py --source-zip ../autoapply-artifacts-new
python scripts/check_release.py --artifacts ../autoapply-artifacts-new --expect wheel,sdist,zip
python scripts/check_install.py --artifacts ../autoapply-artifacts-new --work-dir "../install thử ünïcode new"
```

Các lệnh build cần build requirements đã ghim trong `pyproject.toml` và package
`build`. Smoke cài đặt kiểm artifact cuối trước khi tạo hai venv mới có đủ
dependencies, rồi kiểm bản cài wheel và sdist từ ngoài source tree. Thư mục làm
việc chứa lệnh/kết quả và tách khỏi source public. Nếu đã chuẩn bị wheelhouse
dependency offline, thêm `--wheelhouse PATH` vào `check_install.py`; dependencies
vẫn được cài đầy đủ. Đây là quy trình kiểm tích hợp, chưa phải báo cáo tích hợp
đã hoàn tất cho artifact hiện tại.

License MIT, ghi nguồn tại NOTICE.
