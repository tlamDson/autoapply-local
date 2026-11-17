"""Typed bilingual questions for the complete, explicitly answered intake.

This module is data only: it neither reads resumes nor establishes facts.
Stable identifiers and dotted fields are backend bindings, never UI prompts.
"""

import copy

from .intake import BOOL_FIELDS, FIELDS, INT_FIELDS, LIST_FIELDS


GROUP_LABELS = {
    "runtime": ("setup preferences", "tùy chọn thiết lập"),
    "contact": ("contact details", "thông tin liên hệ"),
    "resumes": ("resume selection", "lựa chọn resume"),
    "education": ("education", "học tập"),
    "targets": ("job preferences", "mục tiêu công việc"),
    "logistics": ("availability and location", "thời gian và địa điểm"),
    "authorization": ("work authorization", "quyền làm việc"),
    "international_student": ("student and immigration information", "thông tin sinh viên và nhập cư"),
    "filters": ("job filtering preferences", "tùy chọn lọc công việc"),
    "experience": ("experience and evidence", "kinh nghiệm và bằng chứng"),
    "compensation": ("pay and voluntary information", "lương và thông tin tự nguyện"),
    "history": ("application history", "lịch sử ứng tuyển"),
    "tailor": ("resume tailoring preferences", "tùy chọn tailor resume"),
    "resources": ("machine resources and Gmail consent", "tài nguyên máy và quyền Gmail"),
}

