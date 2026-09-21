from datetime import datetime
import secrets

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


def now():
    return datetime.utcnow


class TimestampMixin:
    created_at = Column(DateTime, default=now())
    updated_at = Column(DateTime, default=now(), onupdate=now())


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(120), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(String(20), nullable=False, default="member")
    active = Column(Boolean, nullable=False, default=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)


class RememberedLogin(Base):
    __tablename__ = "remembered_logins"
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    encrypted_password = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=now(), nullable=False)
    username = Column(String(120), nullable=False)
    action = Column(String(300), nullable=False)
    status = Column(Integer, nullable=False)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    key = Column(String(64), primary_key=True)
    count = Column(Integer, nullable=False, default=0)
    until = Column(DateTime, nullable=False)


class Student(Base, TimestampMixin):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False, index=True)
    grade = Column(String(50), default="")
    student_type = Column(String(20), nullable=False, default="college", server_default="college", index=True)
    enrollment_year = Column(String(4), default="", server_default="")
    birth_place = Column(String(200), default="", server_default="")
    nationality = Column(String(100), default="", server_default="")
    current_school = Column(String(200), default="", server_default="")
    applying_grade = Column(String(50), default="", server_default="")
    mother_info = Column(Text, default="", server_default="")
    father_info = Column(Text, default="", server_default="")
    application_email = Column(String(200), default="", server_default="")
    parent_application_email = Column(String(200), default="", server_default="")
    toefl_account = Column(String(200), default="", server_default="")
    ssat_parent_account = Column(String(200), default="", server_default="")
    ssat_student_account = Column(String(200), default="", server_default="")
    vericant_account = Column(String(200), default="", server_default="")
    show_and_tell = Column(Text, default="", server_default="")
    css_completed_date = Column(Date, nullable=True)
    css_report_date = Column(Date, nullable=True)
    css_submitted = Column(String(20), default="", server_default="")
    target_school = Column(String(200), default="")
    target_major = Column(String(200), default="")
    advisor = Column(String(120), default="")
    priority_level = Column(String(20), default="中")
    priority_direction = Column(String(10), default="up")
    overall_status = Column(String(120), default="")
    birth_date = Column(Date, nullable=True)
    phone = Column(String(80), default="")
    address = Column(Text, default="")
    chinese_address = Column(Text, default="")
    national_id_number = Column(String(80), default="")
    passport_number = Column(String(80), default="")
    passport_issue_date = Column(Date, nullable=True)
    passport_expiry_date = Column(Date, nullable=True)
    bank_certificate_amount = Column(String(80), default="")
    credit_card_holder = Column(String(120), default="")
    cardholder_phone = Column(String(80), default="")
    card_channel = Column(String(80), default="")
    card_number = Column(String(100), default="")
    card_expiry = Column(String(30), default="")
    final_school = Column(String(200), default="")
    notes = Column(Text, default="")
    portal_token = Column(String(64), default=lambda: secrets.token_urlsafe(24), unique=True, index=True)

    tasks = relationship("Task", back_populates="student", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="student", cascade="all, delete-orphan")
    applications = relationship("Application", back_populates="student", cascade="all, delete-orphan")
    exams = relationship("Exam", back_populates="student", cascade="all, delete-orphan")
    courses = relationship("Course", back_populates="student", cascade="all, delete-orphan")
    communications = relationship("Communication", back_populates="student", cascade="all, delete-orphan")
    timelines = relationship("Timeline", back_populates="student", cascade="all, delete-orphan")
    activities = relationship("StudentActivity", back_populates="student", cascade="all, delete-orphan", order_by="StudentActivity.id")
    third_party_interviews = relationship("ThirdPartyInterview", back_populates="student", cascade="all, delete-orphan", order_by="ThirdPartyInterview.date, ThirdPartyInterview.id")
    school_meetings = relationship("SchoolMeeting", back_populates="student", cascade="all, delete-orphan", order_by="SchoolMeeting.date, SchoolMeeting.id")
    training_records = relationship("TrainingRecord", back_populates="student", cascade="all, delete-orphan", order_by="TrainingRecord.date, TrainingRecord.id")


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    category = Column(String(50), default="其他")
    status = Column(String(50), default="WIP")
    priority = Column(String(20), default="中")
    priority_direction = Column(String(10), default="up")
    owner = Column(String(50), default="学生")
    due_date = Column(Date, nullable=True)
    time_node = Column(String(50), default="未添加时间信息")
    description = Column(Text, default="")
    reminder_days = Column(String(50), default="7,1")

    student = relationship("Student", back_populates="tasks")


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    project_name = Column(String(200), nullable=False)
    project_type = Column(String(50), default="其他")
    status = Column(String(50), default="未开始")
    start_date = Column(Date, nullable=True)
    start_time_node = Column(String(50), default="未添加时间信息")
    end_date = Column(Date, nullable=True)
    end_time_node = Column(String(50), default="未添加时间信息")
    outcome = Column(String(250), default="")
    risk_level = Column(String(20), default="中")
    notes = Column(Text, default="")

    student = relationship("Student", back_populates="projects")


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    program_name = Column(String(200), nullable=False)
    program_type = Column(String(50), default="其他")
    status = Column(String(50), default="未开始")
    deadline = Column(Date, nullable=True)
    deadline_time_node = Column(String(50), default="未添加时间信息")
    result_date = Column(Date, nullable=True)
    result_time_node = Column(String(50), default="未添加时间信息")
    result = Column(String(120), default="")
    materials_status = Column(String(200), default="")
    next_step = Column(String(250), default="")
    notes = Column(Text, default="")
    country_batch = Column(String(120), default="")
    country = Column(String(100), default="")
    batch = Column(String(80), default="")
    portal_id = Column(String(150), default="")
    portal_password = Column(String(150), default="")
    portal_material_progress = Column(String(120), default="")
    form_status = Column(String(120), default="")
    submission_date = Column(Date, nullable=True)
    supplemental_essay = Column(String(20), default="")
    transcript_required = Column(String(20), default="")
    ceeb_code = Column(String(50), default="")
    language_delivery = Column(String(120), default="")
    sat_delivery = Column(String(120), default="")
    act_code = Column(String(50), default="")
    act_delivery = Column(String(120), default="")
    ap_delivery = Column(String(120), default="")
    other_delivery = Column(String(200), default="")
    delivery_date = Column(Date, nullable=True)
    application_system = Column(String(150), default="")
    application_username = Column(String(150), default="")
    application_password = Column(String(150), default="")
    portal_security_qa = Column(Text, default="")
    portal_url = Column(String(500), default="")
    online_application_status = Column(String(200), default="")
    portal_status = Column(String(200), default="")
    score_delivery_status = Column(String(200), default="")
    portal_q1 = Column(Text, default="")
    portal_a1 = Column(Text, default="")
    portal_q2 = Column(Text, default="")
    portal_a2 = Column(Text, default="")
    portal_q3 = Column(Text, default="")
    portal_a3 = Column(Text, default="")
    portal_q4 = Column(Text, default="")
    portal_a4 = Column(Text, default="")
    portal_q5 = Column(Text, default="")
    portal_a5 = Column(Text, default="")
    online_details_expanded = Column(Boolean, default=False)
    portal_details_expanded = Column(Boolean, default=False)
    delivery_details_expanded = Column(Boolean, default=False)

    school_state = Column(String(100), default="", server_default="")
    applying_grade = Column(String(50), default="", server_default="")
    application_url = Column(String(500), default="", server_default="")
    application_fee = Column(String(80), default="", server_default="")
    toefl_code = Column(String(50), default="", server_default="")
    toefl_delivery_date = Column(Date, nullable=True)
    toefl_delivery_score = Column(String(80), default="", server_default="")
    ssat_code = Column(String(50), default="", server_default="")
    ssat_delivery_date = Column(Date, nullable=True)
    ssat_delivery_score = Column(String(80), default="", server_default="")
    isee_code = Column(String(50), default="", server_default="")
    isee_delivery_date = Column(Date, nullable=True)
    isee_delivery_score = Column(String(80), default="", server_default="")
    css_status = Column(Text, default="", server_default="")
    school_supplemental_essay = Column(Text, default="", server_default="")
    recommendations = Column(Text, default="", server_default="")
    transcript_resubmit_date = Column(Date, nullable=True)

    student = relationship("Student", back_populates="applications")


