# Profile intake / Thông tin ứng viên

Edit the single JSON block. null = unknown, never No. All groups stay visible.
Sửa một khối JSON dưới đây. null = chưa biết, không phải Không.
Use raw values or fact envelopes: {value,status,provenance,confirmed_at,scope}.
Statuses: confirmed, unknown, not_applicable, declined. Scope identifiers: country, job_type, employer, question, effective_at. Confirm only facts you supplied.
Education records: id, school, degree, major, status (current/completed/leave), start, end, graduation {value:YYYY-MM,precision:month,kind:expected|actual}. Year precision is allowed.
Experience records: id, employer, title, start, end, location, description, metrics (precise/estimated). Skills need evidence work/project/course/learning and source_id.
Authorization records: id, country, job_type, employer, current_status, authorized_now, authorized_at_start, sponsorship_now, sponsorship_future, restrictions, mechanism.
CPT/OPT/STEM_OPT each: status not_applied/planned/pending/granted, valid_from, valid_to, school_support, dso_support, employer_support; keep visa/program/auth expiry separate.
Câu nhạy cảm, pháp lý, đồng ý, quan hệ công ty và tự luận cần xác nhận đúng nguyên văn câu hỏi.
Targets / Mục tiêu: internship/co-op/newgrad/experienced; SWE/backend/frontend/fullstack/mobile/platform/SRE/DevOps/data/AI/ML/research/QA/SDET/security/custom.
Filters: eligibility broad_unknown|explicit_only; no_sponsor_conflict review|exclude.
Tailor: scope all|priority|bigtech, user employer list; missing_facts ask|omit; pages 1|2.
Gmail is opt-in: permissions search/read/current_job_verification and exact account.
Gmail chỉ đọc khi bạn cho phép và chỉ với tài khoản đã chọn; tối đa 3 lane khi tự chọn.

## Runtime / Engine và ngôn ngữ

- Which installed engines may be used, and which one should apply/tailor use? / Bạn đã cài engine nào, chọn engine nào để apply/tailor? Set available_engines, apply_engine and tailor_engine; no fallback.
- Which mode and language do you prefer? / Bạn chọn chế độ và ngôn ngữ nào? mode: quick|tailor|both; language: en|vi.

## Contact / Thông tin liên hệ

- What are your legal full name, explicitly confirmed given/first name and family/last name? / Họ tên pháp lý, tên và họ do chính bạn xác nhận là gì? Fill legal_name, first_name and last_name separately; never derive their order from the full name.
- What preferred name, application email, optional Gmail email and phone/country code should be used? / Bạn dùng tên gọi, email ứng tuyển, Gmail tùy chọn và số điện thoại/mã quốc gia nào?
- What city, state, country, postcode and optional street address may be shared? / Bạn cho phép dùng thành phố, bang, quốc gia, mã bưu chính và địa chỉ nào?
- Which LinkedIn, GitHub and portfolio URLs are yours? / Link LinkedIn, GitHub và portfolio của bạn là gì? Leave unknown fields null.

## Resumes / Resume và nguồn fact

- Which existing PDF is the main resume, and which alternatives map to target roles? / PDF chính và các bản thay thế theo role là file nào? Set main_pdf, alternatives and role_mapping with local paths.
- Which resume/source is newest, and which claims are withdrawn or superseded? / Resume/nguồn mới nhất là gì, fact nào đã rút lại hoặc thay thế? Set newest, master_source, withdrawn_claims and superseded_facts.

## Education / Học tập

- For each school/program, what degree, major, status and start/end dates are confirmed? / Với từng trường/chương trình, bằng, ngành, trạng thái và ngày bắt đầu/kết thúc nào đã xác nhận? records: id, school, degree, major, status current|completed|leave, start, end.
- When is graduation expected or actual, and how precise is that date? / Ngày tốt nghiệp dự kiến hay thực tế là khi nào, chính xác tới năm hay tháng? graduation: {value:YYYY-MM,precision:month,kind:expected|actual}; year precision is allowed.
- Which program is current, will you return after internship, and what is class standing now/at internship? / Chương trình hiện tại, việc trở lại học và năm học hiện tại/lúc thực tập là gì?
- What GPA/scale and major GPA may be shown, and is a transcript available? / GPA/thang điểm, GPA ngành nào được hiển thị, có bảng điểm không? Set display_gpa explicitly.

## Targets / Role và loại công việc

- Which target roles and job types do you want? / Bạn nhắm role và loại công việc nào? job_types: internship|co-op|newgrad|experienced; roles: SWE|backend|frontend|fullstack|mobile|platform|SRE|DevOps|data|AI|ML|research|QA|SDET|security|custom.
- Which roles/stacks should be included/excluded, are adjacent roles acceptable, and in which countries/seniority? / Bao gồm/loại trừ role hoặc stack nào, có nhận role gần không, tại quốc gia và cấp bậc nào?

## Logistics / Thời gian và địa điểm