# Labels describe the actual user choice rather than exposing schema paths.
LABELS = {
    "available_engines": ("installed engines you permit", "các engine đã cài và cho phép dùng"),
    "apply_engine": ("engine to use for applications", "engine dùng để ứng tuyển"),
    "tailor_engine": ("engine to use for tailoring", "engine dùng để tailor resume"),
    "mode": ("workflow mode", "chế độ làm việc"),
    "language": ("preferred interface language", "ngôn ngữ giao diện"),
    "legal_name": ("full legal name", "họ tên pháp lý đầy đủ"),
    "first_name": ("given or first name, as you confirm it", "tên riêng do bạn xác nhận"),
    "last_name": ("family or last name, as you confirm it", "họ do bạn xác nhận"),
    "preferred_name": ("preferred name", "tên thường dùng"),
    "application_email": ("email for applications", "email ứng tuyển"),
    "gmail_email": ("optional Gmail address", "địa chỉ Gmail tùy chọn"),
    "phone": ("phone number", "số điện thoại"),
    "phone_country_code": ("phone country calling code", "mã điện thoại quốc gia"),
    "city": ("city", "thành phố"), "state": ("state or province", "bang hoặc tỉnh"),
    "country": ("country", "quốc gia"), "postcode": ("postal code", "mã bưu chính"),
    "street": ("optional street address", "địa chỉ đường phố tùy chọn"),
    "linkedin": ("your LinkedIn URL", "đường dẫn LinkedIn của bạn"),
    "github": ("your GitHub URL", "đường dẫn GitHub của bạn"),
    "portfolio": ("your portfolio URL", "đường dẫn portfolio của bạn"),
    "main_pdf": ("existing main resume PDF path", "đường dẫn PDF resume chính hiện có"),
    "alternatives": ("alternative resume PDFs", "các PDF resume thay thế"),
    "role_mapping": ("explicit role to resume mappings", "ánh xạ role sang resume do bạn chọn"),
    "newest": ("resume you explicitly identify as newest", "resume mới nhất do chính bạn chọn"),
    "master_source": ("master resume source file path", "đường dẫn nguồn resume Master"),
    "withdrawn_claims": ("claims you have withdrawn", "những thông tin bạn đã rút lại"),
    "superseded_facts": ("facts replaced by newer information", "những thông tin đã được thay thế"),
    "records": ("individual records to add or revisit", "các mục riêng để thêm hoặc xem lại"),
    "current_program": ("current education program", "chương trình đang theo học"),
    "return_after_internship": ("whether you will return to school after an internship", "việc trở lại trường sau kỳ thực tập"),
    "class_standing_now": ("current class standing", "năm học hiện tại"),
    "class_standing_at_internship": ("class standing when the internship starts", "năm học lúc bắt đầu thực tập"),
    "gpa": ("GPA", "điểm GPA"), "gpa_scale": ("GPA scale", "thang điểm GPA"),
    "major_gpa": ("major GPA", "GPA chuyên ngành"),
    "display_gpa": ("permission to show your GPA", "quyền hiển thị GPA"),
    "transcript_available": ("whether a transcript is available", "việc có bảng điểm"),
    "job_types": ("target job types", "các loại công việc mong muốn"),
    "job_type": ("job type for this authorization record", "loại công việc của mục quyền làm việc này"),
    "roles": ("one or more target roles", "một hoặc nhiều role mong muốn"),
    "include_roles": ("additional roles to include", "các role bổ sung muốn giữ"),
    "exclude_roles": ("roles to exclude", "các role muốn loại trừ"),
    "include_stacks": ("technology stacks to include", "các công nghệ muốn tìm"),
    "exclude_stacks": ("technology stacks to exclude", "các công nghệ muốn loại trừ"),
    "adjacent_roles": ("adjacent roles you would consider", "các role gần bạn muốn cân nhắc"),
    "seniority": ("target seniority", "cấp bậc mong muốn"),
    "countries": ("countries where you want to work", "các quốc gia muốn làm việc"),
    "season": ("recruiting season", "kỳ tuyển dụng"), "year": ("recruiting year", "năm tuyển dụng"),
    "window": ("preferred date window", "khoảng thời gian mong muốn"),
    "earliest_start": ("earliest possible start date", "ngày có thể bắt đầu sớm nhất"),
    "latest_end": ("latest possible end date", "ngày có thể kết thúc muộn nhất"),
    "hours_per_week": ("available working hours per week", "số giờ có thể làm mỗi tuần"),
    "weeks": ("number of weeks available", "số tuần có thể làm"),
    "term_time_availability": ("whether you can work during the school term", "việc có thể làm trong kỳ học"),
    "preferred_locations": ("preferred work locations", "các địa điểm làm việc mong muốn"),
    "excluded_locations": ("locations to exclude", "các địa điểm muốn loại trừ"),
    "work_modes": ("acceptable work arrangements", "các hình thức làm việc chấp nhận"),
    "relocation": ("relocation preference and conditions", "mong muốn và điều kiện chuyển nơi ở"),
    "travel": ("whether you agree to work travel", "việc đồng ý đi công tác"),
    "housing": ("housing requirements", "nhu cầu nhà ở"),
    "relocation_support": ("whether you need relocation support", "việc cần hỗ trợ chuyển nơi ở"),
    "current_status": ("current status you explicitly report", "tình trạng hiện tại do chính bạn cung cấp"),
    "authorized_now": ("whether you are authorized to work now in this scope", "quyền làm việc hiện tại trong phạm vi này"),
    "authorized_at_start": ("whether you will be authorized at the job start date in this scope", "quyền làm việc lúc bắt đầu công việc trong phạm vi này"),
    "sponsorship_now": ("whether employer sponsorship is required now in this scope", "việc cần công ty bảo lãnh hiện tại trong phạm vi này"),
    "sponsorship_future": ("whether employer sponsorship is required in the future in this scope", "việc cần công ty bảo lãnh trong tương lai trong phạm vi này"),
    "restrictions": ("work authorization restrictions", "các giới hạn quyền làm việc"),
    "status": ("status you explicitly report", "tình trạng do chính bạn cung cấp"),
    "intended_mechanism": ("intended work authorization mechanism", "cơ chế quyền làm việc dự định dùng"),
    "mechanism": ("work authorization mechanism for this scope", "cơ chế quyền làm việc trong phạm vi này"),
    "cpt": ("CPT status, dates and support separately", "tình trạng, ngày và hỗ trợ CPT riêng biệt"),
    "opt": ("OPT status, dates and support separately", "tình trạng, ngày và hỗ trợ OPT riêng biệt"),
    "stem_opt": ("STEM OPT status, dates and support separately", "tình trạng, ngày và hỗ trợ STEM OPT riêng biệt"),
    "visa_expiry": ("visa expiration date", "ngày hết hạn visa"),
    "program_end": ("education program end date", "ngày kết thúc chương trình học"),
    "work_auth_expiry": ("actual work authorization expiration date", "ngày hết hạn quyền làm việc thực tế"),
    "school_support": ("confirmed school support", "hỗ trợ từ trường đã xác nhận"),
    "dso_support": ("confirmed DSO support", "hỗ trợ từ DSO đã xác nhận"),
    "employer_support": ("confirmed employer support", "hỗ trợ từ công ty đã xác nhận"),
    "unknown_conditions": ("conditions that remain unknown", "các điều kiện còn chưa rõ"),
    "eligibility": ("handling of jobs with unknown eligibility", "cách xử lý job chưa rõ điều kiện"),
    "no_sponsor_conflict": ("handling of sponsorship filter conflicts", "cách xử lý xung đột bộ lọc sponsorship"),
    "priority_employers": ("priority employers", "các công ty ưu tiên"),
    "excluded_employers": ("employers to exclude", "các công ty muốn loại trừ"),
    "include_big_tech": ("whether to include big tech employers", "việc giữ các công ty big tech"),
    "current": ("current experience record", "mục kinh nghiệm hiện tại"),
    "most_recent": ("most recent experience record", "mục kinh nghiệm gần nhất"),
    "unlisted_projects": ("additional projects you actually worked on", "các dự án khác bạn thực sự đã làm"),
    "skills": ("skills and their evidence", "các kỹ năng cùng bằng chứng"),
    "metrics": ("reported metrics with precise or estimated qualifiers", "số liệu do bạn cung cấp cùng mức chính xác hoặc ước lượng"),
    "certifications": ("certifications you hold", "các chứng chỉ bạn có"),
    "languages": ("languages you can use", "các ngôn ngữ bạn sử dụng"),
    "minimum": ("minimum acceptable pay", "mức lương tối thiểu chấp nhận"),
    "maximum": ("maximum of the preferred pay range", "mức cao nhất của khoảng lương mong muốn"),
    "range": ("preferred pay range", "khoảng lương mong muốn"),
    "currency": ("pay currency", "loại tiền lương"), "period": ("pay period", "kỳ tính lương"),
    "unpaid_consent": ("explicit consent to unpaid work", "sự đồng ý rõ ràng làm không lương"),
    "pronouns": ("optional pronouns", "đại từ tự nguyện"),
    "eeo": ("voluntary EEO answers, or decline to provide them", "câu trả lời EEO tự nguyện hoặc từ chối cung cấp"),
    "decline_preferences": ("voluntary questions you prefer to decline", "các câu tự nguyện bạn muốn từ chối"),
    "import_files": ("history files to select for a separate import", "các file lịch sử chọn để nhập trong bước riêng"),
    "alternate_emails": ("alternate application email addresses", "các địa chỉ email ứng tuyển khác"),
    "avoid_employers": ("employers to avoid reapplying to", "các công ty muốn tránh nộp lại"),
    "enabled": ("whether to enable optional resume tailoring", "việc bật tailor resume tùy chọn"),
    "scope": ("employers eligible for resume tailoring", "phạm vi công ty được tailor resume"),
    "employers": ("explicit employer list for tailoring", "danh sách công ty cụ thể để tailor"),
    "fact_sources": ("files providing facts for tailoring", "các file nguồn thông tin để tailor"),
    "pages": ("resume page count", "số trang resume"),
    "section_order": ("resume section order", "thứ tự các mục resume"),
    "summary": ("preferred resume summary", "phần giới thiệu resume mong muốn"),
    "honors": ("honors to show on the resume", "các danh hiệu muốn hiển thị trên resume"),
    "required_entries": ("resume entries that must remain", "các mục resume bắt buộc giữ"),
    "excluded_entries": ("resume entries to exclude", "các mục resume muốn bỏ"),
    "required_bullets": ("resume bullets that must remain", "các bullet resume bắt buộc giữ"),
    "excluded_bullets": ("resume bullets to exclude", "các bullet resume muốn bỏ"),
    "allow_rewrite": ("permission to rewrite confirmed bullets", "quyền viết lại bullet đã xác nhận"),
    "allow_reselection": ("permission to reselect confirmed entries", "quyền chọn lại các mục đã xác nhận"),
    "missing_facts": ("handling of missing resume facts", "cách xử lý thông tin resume còn thiếu"),
    "filename": ("preferred resume output filename", "tên file resume đầu ra mong muốn"),
    "gmail_opt_in": ("explicit opt-in to native Gmail access", "sự cho phép rõ ràng truy cập Gmail native"),
    "gmail_account": ("exact Gmail account to authorize", "đúng tài khoản Gmail muốn cho phép"),
    "gmail_lookback": ("number of days of Gmail history to consider", "số ngày lịch sử Gmail muốn xem xét"),
    "gmail_permissions": ("separate Gmail search and read permissions", "các quyền tìm kiếm và đọc Gmail riêng biệt"),
    "lanes": ("explicitly permitted number of browser lanes", "số lane trình duyệt bạn chủ động cho phép"),
    "chrome_override": ("optional local Chrome executable path", "đường dẫn Chrome cục bộ tùy chọn"),
    "boards": ("public job boards to use", "các board tuyển dụng công khai muốn dùng"),
    "school": ("school name", "tên trường"), "degree": ("degree or qualification", "bằng cấp hoặc chứng nhận"),
    "major": ("field of study", "ngành học"), "start": ("start date", "ngày bắt đầu"),
    "end": ("end date", "ngày kết thúc"),
    "graduation": ("graduation date, precision and expected or actual status", "ngày tốt nghiệp, độ chính xác và dự kiến hay thực tế"),
    "value": ("value for this entry", "giá trị của mục này"),
    "precision": ("date precision", "độ chính xác của ngày"),
    "kind": ("kind of this information", "loại thông tin này"),
    "employer": ("employer name", "tên công ty"),
    "effective_at": ("date when this scoped information applies", "ngày thông tin trong phạm vi này có hiệu lực"),
    "title": ("actual position title", "chức danh thực tế"),
    "location": ("location", "địa điểm"),
    "description": ("work you actually performed", "công việc bạn thực sự đã làm"),
    "name": ("name of this entry", "tên mục này"),
    "contribution": ("your own contribution", "phần đóng góp của chính bạn"),
    "evidence": ("source of skill evidence", "nguồn bằng chứng kỹ năng"),
    "source_id": ("reference identifying the supporting source", "mã tham chiếu nguồn bằng chứng"),
    "role": ("role matched by this resume", "role ứng với resume này"),
    "path": ("existing local file path", "đường dẫn file cục bộ hiện có"),
    "category": ("voluntary question category", "nhóm câu hỏi tự nguyện"),
    "ats": ("job board platform", "nền tảng tuyển dụng"),
    "company": ("company name", "tên công ty"),
    "board": ("public board identifier", "mã board tuyển dụng công khai"),
    "host": ("public Workday host name", "tên host Workday công khai"),
    "tenant": ("public Workday tenant", "tenant Workday công khai"),
    "site": ("public Workday career site", "site tuyển dụng Workday công khai"),
    "endpoint": ("optional supported public HTTPS endpoint", "địa chỉ HTTPS công khai được hỗ trợ nếu cần"),
    "search": ("optional job search phrase", "cụm từ tìm công việc tùy chọn"),
    "valid_from": ("confirmed validity start date", "ngày bắt đầu hiệu lực đã xác nhận"),
    "valid_to": ("confirmed validity end date", "ngày kết thúc hiệu lực đã xác nhận"),
}

