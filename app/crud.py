from datetime import date, datetime, timedelta
import re

from sqlalchemy import case, or_
from sqlalchemy.orm import Session

from app import models, schemas


def parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def exam_detail(exam):
    name = (exam.exam_name or "").upper()
    if name == "AP":
        return exam.subject or "-"
    if name == "SAT":
        return exam.record_locator or "-"
    if name == "TOEFL":
        return exam.appointment_number or "-"
    return "-"


def exam_component_scores(exam):
    name = (exam.exam_name or "").upper()
    if name == "TOEFL JUNIOR":
        return " / ".join(f"{label}: {value or '-'}" for label, value in [("Listening", exam.listening), ("Language", exam.language), ("Reading", exam.reading)])
    if name == "SSAT":
        return " / ".join(f"{label}: {value or '-'}" for label, value in [("V", exam.verbal), ("Q", exam.quantitative), ("A", exam.analytical)])
    values = {
        "TOEFL": [exam.reading, exam.listening, exam.speaking, exam.writing],
        "SAT": [exam.reading, exam.math],
        "ACT": [exam.math, exam.science, exam.english, exam.reading, exam.writing],
    }.get(name)
    if not values:
        return "-"
    return " / ".join(value or "-" for value in values)


def school_exam_high_scores(exams):
    """Show the best recorded total without conflating the two TOEFL scales."""
    from decimal import Decimal, InvalidOperation
    best = {}
    for exam in exams:
        if exam.exam_name not in ("TOEFL", "TOEFL Junior", "SSAT"):
            continue
        value = exam.total or exam.score
        try:
            score = Decimal(value or "")
        except InvalidOperation:
            continue
        if not score.is_finite():
            continue
        if exam.exam_name not in best or score > best[exam.exam_name][0]:
            best[exam.exam_name] = (score, value)
    return [(name, best[name][1] if name in best else "-") for name in ("TOEFL Junior", "TOEFL", "SSAT")]


def normalize_application_detail_fields(db):
    changed = False
    for item in db.query(models.Application).all():
        form_status = item.form_status or item.online_application_status or "WIP"
        if form_status not in schemas.APPLICATION_FORM_STATUSES:
            form_status = "WIP"
        progress = item.portal_material_progress or item.portal_status or "0%"
        if progress not in schemas.PORTAL_PROGRESS_OPTIONS:
            match = re.search(r"(?:0|25|50|75|100)%", progress)
            progress = match.group(0) if match else "0%"
        delivery_status = item.score_delivery_status or "WIP"
        if delivery_status not in schemas.SCORE_DELIVERY_STATUSES:
            delivery_status = "已完成" if any([item.language_delivery, item.sat_delivery, item.act_delivery, item.ap_delivery, item.other_delivery, item.delivery_date]) else "WIP"
        normalized = {
            "form_status": form_status, "online_application_status": form_status,
            "portal_material_progress": progress, "portal_status": progress,
            "score_delivery_status": delivery_status,
        }
        for field, value in normalized.items():
            if getattr(item, field) != value:
                setattr(item, field, value)
                changed = True
        if item.portal_security_qa and not any(getattr(item, f"portal_q{i}") or getattr(item, f"portal_a{i}") for i in range(1, 6)):
            pairs = re.findall(r"Q:\s*(.*?)\s*\nA:\s*(.*?)(?=\n\nQ:|\Z)", item.portal_security_qa, flags=re.S)
            for index, (question, answer) in enumerate(pairs[:5], 1):
                setattr(item, f"portal_q{index}", question.strip())
                setattr(item, f"portal_a{index}", answer.strip())
                changed = True
    if changed:
        db.commit()


def grade_number(student):
    if not student or not student.grade:
        return 11
    if "10" in student.grade:
        return 10
    if "12" in student.grade:
        return 12
    return 11