class Exam(Base):
    __tablename__ = "exams"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    exam_name = Column(String(120), nullable=False, index=True)
    exam_date = Column(Date, nullable=True)
    exam_time_node = Column(String(50), default="未添加时间信息")
    score = Column(String(80), default="")
    target_score = Column(String(80), default="")
    status = Column(String(50), default="未开始")
    next_action = Column(String(250), default="")
    notes = Column(Text, default="")
    reading = Column(String(30), default="")
    listening = Column(String(30), default="")
    speaking = Column(String(30), default="")
    writing = Column(String(30), default="")
    math = Column(String(30), default="")
    science = Column(String(30), default="")
    english = Column(String(30), default="")
    total = Column(String(30), default="")
    appointment_number = Column(String(100), default="")
    record_locator = Column(String(100), default="")
    subject = Column(String(150), default="")
    component_score = Column(String(100), default="")
    language = Column(String(30), default="", server_default="")
    verbal = Column(String(30), default="", server_default="")
    quantitative = Column(String(30), default="", server_default="")
    analytical = Column(String(30), default="", server_default="")

    student = relationship("Student", back_populates="exams")


class Course(Base, TimestampMixin):
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    semester = Column(String(100), default="")
    category = Column(String(120), default="")
    name = Column(String(200), nullable=False)
    level = Column(String(100), default="")
    grade = Column(String(80), default="")
    credits = Column(String(50), default="")

    student = relationship("Student", back_populates="courses")