ROLES = ("SWE", "backend", "frontend", "fullstack", "mobile", "platform", "SRE", "DevOps", "data", "AI", "ML", "research", "QA", "SDET", "security", "custom")
JOB_TYPES = ("internship", "co-op", "newgrad", "experienced")
MECHANISM_STATUSES = ("not_applied", "planned", "pending", "granted")

ENUMS = {
    "runtime.mode": ("quick", "tailor", "both"),
    "runtime.language": ("en", "vi"),
    "runtime.apply_engine": ("codex", "claude"),
    "runtime.tailor_engine": ("codex", "claude"),
    "runtime.available_engines": ("codex", "claude"),
    "targets.roles": ROLES,
    "targets.job_types": JOB_TYPES,
    "targets.seniority": ("entry", "mid", "senior", "any"),
    "logistics.season": ("spring", "summer", "fall", "winter", "year_round"),
    "logistics.work_modes": ("remote", "hybrid", "onsite"),
    "logistics.relocation": ("yes", "no", "case_by_case"),
    "filters.eligibility": ("broad_unknown", "explicit_only"),
    "filters.no_sponsor_conflict": ("review", "exclude"),
    "education.records.status": ("current", "completed", "leave"),
    "education.records.graduation.precision": ("year", "month", "day"),
    "education.records.graduation.kind": ("expected", "actual"),
    "authorization.records.job_type": (*JOB_TYPES, "fulltime"),
    "authorization.records.mechanism": ("CPT", "OPT", "STEM_OPT", "other"),
    "international_student.intended_mechanism": ("CPT", "OPT", "STEM_OPT", "other"),
    "experience.skills.evidence": ("work", "project", "course", "learning"),
    "experience.metrics.kind": ("precise", "estimated"),
    "experience.records.metrics.kind": ("precise", "estimated"),
    "compensation.currency": ("USD", "CAD", "EUR", "GBP", "other"),
    "compensation.period": ("hour", "month", "year", "other"),
    "tailor.scope": ("all", "priority", "bigtech"),
    "tailor.pages": (1, 2),
    "tailor.missing_facts": ("ask", "omit"),
    "resources.lanes": (1, 2, 3),
    "resources.gmail_permissions": ("search", "read", "current_job_verification"),
    "resources.boards.ats": ("greenhouse", "workday", "rippling"),
}