def infer_time_node(student, value, today=None):
    value = parse_date(value)
    if not value:
        return "未添加时间信息"
    if student and student.student_type == "school":
        return "未添加时间信息"
    current_grade = grade_number(student)
    today = today or date.today()
    current_start = today.year if today.month >= 9 else today.year - 1
    target_start = value.year if value.month >= 9 else value.year - 1
    grade = current_grade + (target_start - current_start)
    month, day = value.month, value.day
    if grade <= 9:
        return "10年级前"
    if grade >= 13:
        return "12年级上学期后"
    if grade == 12 and month < 9:
        return "12年级上学期后"
    if month in (7, 8):
        return "10-11暑假" if grade == 10 else "11-12暑假"
    if month >= 9:
        return f"{grade}年级上学期"
    if month == 1:
        return f"{grade}年级寒假"
    return f"{grade}年级下学期"


def academic_start_year(student, today=None):
    today = today or date.today()
    current_grade = grade_number(student)
    current_grade_start_year = today.year if today.month >= 9 else today.year - 1
    return current_grade_start_year - (current_grade - 10)


def time_node_start_date(student, node, today=None):
    if not node or node == "未添加时间信息":
        return None
    if student and student.student_type == "school":
        return None
    grade_10_year = academic_start_year(student, today)
    if node == "10年级前":
        return date(grade_10_year - 1, 9, 1)
    if node == "10-11暑假":
        return date(grade_10_year + 1, 7, 1)
    if node == "11-12暑假":
        return date(grade_10_year + 2, 7, 1)
    if node == "12年级上学期后":
        return date(grade_10_year + 3, 1, 1)
    for grade in (10, 11, 12):
        start_year = grade_10_year + (grade - 10)
        if node == f"{grade}年级上学期":
            return date(start_year, 9, 1)
        if node == f"{grade}年级寒假":
            return date(start_year + 1, 1, 1)
        if node == f"{grade}年级下学期":
            return date(start_year + 1, 2, 1)
    return None


def resolve_time_node(form, field, student, date_value):
    selected = form.get(field, "").strip()
    if selected and selected != "未添加时间信息":
        return selected
    if date_value:
        return infer_time_node(student, date_value)
    return selected or "未添加时间信息"


def resolve_task_date(form, student):
    date_value = parse_date(form.get("due_date"))
    if date_value:
        return date_value
    selected_node = (form.get("time_node") or "").strip()
    return time_node_start_date(student, selected_node)


def display_time(value, node):
    node = node or "未添加时间信息"
    if value:
        return f"{value.strftime('%m/%d/%Y')} · {node}"
    return node


def display_task_time(task):
    return display_time(task.due_date, task.time_node)


def cycle_priority(item):
    current = item.priority_level if hasattr(item, "priority_level") else item.priority
    direction = getattr(item, "priority_direction", "up") or "up"
    if current == "低":
        new, direction = "中", "up"
    elif current == "高":
        new, direction = "中", "down"
    elif direction == "down":
        new, direction = "低", "up"
    else:
        new, direction = "高", "down"
    if hasattr(item, "priority_level"):
        item.priority_level = new
    else:
        item.priority = new
    item.priority_direction = direction
    return new


def parse_reminder_days(value, default=(7, 1)):
    try:
        days = sorted({max(0, int(part.strip())) for part in (value or "").split(",") if part.strip()}, reverse=True)
        return days or list(default)
    except ValueError:
        return list(default)


def task_due_state(task, today=None):
    today = today or date.today()
    if not task.due_date or task.status in ["已完成", "已取消"]:
        return ""
    if task.due_date < today:
        return "overdue"
    if task.due_date <= today + timedelta(days=7):
        return "soon"
    return ""


def application_due_state(application, today=None):
    today = today or date.today()
    if not application.deadline or application.status in ["Accepted", "Rejected", "放弃"]:
        return ""
    if application.deadline < today:
        return "overdue"
    if application.deadline <= today + timedelta(days=14):
        return "soon"
    return ""


def follow_up_state(communication, today=None):
    today = today or date.today()
    if not communication.next_follow_up:
        return ""
    if communication.next_follow_up < today:
        return "overdue"
    if communication.next_follow_up <= today + timedelta(days=7):
        return "soon"
    return ""


def commit(db: Session, obj):
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete(db: Session, obj):
    db.delete(obj)
    db.commit()


