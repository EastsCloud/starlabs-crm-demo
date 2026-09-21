"""Field definitions for school profiles; credentials are excluded from exports."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = "text"
    options: tuple = ()
    required: bool = False
    private: bool = False
    limit: int = 200


PROFILE_FIELDS = (
    Field("birth_place", "出生地"), Field("nationality", "国籍", limit=100),
    Field("current_school", "在读学校"), Field("applying_grade", "申请年级", limit=50),
    Field("mother_info", "妈妈信息（名字、电话和邮箱）", "textarea", private=True, limit=3000),
    Field("father_info", "爸爸信息（名字、电话和邮箱）", "textarea", private=True, limit=3000),
    Field("application_email", "学生申请邮箱", "email", private=True),
    Field("parent_application_email", "家长申请邮箱", "email", private=True),
    Field("toefl_account", "TOEFL Junior / TOEFL账户", private=True),
    Field("ssat_parent_account", "SSAT家长账户", private=True),
    Field("ssat_student_account", "SSAT学生账户", private=True),
    Field("vericant_account", "维立克账户", private=True),
    Field("show_and_tell", "Show and Tell", "textarea", limit=10000),
    Field("css_completed_date", "CSS完成时间", "date"),
    Field("css_report_date", "CSS出报告时间", "date"),
    Field("css_submitted", "CSS是否递交", "select", ("未递交", "已递交"), limit=20),
)

BASIC_FIELDS = (Field("birth_date", "出生日期", "date"),) + PROFILE_FIELDS

ACTIVITY_FIELDS = (
    Field("name", "名称", required=True),
    Field("activity_type", "类型", "select", ("艺术", "体育", "公益", "学术"), True, limit=30),
    Field("frequency_duration", "频率与时长", limit=300),
    Field("level", "等级", "select", ("校级", "市级", "国家级", "国际级"), limit=30),
)
INTERVIEW_FIELDS = (
    Field("interview_type", "面试类型", "select", ("维立克预面试", "维立克正式面试", "InitialView"), True, limit=50),
    Field("date", "面试日期", "date"), Field("psee_score", "PSEE成绩", limit=80),
    Field("pwse_score", "PWSE成绩", limit=80), Field("result", "面试结果", "textarea", limit=10000),
)
MEETING_FIELDS = (
    Field("school_name", "学校名字", required=True), Field("date", "日期", "date"),
    Field("format", "形式", "select", ("面试会", "线上面试", "线下面试", "仅访校"), True, limit=30),
    Field("notes", "备注", "textarea", limit=10000),
)
TRAINING_FIELDS = (
    Field("date", "日期", "date"), Field("duration", "时长", limit=80),
    Field("training_type", "类型", "select", ("头脑风暴", "面试练习"), True, limit=30),
    Field("method", "方式", "select", ("线上", "线下"), True, limit=20),
    Field("notes", "备注", "textarea", limit=10000),
)
APPLICATION_FIELDS = (
    Field("program_name", "申请学校", required=True), Field("school_state", "所在州", limit=100),
    Field("applying_grade", "申请年级", limit=50), Field("deadline", "截止日期", "date"),
    Field("application_system", "申请系统", limit=150), Field("application_url", "申请网址", "url", limit=500),
    Field("application_username", "申请账户", private=True, limit=150),
    Field("application_password", "申请密码", "password", private=True, limit=150),
    Field("application_fee", "申请费", limit=80), Field("toefl_code", "TOEFL代码", limit=50),
    Field("toefl_delivery_date", "TOEFL / TOEFL Junior送分日期", "date"),
    Field("toefl_delivery_score", "TOEFL / TOEFL Junior送分分数", limit=80),
    Field("ssat_code", "SSAT代码", limit=50), Field("ssat_delivery_date", "SSAT送分日期", "date"),
    Field("ssat_delivery_score", "SSAT送分分数", limit=80), Field("isee_code", "ISEE代码", limit=50),
    Field("isee_delivery_date", "ISEE送分日期", "date"), Field("isee_delivery_score", "ISEE送分分数", limit=80),
    Field("css_status", "CSS", "textarea", limit=10000),
    Field("school_supplemental_essay", "补充文书", "textarea", limit=10000),
    Field("recommendations", "推荐信", "textarea", limit=10000),
    Field("notes", "申请特殊情况备注", "textarea", limit=10000),
    Field("transcript_resubmit_date", "补交新成绩单时间", "date"),
    Field("result_date", "出结果时间", "date"), Field("portal_url", "查询网址", "url", limit=500),
    Field("portal_id", "查询账户", private=True, limit=150),
    Field("portal_password", "查询密码", "password", private=True, limit=150),
    Field("status", "申请状态", "select", ("WIP", "Submitted", "Accepted", "Conditional", "Waitlisted", "Unsuccessful", "Withdrawn", "Deferred"), True, limit=50),
    Field("result", "申请结果", limit=120),
)

# Used by profiles, the read-only view, and PDF allowlists.
RECORD_FIELDS = {
    "activities": ("活动记录", ACTIVITY_FIELDS),
    "third-party-interviews": ("第三方面试", INTERVIEW_FIELDS),
    "school-meetings": ("学校见面会与面试记录", MEETING_FIELDS),
    "training-records": ("面试&文书培训记录", TRAINING_FIELDS),
    "applications": ("申请信息", APPLICATION_FIELDS),
}
RELATIONS = {"activities": "activities", "third-party-interviews": "third_party_interviews",
             "school-meetings": "school_meetings", "training-records": "training_records", "applications": "applications"}


def parse_fields(form, fields):
    from datetime import date
    from urllib.parse import urlsplit
    result = {}
    for field in fields:
        value = str(form.get(field.name, "")).strip()
        if field.required and not value:
            raise ValueError(f"请填写{field.label}。")
        if len(value) > field.limit:
            raise ValueError(f"{field.label}最多{field.limit}个字符。")
        if field.kind == "select" and value and value not in field.options:
            raise ValueError(f"请选择有效的{field.label}。")
        if field.kind == "date":
            try:
                value = date.fromisoformat(value) if value else None
            except ValueError:
                raise ValueError(f"{field.label}格式不正确。") from None
        if field.kind == "url" and value:
            try:
                url = urlsplit(value)
                valid = url.scheme in ("https", "http") and bool(url.netloc)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(f"{field.label}须为完整的 http 或 https 地址。")
        result[field.name] = value
    return result