# Each editor's children are real typed questions, not instructions to enter JSON.
STRUCTURES = {
    "education.records": ("record", "school degree major status start end graduation"),
    "education.records.graduation": ("object", "value precision kind"),
    "authorization.records": ("record", "country job_type employer effective_at current_status authorized_now authorized_at_start sponsorship_now sponsorship_future restrictions mechanism"),
    "experience.records": ("record", "employer title start end location description metrics"),
    "experience.unlisted_projects": ("record", "name description contribution"),
    "experience.skills": ("record", "name evidence source_id"),
    "experience.metrics": ("record", "value kind"),
    "experience.records.metrics": ("record", "value kind"),
    "resumes.role_mapping": ("object", "role path"),
    "compensation.eeo": ("object", "category value"),
    "compensation.range": ("object", "minimum maximum"),
    "resources.boards": ("record", "ats company board host tenant site endpoint search"),
}
for _mechanism in ("cpt", "opt", "stem_opt"):
    STRUCTURES[f"international_student.{_mechanism}"] = (
        "object", "status valid_from valid_to school_support dso_support employer_support",
    )
    ENUMS[f"international_student.{_mechanism}.status"] = MECHANISM_STATUSES

CHOICE_LABELS = {
    True: ("Yes", "Có"), False: ("No", "Không"),
    "en": ("English", "Tiếng Anh"), "vi": ("Vietnamese", "Tiếng Việt"),
    "quick": ("Use existing resumes", "Dùng resume hiện có"),
    "tailor": ("Tailor resumes separately", "Tailor resume riêng"),
    "both": ("Existing resumes and optional tailoring", "Resume hiện có và tailor tùy chọn"),
    "internship": ("Internship", "Thực tập"), "co-op": ("Co-op", "Co-op"),
    "newgrad": ("New graduate", "Mới tốt nghiệp"), "experienced": ("Experienced roles", "Việc cho người có kinh nghiệm"),
    "fulltime": ("Full-time", "Toàn thời gian"),
    "SWE": ("Software engineering", "Kỹ thuật phần mềm"),
    "backend": ("Backend engineering", "Kỹ thuật backend"),
    "frontend": ("Frontend engineering", "Kỹ thuật frontend"),
    "fullstack": ("Full-stack engineering", "Kỹ thuật full-stack"),
    "mobile": ("Mobile engineering", "Kỹ thuật ứng dụng di động"),
    "platform": ("Platform engineering", "Kỹ thuật nền tảng"),
    "SRE": ("Site reliability engineering", "Kỹ thuật độ tin cậy hệ thống"),
    "DevOps": ("DevOps engineering", "Kỹ thuật DevOps"),
    "data": ("Data engineering", "Kỹ thuật dữ liệu"),
    "AI": ("Artificial intelligence", "Trí tuệ nhân tạo"),
    "ML": ("Machine learning", "Học máy"), "research": ("Research", "Nghiên cứu"),
    "QA": ("Quality assurance", "Đảm bảo chất lượng"),
    "SDET": ("Software development in test", "Phát triển phần mềm kiểm thử"),
    "security": ("Security engineering", "Kỹ thuật bảo mật"),
    "custom": ("Enter a custom role using the text field", "Nhập role riêng bằng ô văn bản"),
    "broad_unknown": ("Keep jobs with unknown eligibility for consideration", "Giữ job chưa rõ điều kiện để cân nhắc"),
    "explicit_only": ("Require explicit eligibility evidence", "Chỉ giữ khi có bằng chứng rõ về điều kiện"),
    "review": ("Review conflicts manually", "Kiểm tra xung đột thủ công"),
    "exclude": ("Exclude conflicting jobs", "Loại job có xung đột"),
    "current": ("Currently enrolled", "Đang theo học"),
    "completed": ("Completed", "Đã hoàn thành"), "leave": ("On leave", "Đang bảo lưu"),
    "year": ("Year", "Năm"), "month": ("Month", "Tháng"), "day": ("Day", "Ngày"),
    "expected": ("Expected", "Dự kiến"), "actual": ("Actual", "Thực tế"),
    "not_applied": ("Not applied", "Chưa nộp"), "planned": ("Planned", "Dự định"),
    "pending": ("Pending", "Đang chờ"), "granted": ("Granted", "Đã được cấp"),
    "work": ("Work experience", "Kinh nghiệm làm việc"),
    "project": ("Project", "Dự án"), "course": ("Coursework", "Môn học"), "learning": ("Learning", "Đang học"),
    "precise": ("Precise measurement", "Đo chính xác"), "estimated": ("Estimate", "Ước lượng"),
    "all": ("All employers", "Mọi công ty"), "priority": ("Priority employers", "Công ty ưu tiên"),
    "bigtech": ("Big tech employers", "Công ty big tech"),
    "ask": ("Ask about missing facts", "Hỏi thông tin còn thiếu"), "omit": ("Omit missing facts", "Bỏ thông tin còn thiếu"),
    "remote": ("Remote", "Từ xa"), "hybrid": ("Hybrid", "Kết hợp"), "onsite": ("On-site", "Tại văn phòng"),
    "yes": ("Willing to relocate", "Sẵn sàng chuyển nơi ở"), "no": ("Do not relocate", "Không chuyển nơi ở"),
    "case_by_case": ("Consider each location separately", "Cân nhắc từng địa điểm"),
    "search": ("Search mail", "Tìm thư"), "read": ("Read selected mail", "Đọc thư được chọn"),
    "current_job_verification": ("Read verification for the current job only", "Đọc xác minh cho job hiện tại"),
}