def get_students(db: Session, q=None, grade=None, priority=None, student_type=None):
    query = db.query(models.Student)
    if student_type:
        query = query.filter(models.Student.student_type == student_type)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            models.Student.name.ilike(like),
            models.Student.grade.ilike(like),
            models.Student.enrollment_year.ilike(like),
            models.Student.current_school.ilike(like),
            models.Student.target_school.ilike(like),
            models.Student.target_major.ilike(like),
            models.Student.notes.ilike(like),
            models.Student.phone.ilike(like),
            models.Student.address.ilike(like),
        ))
    if grade:
        query = query.filter(models.Student.grade == grade)
    if priority:
        query = query.filter(models.Student.priority_level == priority)
    priority_order = case((models.Student.priority_level == "高", 0), (models.Student.priority_level == "中", 1), else_=2)
    grade_order = case((models.Student.grade.contains("12"), 0), (models.Student.grade.contains("11"), 1), (models.Student.grade.contains("10"), 2), else_=3)
    return query.order_by(priority_order, grade_order, models.Student.name.asc()).all()


def dashboard_stats(db: Session):
    today = date.today()
    soon = today + timedelta(days=7)
    active_filter = ~models.Task.status.in_(["已完成", "已取消"])
    follow_up_tasks = (
        db.query(models.Task)
        .filter(active_filter, models.Task.due_date <= soon)
        .order_by(models.Task.due_date.asc())
        .all()
    )
    important_items = db.query(models.ImportantItem).order_by(models.ImportantItem.date.asc().nullslast(), models.ImportantItem.id.desc()).all()
    return {
        "student_count": db.query(models.Student).count(),
        "task_count": db.query(models.Task).count(),
        "important_item_count": len(important_items),
        "follow_up_count": len(follow_up_tasks),
        "high_priority_count": db.query(models.Student).filter(models.Student.priority_level == "高").count(),
        "high_priority_students": get_students(db, priority="高"),
        "important_items": important_items,
        "follow_up_tasks": follow_up_tasks,
    }


ACCEPTED_APPLICATION_STATUSES = {"Accepted", "Conditional", "录取", "已录取"}
REJECTED_APPLICATION_STATUSES = {"Rejected", "Unsuccessful", "拒绝", "已拒绝"}


def application_statistics(db: Session):
    rows = []
    students = db.query(models.Student).order_by(models.Student.name.asc()).all()
    for index, student in enumerate(students, start=1):
        applications = list(student.applications)
        total = len(applications)
        accepted = sum(item.status in ACCEPTED_APPLICATION_STATUSES for item in applications)
        rejected = sum(item.status in REJECTED_APPLICATION_STATUSES for item in applications)
        decided = accepted + rejected
        rows.append({
            "index": index,
            "student": student,
            "total": total,
            "accepted": accepted,
            "nominal_acceptance_rate": accepted / total if total else None,
            "rejected": rejected,
            "nominal_rejection_rate": rejected / total if total else None,
            "effective": decided,
            "actual_acceptance_rate": accepted / decided if decided else None,
            "actual_rejection_rate": rejected / decided if decided else None,
            "progress_rate": decided / total if total else None,
            "final_school": student.final_school or "",
        })
    return rows


def distinct_values(db: Session, model, column_name):
    column = getattr(model, column_name)
    rows = db.query(column).filter(column != "").group_by(column).order_by(column).all()
    return [row[0] for row in rows if row[0]]


def normalize_task_time_fields(db: Session):
    changed = False
    tasks = db.query(models.Task).outerjoin(models.Student).all()
    for task in tasks:
        if task.owner != "学生":
            task.owner = "学生"
            changed = True
        if task.status not in schemas.TASK_STATUSES:
            task.status = "待确认" if task.status in ["等待反馈", "进行中"] else "WIP"
            changed = True
        if task.student and task.due_date and task.time_node in (None, "", "未添加时间信息"):
            inferred = infer_time_node(task.student, task.due_date)
            if task.time_node != inferred:
                task.time_node = inferred
                changed = True
        if not task.due_date and task.time_node and task.time_node != "未添加时间信息":
            task.due_date = time_node_start_date(task.student, task.time_node)
            changed = True
    if changed:
        db.commit()