class Communication(Base):
    __tablename__ = "communications"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=True)
    date_time_node = Column(String(50), default="未添加时间信息")
    contact_person = Column(String(50), default="家长")
    method = Column(String(50), default="微信")
    summary = Column(Text, default="")
    next_follow_up = Column(Date, nullable=True)
    follow_up_time_node = Column(String(50), default="未添加时间信息")
    generated_tasks = Column(Text, default="")
    created_at = Column(DateTime, default=now())

    duration = Column(String(80), default="")
    school_snapshot = Column(String(200), default="")
    grade_snapshot = Column(String(50), default="")
    notes = Column(Text, default="")
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    updated_at = Column(DateTime, default=now(), onupdate=now())
    import_fingerprint = Column(String(64), nullable=True)
    __table_args__ = (UniqueConstraint('student_id', 'import_fingerprint', name='uq_communication_import'),)
    items = relationship("CommunicationItem", cascade="all, delete-orphan", order_by="CommunicationItem.sort_order")

    student = relationship("Student", back_populates="communications")


class Timeline(Base):
    __tablename__ = "timelines"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="SET NULL"), nullable=True, index=True)
    owner = Column(String(50), default="学生")
    period = Column(String(100), default="")
    category = Column(String(50), default="其他")
    content = Column(Text, default="")
    status = Column(String(50), default="未开始")
    date = Column(Date, nullable=True)
    time_node = Column(String(50), default="未添加时间信息")
    notes = Column(Text, default="")
    source_event_id = Column(Integer, ForeignKey("public_events.id", ondelete="SET NULL"), nullable=True, index=True)
    reminder_days = Column(String(50), default="7,1")

    student = relationship("Student", back_populates="timelines")


class ImportantItem(Base, TimestampMixin):
    __tablename__ = "important_items"

    id = Column(Integer, primary_key=True, index=True)
    item_type = Column(String(80), default="其他")
    content = Column(Text, nullable=False)
    participants = Column(String(500), default="")
    date = Column(Date, nullable=True)
    reminder = Column(String(120), default="")


class ReferenceItem(Base):
    __tablename__ = "reference_items"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(50), nullable=False, index=True)
    name = Column(String(250), nullable=False, index=True)
    code = Column(String(50), default="")
    secondary_code = Column(String(50), default="")
    url = Column(String(500), default="")
    sort_order = Column(Integer, default=0)


class DataSource(Base, TimestampMixin):
    __tablename__ = "data_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    category = Column(String(80), default="其他")
    url = Column(String(1000), nullable=False)
    enabled = Column(Boolean, default=True)
    refresh_hours = Column(Integer, default=24)
    last_checked_at = Column(DateTime, nullable=True)
    last_status = Column(String(50), default="未抓取")
    last_error = Column(Text, default="")

    events = relationship("PublicEvent", back_populates="source", cascade="all, delete-orphan")


class PublicEvent(Base, TimestampMixin):
    __tablename__ = "public_events"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(300), nullable=False)
    category = Column(String(80), default="其他")
    event_date = Column(Date, nullable=False, index=True)
    date_kind = Column(String(80), default="重要日期")
    source_url = Column(String(1000), default="")
    source_text = Column(Text, default="")
    review_status = Column(String(50), default="待确认")
    reminder_days = Column(String(50), default="7,1")

    source = relationship("DataSource", back_populates="events")


class CommunicationItem(Base):
    __tablename__ = 'communication_items'
    id = Column(Integer, primary_key=True)
    communication_id = Column(Integer, ForeignKey('communications.id', ondelete='CASCADE'), nullable=False, index=True)
    section = Column(String(30), nullable=False)
    item_name = Column(Text, nullable=False, default='')
    feedback = Column(Text, nullable=False, default='')
    sort_order = Column(Integer, nullable=False, default=0)


class ImportPreview(Base):
    __tablename__ = 'import_previews'
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    student_id = Column(Integer, ForeignKey('students.id', ondelete='CASCADE'), nullable=True)
    kind = Column(String(30), nullable=False)
    encrypted_payload = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)


class ArchiveExport(Base):
    __tablename__ = 'archive_exports'
    id = Column(String(32), primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    sha256 = Column(String(64), nullable=False)
    source_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=now(), nullable=False)


class StudentActivity(Base, TimestampMixin):
    __tablename__ = "student_activities"
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    activity_type = Column(String(30), nullable=False)
    frequency_duration = Column(String(300), default="")
    level = Column(String(30), default="")
    student = relationship("Student", back_populates="activities")


class ThirdPartyInterview(Base, TimestampMixin):
    __tablename__ = "third_party_interviews"
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    interview_type = Column(String(50), nullable=False)
    date = Column(Date, nullable=True)
    psee_score = Column(String(80), default="")
    pwse_score = Column(String(80), default="")
    result = Column(Text, default="")
    student = relationship("Student", back_populates="third_party_interviews")


class SchoolMeeting(Base, TimestampMixin):
    __tablename__ = "school_meetings"
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    school_name = Column(String(200), nullable=False)
    date = Column(Date, nullable=True)
    format = Column(String(30), nullable=False)
    notes = Column(Text, default="")
    student = relationship("Student", back_populates="school_meetings")


class TrainingRecord(Base, TimestampMixin):
    __tablename__ = "training_records"
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=True)
    duration = Column(String(80), default="")
    training_type = Column(String(30), nullable=False)
    method = Column(String(20), nullable=False)
    notes = Column(Text, default="")
    student = relationship("Student", back_populates="training_records")