PATH_FIELDS = {
    "resumes.main_pdf", "resumes.master_source", "resumes.newest", "resumes.role_mapping.path",
    "resumes.alternatives.value", "history.import_files.value", "tailor.fact_sources.value",
    "resources.chrome_override",
}
NUMBER_FIELDS = {"hours_per_week", "minimum", "maximum", "gpa", "gpa_scale", "major_gpa"}
DATE_FIELDS = {"start", "end", "earliest_start", "latest_end", "visa_expiry", "program_end", "work_auth_expiry", "effective_at", "valid_from", "valid_to"}
CUSTOM_ENUMS = {"targets.roles", "targets.seniority", "compensation.currency", "compensation.period", "authorization.records.mechanism", "international_student.intended_mechanism"}


def _choices(field, values):
    result = []
    for index, value in enumerate(values):
        # Python bool/int keys alias each other: integer labels must stay numeric.
        en, vi = CHOICE_LABELS.get(value, (str(value), str(value))) if type(value) is not int else (str(value), str(value))
        result.append({"id": f"{field}:{index}", "value": value, "label": {"en": en, "vi": vi}})
    return result


def _descriptor(field):
    group, name = field.split(".")[0], field.rsplit(".", 1)[-1]
    en, vi = LABELS[name]
    group_en, group_vi = GROUP_LABELS[group]
    question = {
        "id": field,
        "field": field,
        "group": group,
        "kind": "text",
        "value_type": "string",
        "prompt": {"en": f"For {group_en}, what is your {en}?", "vi": f"Về {group_vi}, bạn chọn gì cho {vi}?"},
        "mandatory": field in {"runtime.mode", "runtime.language", "runtime.apply_engine"},
        "allow_unknown": True,
        "allow_skip": True,
        "allow_custom": True,
        "choices": [],
    }
    if name in BOOL_FIELDS:
        question.update(kind="choice", value_type="boolean", choices=_choices(field, (True, False)), allow_custom=False)
    elif name in INT_FIELDS or name in NUMBER_FIELDS:
        question.update(kind="number", value_type="integer" if name in INT_FIELDS else "number")
    elif name in DATE_FIELDS or field == "education.records.graduation.value":
        question.update(kind="date", date_precision="preserve")
    elif field in PATH_FIELDS:
        question.update(kind="path", existing_file=True)
    if field in STRUCTURES:
        kind, children = STRUCTURES[field]
        question.update(kind=kind, value_type="array" if kind == "record" else "object", allow_custom=False,
                        fields=[_descriptor(field + "." + child) for child in children.split()])
        if kind == "record":
            question["repeatable"] = True
    elif name in LIST_FIELDS:
        question.update(kind="list", value_type="array", repeatable=True, allow_custom=False,
                        fields=[_descriptor(field + ".value")], scalar_items=True)
    if field in ENUMS:
        question.update(kind="multi_choice" if name in LIST_FIELDS else "choice",
                        choices=_choices(field, ENUMS[field]), allow_custom=field in CUSTOM_ENUMS)
        question.pop("fields", None)
    if field == "compensation.eeo":
        question.update(allow_decline=True, mapping=True)
    if field == "resumes.role_mapping":
        question["mapping"] = True
    if field == "education.records.graduation.value":
        question["prompt"] = {"en": "What is the expected or actual graduation date, with only the precision you know?", "vi": "Ngày tốt nghiệp dự kiến hoặc thực tế là khi nào, với độ chính xác bạn biết?"}
    if field == "education.records.graduation.precision":
        question["prompt"] = {"en": "How precisely do you know this graduation date?", "vi": "Bạn biết ngày tốt nghiệp này chính xác đến mức nào?"}
    if field == "education.records.graduation.kind":
        question["prompt"] = {"en": "Is this graduation date expected or actual?", "vi": "Ngày tốt nghiệp này là dự kiến hay thực tế?"}
    if field.startswith("international_student.") and name == "status" and field.count(".") == 2:
        mechanism = field.split(".")[1].upper().replace("_", " ")
        question["prompt"] = {"en": f"What is your {mechanism} application or approval status, independently of other mechanisms?", "vi": f"Tình trạng nộp hoặc được cấp {mechanism} của bạn là gì, riêng biệt với cơ chế khác?"}
    return question


def questions():
    """Return fresh descriptors, ordered for the first native runtime batch."""
    ordered = [f"{group}.{name}" for group, names in FIELDS.items() for name in names.split()]
    first = ["runtime.mode", "runtime.language", "runtime.apply_engine"]
    return [_descriptor(field) for field in [*first, *(field for field in ordered if field not in first)]]


def walk(descriptors=None):
    """Yield containers and nested fields in deterministic editor order."""
    for question in questions() if descriptors is None else descriptors:
        yield question
        yield from walk(question.get("fields", []))


def index():
    """Return an independent descriptor lookup by stable question ID."""
    return {question["id"]: copy.deepcopy(question) for question in walk()}
