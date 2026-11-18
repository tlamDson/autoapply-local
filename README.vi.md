# AutoApply Local 0.1.0a1 — bản thử nghiệm

Công cụ apply nhanh cho người tìm thực tập và việc new-grad, ưu tiên du học sinh,
chạy trên Windows và macOS. Công cụ tìm job trên các board ATS công khai, điền
từng đơn bằng chính resume PDF của bạn (đúng bản cho role đó), rồi để form đã điền
mở trong một tab trình duyệt. Công cụ không bao giờ bấm Submit: bạn tự kiểm và tự
nộp từng form đang giữ. Tracker cục bộ được cập nhật từ các form bạn đã nộp và từ
mail xác nhận trong tab Gmail của bạn.
[Hướng dẫn tiếng Anh](README.md).

Tóm tắt luồng: cài một lần, trả lời phần setup trên terminal, xác nhận fact trong
engine AI lập trình của bạn (Claude Code hoặc Codex), rồi gõ `apply` mỗi khi muốn
điền một đợt đơn.

## Trạng thái và giới hạn

Các kiểm tra offline, DOM tổng hợp và package cài đặt được tự động hóa. Chưa xác
minh ATS thật, Gmail thật, toàn bộ luồng trên cả hai engine, phần cứng macOS hoặc
máy 8 GB. Đây là bản thử nghiệm, chưa phải tự động hóa ổn định. Những trường chưa
hỗ trợ hoặc bước tài khoản cần người dùng sẽ giữ tab mở cho bạn.
macOS chưa được thử thật: trên macOS mới chỉ có test offline và job cài đặt của CI.

Lấy job (boards và `--url`) chỉ hỗ trợ Greenhouse, Workday và Rippling trong
v0.2.0a1. Ashby, Lever và Workable đã có driver điền form nhưng chưa có phần lấy job;
phần lấy job của chúng có ở bản tiếp theo.

Fill browser native còn rủi ro
với engine thật, cần qua cổng ATS live sau và chưa được coi là ổn định.

## Cài đặt

Một dòng. Windows PowerShell:

```text
irm https://raw.githubusercontent.com/tlamDson/autoapply-local/main/install.ps1 | iex
```

macOS:

```text
curl -fsSL https://raw.githubusercontent.com/tlamDson/autoapply-local/main/install.sh | bash
```