- Which season/year, date window, earliest start and latest end apply? / Bạn chọn kỳ/năm, khoảng thời gian, ngày bắt đầu sớm nhất và kết thúc muộn nhất nào?
- How many hours per week/weeks can you work, including term time? / Bạn làm được bao nhiêu giờ mỗi tuần/bao nhiêu tuần, kể cả trong kỳ học?
- Which locations and remote/hybrid/onsite modes are preferred or excluded? / Bạn muốn hoặc loại trừ địa điểm và hình thức remote/hybrid/onsite nào?
- Can you relocate/travel, and what housing or relocation support is needed? / Bạn có thể chuyển nơi ở/đi công tác không, cần hỗ trợ nhà ở hoặc chuyển chỗ nào? Unknown is not consent.

## Authorization / Quyền làm việc và sponsorship

- For each country, employer and job type, are you authorized now and at the job start date? / Với từng quốc gia, công ty và loại job, bạn có quyền làm việc hiện tại và lúc bắt đầu không? Choose Yes|No|Unknown separately; include restrictions and current_status.
- Do you require employer sponsorship now for this job type? / Bạn có cần công ty bảo lãnh hiện tại cho loại job này không? sponsorship_now: true|false|null, separately for internship/co-op/newgrad/fulltime and the exact employer/time.
- Will you require employer sponsorship in the future for this job type? / Bạn có cần công ty bảo lãnh trong tương lai cho loại job này không? sponsorship_future: true|false|null; do not copy the current answer.
- What is the exact form question and your confirmed answer for its scope? / Nguyên văn câu hỏi form và câu trả lời bạn xác nhận trong đúng phạm vi là gì? Add a top-level answers entry with question, value, status, provenance, confirmed_at and scope {country,job_type,employer,question,effective_at}. Preview then --confirm; no hidden profile editing is needed.

## International student / Visa và cơ chế làm việc

- What is your visa/student status, distinct from actual work authorization? / Tình trạng visa/sinh viên của bạn là gì, tách biệt với quyền làm việc thực tế? Visa status alone establishes neither authorization nor sponsorship answers.
- For CPT, OPT and STEM OPT, is each not_applied, planned, pending or granted? / Với CPT, OPT và STEM OPT, từng cơ chế là chưa nộp, dự định, đang chờ hay đã được cấp? Fill cpt/opt/stem_opt.status and intended_mechanism; planned/pending never means granted.
- What are each mechanism's valid dates and confirmed school/DSO/employer support? / Thời hạn và hỗ trợ trường/DSO/công ty đã xác nhận của từng cơ chế là gì? Keep visa_expiry, program_end and work_auth_expiry separate; list unknown_conditions.

## Filters / Chính sách lọc

- Should eligibility filtering retain unknowns or require explicit evidence? / Lọc điều kiện giữ job chưa rõ hay chỉ giữ job có bằng chứng rõ? eligibility: broad_unknown|explicit_only.
- How should the sponsor filter handle conflicts: review or exclude? / Bộ lọc sponsorship xử lý xung đột bằng kiểm thủ công hay loại? no_sponsor_conflict: review|exclude; this preference never supplies immigration answers.
- Which priority/excluded employers and big-tech preference apply? / Bạn ưu tiên/loại trừ công ty nào và có muốn big tech không?

## Experience / Kinh nghiệm và bằng chứng

- For every experience/project, what employer, title, dates, location and work did you actually do? / Với từng kinh nghiệm/dự án, công ty, chức danh, ngày, địa điểm và việc thật sự đã làm là gì? records: id, employer, title, start, end, location, description; current/most_recent and unlisted_projects are separate.
- Which skills, metrics, certifications and languages have evidence? / Skill, số liệu, chứng chỉ và ngôn ngữ nào có bằng chứng? Skills need evidence work|project|course|learning and source_id; mark metrics precise or estimated, never invent numbers.

## Compensation / Lương và lựa chọn cá nhân

- What minimum/range, currency and pay period do you accept, and do you consent to unpaid work? / Mức tối thiểu/khoảng lương, tiền tệ và kỳ trả lương nào chấp nhận, có đồng ý làm không lương không? Unpaid consent must be explicit.
- Which optional pronouns/EEO answers do you want to provide or decline? / Bạn muốn cung cấp hoặc từ chối đại từ/EEO tự nguyện nào? Set eeo and decline_preferences; blank does not mean consent.

## History / Lịch sử ứng tuyển

- Which CSV/XLSX history files, alternate emails and employers to avoid should be considered? / Cần xem file lịch sử CSV/XLSX, email khác và công ty cần tránh nào? Import history separately with preview/confirmation; missing mail is not a failed application.

## Tailor / Quy tắc resume tùy chọn