Hoặc clone [kho mã](https://github.com/tlamDson/autoapply-local) rồi chạy installer
trong đó (macOS dùng `./install.sh`):

```text
git clone https://github.com/tlamDson/autoapply-local.git
cd autoapply-local
./install.ps1
```

Script clone kho mã vào `./autoapply-local` (hoặc dùng checkout chứa nó). Script
kiểm Python 3.11+, git, Node.js/npm và Google Chrome. Thiếu thứ nào thì in đúng
lệnh `winget install ...` hoặc `brew install ...` rồi thoát với mã 3; script không
bao giờ tự cài phần mềm hệ thống. Đủ hết thì tạo `.venv` trong kho mã, chạy
`pip install -e .` và chạy `setup` một lần để tạo thư mục runtime `<repo>/user/`
cùng command, skill và launcher `autoapply-local.py`. Chạy lại installer an toàn:
`.venv` có sẵn được dùng lại và setup giữ nguyên mọi file bạn đã sửa. Thêm
`--no-setup` để chỉ cài. Không ghi gì ra ngoài kho mã; mọi dữ liệu của bạn nằm
trong `<repo>/user/` (đã gitignore, xem [Dữ liệu của bạn](#dữ-liệu-của-bạn)).

Cài thủ công, Windows PowerShell:

```powershell
python -m venv .venv
& .venv/Scripts/Activate.ps1
python -m pip install .
python -m autoapply_local --user-dir ./user setup
Set-Location user
```

macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m autoapply_local --user-dir ./user setup
cd user
```

Mọi lệnh bên dưới chạy từ `<repo>/user/` với `.venv` của kho mã đã kích hoạt,
dạng `python autoapply-local.py LỆNH`. Launcher được tạo đó luôn dùng chính thư
mục của nó làm runtime root. Launcher nguồn ở gốc kho mã (`python autoapply-local.py`
tại đó) cũng mặc định dùng `<repo>/user/`; không có đối số thì chạy `setup`. Với
module hoặc entry point đã cài, đặt `--user-dir PATH` trước tên lệnh: runtime root
là `--user-dir`, rồi `AUTOAPPLY_LOCAL_HOME`, rồi `user/` của checkout nguồn nhận
diện được, cuối cùng `~/.autoapply-local` khi ngoài checkout nguồn. Runtime có sẵn
không bao giờ bị di chuyển.

Apply nhanh dùng PDF có sẵn nên không cần TeX; `source extract` với PDF cần
`pdftotext` của Poppler. Dependency được ghim trong `pyproject.toml` và
`requirements.txt`. Connector browser ghim chrome-devtools-mcp 0.14.0; npm có thể
tải bản đó khi bạn phê duyệt connector lần đầu.

## Thiết lập, phần 1: terminal

Chạy phần terminal trong một terminal thường, trước khi mở engine nào (chạy lại
lúc nào cũng được; câu trả lời hiện tại được đưa làm mặc định):

```text
python autoapply-local.py setup --terminal
```

1. Ngôn ngữ: English hoặc Tiếng Việt.
2. Engine: Claude Code hoặc Codex. Một lần gọi nhỏ kiểm bạn đã đăng nhập và hiện
   mức quota 5 giờ và quota tuần đã dùng. Chưa đăng nhập: in cách đăng nhập rồi chờ
   bạn thử lại hoặc thoát. Sau 3 lần kiểm thất bại, nó hiện lỗi của engine và dừng
   setup, trừ khi bạn gõ r để thử lại.
3. Phần trăm quota để dừng (mặc định 90): mức dùng quota 5 giờ hoặc quota tuần của
   engine mà tại đó `apply` dừng (xem [Dùng hằng ngày](#dùng-hằng-ngày-apply)).
   Giá trị lưu trong `config.json`.
4. Resume PDF: thêm một hoặc nhiều file và gắn tag role cho từng file (SWE, Data,
   ML hoặc chữ tùy ý). File được chép vào `resumes/` của runtime root và thành
   `main_pdf`/`main_tags` và `alternatives` trong `config.json`.
5. Số job mỗi lượt (mặc định 10). Trần tab đang giữ được tính từ RAM trống và
   hiện ra.
6. Gmail: tool mở profile Chrome sạch riêng của nó (`chrome/lane-1`) ở trang Gmail;
   bạn tự đăng nhập ở đó, chỉ trên trang web Gmail, và KHÔNG bật đồng bộ Chrome
   (Chrome sync) trong profile đó (đồng bộ sẽ cài extension của bạn vào đó và mở
   tab của chúng). Sau đó tool tìm mail xác nhận nộp đơn của 183 ngày qua và
   thêm mỗi đơn vào tracker đúng một lần. Có thể bỏ qua bước này.
7. Engine đã chọn mở ngay trong terminal này với `/setup` (Codex:
   `$autoapply-local setup`) cho phần 2. `--no-launch` chỉ in bước đó ra.

Câu trả lời được lưu vào `config.json` ngay khi bạn trả lời, nên setup bị dừng
giữa chừng vẫn giữ những gì đã trả lời. Trong engine, không bao giờ chạy
`setup --terminal`.

## Thiết lập, phần 2: trong engine

Lệnh `/setup` của engine (Codex: `$autoapply-local setup`) hỏi phần còn lại bằng
popup câu hỏi native của chính engine, mỗi lượt tối đa ba câu. Không cần sửa JSON.

1. Fact đọc từ resume, từng nhóm một (liên hệ, học tập, kinh nghiệm, kỹ năng),
   mỗi nhóm có Xác nhận / Sửa.
2. Những gì resume không có: quyền làm việc, nhu cầu bảo lãnh, địa chỉ, EEO (có
   thể từ chối), kỳ tuyển dụng và role mong muốn. Bỏ qua hoặc không biết không bao
   giờ thành Không; quyền làm việc, visa, bảo lãnh và EEO không bao giờ được đoán
   từ resume.
3. Bộ lọc job suy ra từ các câu trả lời đó, có Xác nhận / Sửa: cần bảo lãnh thì bỏ
   job ghi "no sponsorship", chỉ công dân Mỹ, clearance và ITAR; kỳ và loại công
   việc tạo bộ lọc tiêu đề (ví dụ chỉ thực tập, bỏ tiêu đề senior hoặc full-time);
   job đăng trong 7 ngày; các công ty bạn loại trừ. Bộ lọc bạn đã sửa giữ nguyên
   khi câu trả lời đổi về sau.
4. Một preview đã lưu của toàn bộ, rồi lựa chọn native Confirm / Keep draft. Chỉ
   khi bạn chọn Confirm rõ ràng thì fact mới được lưu.
5. Một form demo offline (`demo`: trang cục bộ không có nút Submit, mở trong
   Chrome riêng của tool) chứng minh mọi thứ chạy được.
6. `fetch` lần đầu; `report.summary` cho biết bao nhiêu job từ bao nhiêu board qua
   bộ lọc. Setup xong.

Phía sau popup, engine chạy các lệnh này; file trả lời được ghi từ câu trả lời
thật của bạn trong popup, bạn không tự gõ (`claude` là UI cho AskUserQuestion của
Claude Code; Codex dùng `codex` hoặc `codex-async`):

```text
python autoapply-local.py setup --guided --ui claude
python autoapply-local.py setup --answer user/setup-response.json --ui claude
python autoapply-local.py setup --finish --ui claude
python autoapply-local.py setup --confirm
python autoapply-local.py demo
python autoapply-local.py fetch
```

Engine khám phá đúng công cụ hỏi native thật trước. Không có công cụ được phép thì
dùng `--ui none`, lệnh trả `ui_required` và giữ tiến độ để bạn mở lại engine hoặc
mode hỗ trợ. Timeout, gián đoạn hay lựa chọn được chọn sẵn không bao giờ là câu trả
lời, và không có lối hỏi thay bằng chat. `setup --guided --edit QUESTION_ID --ui UI`
mở lại một câu. Record học tập, kinh nghiệm và authorization đã có dùng bộ chọn
Edit an existing record có số thứ tự; Cancel giữ nguyên record gốc. Nếu fact đã xác
nhận đổi trong lúc đang có draft, engine hỏi Reload latest confirmed profile / Keep
current draft; chỉ khi bạn chọn Reload rõ ràng mới chạy `setup --restart --ui UI`,
lệnh này backup draft cũ và mở phiên mới (cần finish và Confirm preview mới).

Nguồn job là các board công khai đi kèm, chia hai nhóm S&P 500 (`sp500`) và unicorn
(`unicorn`). `boards groups` liệt kê nhóm, `boards group NAME on|off` tắt hoặc bật
cả nhóm mà không xoá board, `boards import PATH` xem preview board của bạn rồi
`--confirm` để thêm; board của bạn luôn được fetch. Các nguồn này không bảo đảm
eligibility hay bảo lãnh.

```text
python autoapply-local.py boards groups
python autoapply-local.py boards group unicorn off
python autoapply-local.py boards import examples/boards.json
python autoapply-local.py boards import examples/boards.json --confirm
```

Để sửa nâng cao, `user/profile-intake.md` chứa mọi nhóm fact trong một form: xem
preview bằng `profile update PATH`, rồi thêm `--confirm`. `source extract PATH`
biến PDF/TEX/DOCX thành text nháp để kiểm; bản nháp không bao giờ là fact đã xác
nhận. Đổi profile làm các câu trả lời và review phụ thuộc hết hiệu lực.

## Dùng hằng ngày: apply

Không có gì chạy theo lịch. Một lượt chỉ bắt đầu khi bạn gõ `apply [N]`: `/apply 5`
trong Claude Code, `$autoapply-local apply 5` trong Codex, hoặc trong terminal:

```text
python autoapply-local.py apply 5
```

Không có N thì điền đúng số job mỗi lượt đã chọn trong setup. Một lượt, theo thứ tự:

1. Track: tab đang giữ đã sang trang xác nhận của ATS và mail xác nhận trong tab
   Gmail thành dòng tracker (xem `track` ở mục [Lệnh](#lệnh)). Tab đã điền nhưng
   chưa nộp được liệt kê, giữ nguyên và tính vào trần tab. Form đang giữ mà Chrome
   đã mất tab (ví dụ sau khi khởi động lại) được mở lại đúng URL và điền lại.
2. Fetch job mới từ các board đang bật.
3. Lọc theo bộ lọc đã xác nhận và bỏ job đã có trong tracker hoặc trong lịch sử
   Gmail/file nhập (cùng công ty và role).
4. Chọn resume: PDF có tag role trùng nguyên từ với tiêu đề job (từ viết tắt phổ
   biến khớp cả hai chiều: SWE = software engineer/engineering, SDE = software
   development engineer, ML = machine learning, AI = artificial intelligence,
   FE/BE = frontend/backend); nhiều PDF khớp →
   PDF nêu nhiều từ khóa công nghệ của mô tả job nhất (không dùng model; hòa thì giữ
   thứ tự cấu hình); không PDF nào khớp và bạn có hơn một PDF → `apply` hỏi một lần
   cho mỗi role trong terminal và nhớ ở `user/resume-choices.json` (không có
   terminal thì dùng resume chính và in lý do). Mỗi lựa chọn và
   lý do được in ra trước khi điền.
5. Điền bằng script driver của ATS: Greenhouse, Workday, Rippling, Ashby, Lever và
   Workable (job Ashby, Lever và Workable chưa được lấy về). Workday đăng nhập bằng tài khoản lưu cho site Workday đó trong
   `secrets.json`, tạo tài khoản khi đăng nhập thất bại, và gõ mã xác minh đọc từ
   tab Gmail. Hiện chưa có bước setup nào lưu tài khoản đó; không có tài khoản thì
   tab Workday được giữ để bạn tự đăng nhập.
6. Câu hỏi bắt buộc chưa có đáp án đã xác nhận không làm dừng lượt chạy: chúng được
   gom cho cả lượt và kết quả có `questions.pending`. Sau đó engine hỏi tất cả trong
   một popup native (cùng một câu từ nhiều job chỉ hỏi một lần), lưu đáp án vào
   `user/answer-bank.json` và điền vào các tab đang giữ. Đáp án trong bank chỉ được
   dùng lại cho cùng chữ câu hỏi, lựa chọn, quốc gia và loại job; chỉ câu hỏi về
   riêng bạn (học vấn, kỹ năng, ngôn ngữ, câu lạc bộ, dự án) mới được dùng cho công
   ty khác. Câu về quyền làm việc, bảo lãnh, đồng ý, EEO, lương và câu riêng của
   công ty không bao giờ được suy ra. Một lần điền lại chỉ tính khi tab, đọc lại
   mới, hiện đúng đáp án của bạn; nếu không thì `questions --refill` thử lại.
7. Không bao giờ Submit. Bạn tự kiểm từng tab đang giữ và tự nộp.
8. Job trên ATS chưa có driver (iCIMS, SmartRecruiters, Taleo, ...) không bao giờ
   được mở: chúng vào danh sách tự nộp kèm link và resume gợi ý. CAPTCHA, đăng nhập
   hoặc control chưa hỗ trợ giữ tab; xử lý ngay trong tab đó rồi chạy `apply ID`
   cho job đó.
9. Bộ chặn quota: trước mỗi job, một lệnh nhỏ đọc mức dùng 5 giờ và tuần của
   engine (cứ ba job một lần, hoặc mỗi job khi cách ngưỡng dưới 10 điểm). Khi một
   trong hai đạt phần trăm dừng đã chọn ở setup, hoặc engine báo hết hạn mức, lượt
   chạy dừng: tab đang giữ vẫn mở, job chưa bắt đầu giữ trạng thái chờ cho lượt
   sau, cuối bảng ghi lý do và số job còn lại (`quota` và `left` trong JSON). Không
   biết mức dùng thì không bao giờ dừng.
10. Cuối lượt, stderr in một bảng, mỗi job một dòng (công ty, role, resume, tab,
   field cần kiểm), rồi mail xác nhận tìm thấy trong Gmail, job bị bỏ kèm lý do,
   danh sách tự nộp và các câu hỏi chưa có đáp án. Stdout cuối vẫn là JSON.

```text
python autoapply-local.py questions --ui claude
python autoapply-local.py questions --answer user/question-answers.json
python autoapply-local.py questions --refill
python autoapply-local.py status
python autoapply-local.py review all
python autoapply-local.py submitted greenhouse:acme:4012345
python autoapply-local.py track
python autoapply-local.py apply greenhouse:acme:4012345
```

Một popup chứa số câu mà công cụ của engine nhận một lần (Claude 4, Codex 3); phần
còn lại chờ popup kế tiếp và `deferred` cho biết còn bao nhiêu. Bỏ qua, hết giờ hoặc
không có công cụ native thì câu hỏi vẫn chờ. Tab đang hiện đơn khác không bao giờ
bị ghi.

Khi chạm trần tab đang giữ, hãy tự nộp các form đang giữ và ghi lại bằng
`submitted ID,ID` (hoặc để `track` tự tìm) trước khi mở thêm. Không đóng form chưa
nộp. ID job ghi rõ thì không bị lọc trùng. Lỗi phân tích trước thao tác browser trả
job vừa claim về `pending` để thử lại thủ công, còn tab đang giữ vẫn mở.

## Cập nhật

```text
python autoapply-local.py update
```

`update` chép thư mục runtime `<repo>/user/` (trừ `backups/`, profile Chrome và
`secrets.json`) vào `<repo>/user/backups/<ngày-giờ>/`, rồi chạy `git pull --ff-only`
(không có git thì tải zip của bản phát hành mới nhất, không bao giờ ghi vào `user/`,
`.venv/` hay `.git/`), cài lại bằng `pip install -e .`, chạy mỗi migration dữ liệu
còn thiếu đúng một lần và in những gì đã đổi. `apply` in một dòng ra stderr khi có
bản mới; đặt `AUTOAPPLY_LOCAL_NO_UPDATE_CHECK=1` để bỏ bước kiểm này.

## Dữ liệu của bạn

Mọi thứ bạn thêm hoặc tool ghi lại đều nằm trong runtime root, mặc định là
`<repo>/user/`, được Git bỏ qua và bị loại khỏi artifact phát hành. Đường dẫn dưới
đây tính từ root đó:

- `config.json`, `profile.json`, `answers.json`: cấu hình, fact đã xác nhận và câu
  trả lời đã xác nhận.
- `resumes/`: các resume PDF bạn thêm trong setup terminal.
- `user/resume/`: thư mục để resume PDF và file nguồn resume (TEX, DOCX).
- `user/additional-document/`: thư mục để bảng điểm, chứng chỉ, cover letter và
  tài liệu bổ sung khác.
- `user/answer-bank.json`, `user/resume-choices.json`: câu trả lời bạn đã đưa cho
  câu hỏi trong form và lựa chọn resume theo role.
- `tracker.xlsx` và `ledger.json`: tracker (lịch sử chuẩn) và trạng thái job.
- `secrets.json`: email và mật khẩu tài khoản Workday cho từng site Workday. File
  không bao giờ được in hay ghi log, không được `update` backup, và audit phát hành
  thất bại nếu Git theo dõi nó.
- `chrome/`: profile Chrome sạch riêng của tool; không bao giờ dùng profile thường
  ngày của bạn.
- `backups/`: bản sao do `update` tạo.

Hai thư mục để tài liệu được setup tạo kèm một README ngắn (chỉ tạo khi chưa có;
tài liệu và README bạn đã sửa được giữ nguyên). Bỏ file vào đó chỉ để lưu: không tự
chọn resume, trích hay xác nhận fact, hay upload gì. Resume mà `apply` dùng là các
file thêm trong setup terminal; tài liệu bổ sung không bao giờ tự upload.

Không có lệnh Gmail và không có OAuth. Truy cập Gmail duy nhất là tab Gmail trong
profile Chrome riêng của tool, nơi bạn tự đăng nhập: tool tìm trong đó và đọc các
dòng kết quả (người gửi, tiêu đề, đoạn trích, ngày). Tool không bao giờ mở, gửi,
soạn nháp, lưu trữ hay gắn nhãn mail, bỏ qua mail xác minh, và chỉ lưu tiêu đề mail
xác nhận, công ty, role và ngày. `history import PATH` xem preview lịch sử ứng tuyển
CSV/XLSX; `--confirm` nhập có chống trùng. Giữ backup tracker và ledger.

## Lệnh

Từ runtime root, chạy `python autoapply-local.py LỆNH`. Slash command cục bộ và
skill Codex dùng cùng launcher đó. Không lệnh nào bấm Submit.

- `setup`: tạo hoặc làm mới runtime (command, skill, launcher, thư mục tài liệu,
  cấu hình browser) mà không thay file của bạn. `setup --terminal [--no-launch]` là
  phần 1; `setup --guided`, `--answer PATH`, `--finish`, `--confirm` và `--restart`
  kèm `--ui UI` là phần 2 (mỗi lần một hành động); `--edit QUESTION_ID` mở lại một câu.
- `boards list|groups`, `boards group NAME on|off`, `boards import PATH
  [--confirm]`: xem, bật/tắt và thêm nguồn job.
- `demo`: điền form demo offline trong Chrome của tool.
- `profile update PATH [--confirm]`: xem preview rồi nhập intake nâng cao.
- `source extract PATH`: text nháp từ PDF/TEX/DOCX, lưu ở `user/drafts`.
- `history import PATH [--confirm]`: xem preview rồi nhập lịch sử CSV/XLSX.
- `doctor [ENGINE]`: kiểm executable và dependency; đăng nhập và connector vẫn là
  `not_verified`.
- `fetch [ENGINE] [--url URL]`: fetch các board đang bật, hoặc một URL job hỗ trợ
  (chỉ Greenhouse, Workday và Rippling).
- `apply [ENGINE] [N|ID,ID]`: một lượt như mô tả ở
  [Dùng hằng ngày](#dùng-hằng-ngày-apply); `--quick N` vẫn dùng được và
  `--approve-browser-tools` duyệt công cụ browser chỉ cho lệnh này.
- `review ID,ID|all`: đọc lại form đang giữ và kiểm job, tab, giá trị, profile và PDF
  còn mới. Lệnh không bao giờ nộp hay đóng form.
- `submitted ID,ID`: ghi lại các form đang giữ mà bạn đã tự nộp; lệnh ghi dòng
  tracker và giải phóng chỗ trong trần tab, không đụng browser.
- `questions --ui UI`, `questions --answer PATH`, `questions --refill`: hỏi, lưu và
  điền lại câu hỏi bắt buộc mà `apply` đang giữ.
- `track`: ghi lại các form đang giữ mà bạn đã tự nộp, từ trang xác nhận của ATS
  trong tab của chúng (Greenhouse, Workday, Rippling, Lever) hoặc mail xác nhận
  trong tab Gmail nêu đúng tên công ty của job (và role, nếu mail có), gửi từ ngày
  giữ form trở đi. Mail chỉ được khớp khi không job nào khác của công ty đó (đang
  giữ, đã điền hoặc vừa được ghi) có thể đã gửi nó; không thì mail được để lại.
  Trang Workday không nêu job nào thì chờ mail. Tab còn ở form được liệt kê là đang
  chờ; tab đã mất mà chưa có bằng chứng được liệt kê kèm URL. Lệnh không bao giờ
  chạy script trong tab job và không đổi gì khi Chrome của tool chưa chạy.
- `status`: xem form đang giữ, số đếm và đơn chưa giải quyết, không cần engine.
- `update`: backup, cập nhật và migrate (xem [Cập nhật](#cập-nhật)).

## Engine và quyền browser

Mỗi task dùng một engine, engine chọn trong setup hoặc ghi trên dòng lệnh; không có
engine dự phòng và không ghim model. Đăng nhập engine theo luồng bình thường của nó.
Setup ghi `.mcp.json` và `mcp-codex.json` cục bộ trong runtime root mà không thay
file có sẵn; mở runtime root trong engine và phê duyệt browser MCP cục bộ như bình
thường. Không gì thay cấu hình toàn cục hay ghi luật cấp quyền, và không bao giờ
vượt qua phê duyệt.

Với `apply`, `review` và `questions`, `--approve-browser-tools` chỉ duyệt các công cụ
browser MCP cục bộ mà từng task yêu cầu, chỉ cho lệnh đó; cờ không được lưu và không
duyệt công cụ Gmail hay analysis. Policy hiện có, policy managed và permission
denials vẫn áp dụng.

```text
python autoapply-local.py doctor
```

Task có công cụ ngoài trên Claude cần CLI từ 2.1.295; bản cũ hơn trả
`unsupported_version`. Codex chạy chỉ đọc, ngoài Git, với override trong phiên tắt
connector kế thừa, shell, browser native và thực thi code; Codex probe inventory MCP
hiệu lực bằng `mcp list --json` trước và chặn khi cấu hình không đọc được hoặc
managed mà không xác minh được. Lỗi được báo là `login_required`, `quota_exhausted`,
`timeout`, `configuration`, `permission_required`, `unsupported_version`,
`invalid_response` hoặc `engine_failure`, không in output nhạy cảm của CLI. Probe tối
đa 10 giây, task tối đa 300 giây; lỗi runtime thoát mã 3, input hoặc phản hồi không
hợp lệ thoát mã 2. Lần smoke trước đây trên Windows với cả hai engine không xác nhận
runtime hiện tại.

Mặc định một lane browser; hai hoặc ba lane cần opt-in rõ trong intake. Trước mỗi job
mới, tool kiểm RAM trống và số tab đang giữ; luật bộ nhớ là heuristic, chưa phải
benchmark máy 8 GB. Mỗi job giữ lane của nó nên các lệnh sau tìm lại đúng tab.

## Kiểm và export

Từ gốc kho mã:

```text
python -m unittest discover -s tests -p "test_*.py" -v
node --test tests/adapters.test.cjs tests/gmail.test.cjs
python scripts/check_release.py
```

Test dùng dữ liệu tổng hợp tạm và mock engine, browser, network. Export nguồn dùng
`export-allowlist.json` kèm quét nội dung, không chỉ dựa vào `.gitignore`; dữ liệu
runtime, credential và output build bị loại. Source test tổng hợp có trong repo public
để CI chạy; wheel và sdist không chứa test. Đây là bản public prerelease thử nghiệm;
xem kết quả CI tại [Actions](https://github.com/tlamDson/autoapply-local/actions).
Workflow khai báo job Windows và macOS với Python 3.11 và 3.12. Job thứ hai,
install-smoke, chạy `install.ps1` (Windows) hoặc `install.sh` (macOS) trên runner sạch,
rồi `scripts/ci_smoke.py`: doctor, setup terminal với câu trả lời định sẵn và một bản
thay thế offline cho CLI engine, profile đã xác nhận của một ứng viên giả, và điền form
demo offline.

`pyproject.toml` là nguồn chuẩn cho tên project, version, dependency và package data
của wheel. `--artifacts DIR` kiểm mọi wheel, sdist và ZIP nguồn cuối trong thư mục
(bộ file, byte đã đóng gói, metadata, hash RECORD của wheel và luật privacy/đường dẫn)
và từ chối artifact cũ; mặc định `--expect wheel,sdist`, `--expect wheel,sdist,zip`
yêu cầu đủ ba loại. `--source-zip DIR` kiểm nguồn, rồi ghi và kiểm một ZIP tái lập
được; lệnh không bao giờ ghi đè artifact. Từ gốc kho mã (thư mục export và thư mục làm
việc phải chưa tồn tại):

```text
python scripts/check_release.py --export ../autoapply-export-new
cd ../autoapply-export-new
python -m pip wheel --no-deps --no-build-isolation --wheel-dir ../autoapply-artifacts-new .
python -m build --sdist --no-isolation --outdir ../autoapply-artifacts-new
python scripts/check_release.py --source-zip ../autoapply-artifacts-new
python scripts/check_release.py --artifacts ../autoapply-artifacts-new --expect wheel,sdist,zip
python scripts/check_install.py --artifacts ../autoapply-artifacts-new --work-dir "../install space ünïcode new"
```

Các lệnh build cần build requirements đã ghim và package `build`. `check_install.py`
cài wheel và sdist vào hai venv mới từ ngoài cây nguồn; thêm `--wheelhouse PATH` khi
có wheelhouse dependency offline. Đây là quy trình kiểm, chưa phải kết quả đã hoàn
tất cho artifact hiện tại.

License MIT, ghi nguồn tại NOTICE.