- Do you want optional tailoring, and for all, priority or bigtech employers? / Bạn có muốn tailor tùy chọn không, cho mọi công ty, công ty ưu tiên hay big tech? enabled: true|false; scope: all|priority|bigtech; employers and fact_sources are explicit lists.
- Do you prefer one/two pages, what section order, summary/GPA/honors and required/excluded entries or bullets? / Bạn chọn một/hai trang, thứ tự mục, summary/GPA/honors và nội dung bắt buộc/loại trừ nào?
- May confirmed bullets be rewritten or reselected, and should missing facts be asked or omitted? / Có cho viết lại/chọn lại bullet đã xác nhận không, thiếu fact thì hỏi hay bỏ? allow_rewrite/allow_reselection; missing_facts: ask|omit; filename controls output naming.

## Resources / Gmail và tài nguyên máy

- Which public boards should fetch use? / Bạn muốn fetch từ board công khai nào? resources.boards: null or absent preserves sources, [] disables them; a list replaces them after preview/confirmation. Defaults are editable discovery seeds, never sponsorship or eligibility evidence.
- Do you opt in to native Gmail, for which exact account and lookback? / Bạn có bật Gmail native không, đúng tài khoản và khoảng ngày nào? gmail_opt_in defaults false; gmail_permissions: search|read|current_job_verification, granted separately; no message reads before account verification.
- Do you explicitly opt in to one, two or three Chrome lanes, and need an executable override? / Bạn chủ động chọn một, hai hay ba lane Chrome, có cần đường dẫn Chrome riêng không? lanes: 1|2|3; more lanes require opt-in and memory/tab admission checks.

```json
{
  "schema_version": 1,
  "groups": {
    "runtime": {
      "available_engines": [],
      "apply_engine": null,
      "tailor_engine": null,
      "mode": "quick",
      "language": "en"
    },
    "contact": {
      "legal_name": null,
      "first_name": null,
      "last_name": null,
      "preferred_name": null,
      "application_email": null,
      "gmail_email": null,
      "phone": null,
      "phone_country_code": null,
      "city": null,
      "state": null,
      "country": null,
      "postcode": null,
      "street": null,
      "linkedin": null,
      "github": null,
      "portfolio": null
    },
    "resumes": {
      "main_pdf": null,
      "alternatives": [],
      "role_mapping": {},
      "newest": null,
      "master_source": null,
      "withdrawn_claims": [],
      "superseded_facts": []
    },
    "education": {
      "records": [],
      "current_program": null,
      "return_after_internship": null,
      "class_standing_now": null,
      "class_standing_at_internship": null,
      "gpa": null,
      "gpa_scale": null,
      "major_gpa": null,
      "display_gpa": null,
      "transcript_available": null
    },
    "targets": {
      "job_types": [],
      "roles": [],
      "include_roles": [],
      "exclude_roles": [],
      "include_stacks": [],
      "exclude_stacks": [],
      "adjacent_roles": [],
      "seniority": null,
      "countries": []
    },
    "logistics": {
      "season": null,
      "year": null,
      "window": null,
      "earliest_start": null,
      "latest_end": null,
      "hours_per_week": null,
      "weeks": null,
      "term_time_availability": null,
      "preferred_locations": [],
      "excluded_locations": [],
      "work_modes": [],
      "relocation": null,
      "travel": null,
      "housing": null,
      "relocation_support": null
    },
    "authorization": {
      "records": [],
      "current_status": null,
      "authorized_now": null,
      "authorized_at_start": null,
      "restrictions": [],
      "sponsorship_now": null,
      "sponsorship_future": null
    },
    "international_student": {
      "status": null,
      "intended_mechanism": null,
      "cpt": {},
      "opt": {},
      "stem_opt": {},
      "visa_expiry": null,
      "program_end": null,
      "work_auth_expiry": null,
      "school_support": null,
      "dso_support": null,
      "employer_support": null,
      "unknown_conditions": []
    },
    "filters": {
      "eligibility": null,
      "no_sponsor_conflict": null,
      "priority_employers": [],
      "excluded_employers": [],
      "include_big_tech": null
    },
    "experience": {
      "records": [],
      "current": null,
      "most_recent": null,
      "unlisted_projects": [],
      "skills": [],
      "metrics": [],
      "certifications": [],
      "languages": []
    },
    "compensation": {
      "minimum": null,
      "range": null,
      "currency": null,
      "period": null,
      "unpaid_consent": null,
      "pronouns": null,
      "eeo": {},
      "decline_preferences": []
    },
    "history": {
      "import_files": [],
      "alternate_emails": [],
      "avoid_employers": []
    },
    "tailor": {
      "enabled": false,
      "scope": null,
      "employers": [],
      "fact_sources": [],
      "pages": 1,
      "section_order": [],
      "summary": null,
      "gpa": null,
      "honors": null,
      "required_entries": [],
      "excluded_entries": [],
      "required_bullets": [],
      "excluded_bullets": [],
      "allow_rewrite": null,
      "allow_reselection": null,
      "missing_facts": "omit",
      "filename": "resume"
    },
    "resources": {
      "gmail_opt_in": false,
      "gmail_account": null,
      "gmail_lookback": 7,
      "gmail_permissions": [],
      "lanes": 1,
      "chrome_override": null,
      "boards": null
    }
  },
  "answers": []
}
```
