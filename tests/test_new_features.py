from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import crud, models
from app.database import Base, reference_rows_from_template
from app.importer import apply_import, filter_import_payload, merge_import_payloads, parse_template_workbook
from app.public_data import _extract_events


def test_application_statistics_rates():
    from app.database import engine
    db = sessionmaker(bind=engine)()
    student = models.Student(name="测试学生", final_school="Example University")
    db.add(student)
    db.flush()
    db.add_all([
        models.Application(student_id=student.id, program_name="A", status="Accepted"),
        models.Application(student_id=student.id, program_name="B", status="Rejected"),
        models.Application(student_id=student.id, program_name="C", status="等结果"),
    ])
    db.commit()

    row = crud.application_statistics(db)[0]
    assert row["total"] == 3
    assert row["accepted"] == 1
    assert row["rejected"] == 1
    assert row["effective"] == 2
    assert row["nominal_acceptance_rate"] == 1 / 3
    assert row["actual_acceptance_rate"] == 1 / 2
    assert row["progress_rate"] == 2 / 3
    assert row["final_school"] == "Example University"
    db.close()


def test_reference_database_template_can_be_loaded():
    rows = reference_rows_from_template()
    categories = {row["category"] for row in rows}
    assert {"美国", "英国", "加拿大", "申请系统", "AP科目名称"} <= categories
    assert any(row["name"] == "ACCD" and row["code"] == "4009" for row in rows)
    assert any(row["name"] == "AP Biology" for row in rows)
    assert any(row["category"] == "申请系统" and row["url"] for row in rows)
    assert any(row["name"] == "Oxford" and row["url"].startswith("https://") for row in rows)


def test_time_node_uses_student_grade_and_target_academic_year():
    today = date(2026, 8, 6)
    assert crud.infer_time_node(models.Student(grade="10年级"), date(2026, 8, 7), today) == "10-11暑假"
    assert crud.infer_time_node(models.Student(grade="11年级"), date(2026, 8, 7), today) == "11-12暑假"
    assert crud.infer_time_node(models.Student(grade="10年级"), date(2026, 10, 1), today) == "11年级上学期"


def test_priority_bounces_low_medium_high_medium_low():
    student = models.Student(priority_level="低", priority_direction="up")
    assert [crud.cycle_priority(student) for _ in range(4)] == ["中", "高", "中", "低"]


def test_students_sort_by_priority_then_grade_descending():
    from app.database import engine
    db = sessionmaker(bind=engine)()
    db.add_all([
        models.Student(name="低十二", priority_level="低", grade="12年级"),
        models.Student(name="高十", priority_level="高", grade="10年级"),
        models.Student(name="中十一", priority_level="中", grade="11年级"),
        models.Student(name="高十二", priority_level="高", grade="12年级"),
    ])
    db.commit()
    assert [row.name for row in crud.get_students(db)] == ["高十二", "高十", "中十一", "低十二"]
    db.close()


def test_multiple_imports_merge_by_a2_and_keep_first_value():
    first = {"students": [{"name": "同一学生", "personal": {"phone": "111"}, "applications": [], "exams": []}], "warnings": []}
    second = {"students": [{"name": "同一学生", "personal": {"phone": "222", "address": "新地址"}, "applications": [], "exams": []}], "warnings": []}
    merged = merge_import_payloads([first, second])
    assert merged["files"] == 2
    assert len(merged["students"]) == 1
    assert merged["students"][0]["personal"] == {"phone": "111", "address": "新地址"}


def test_import_selection_filters_individual_fields_and_records():
    payload = {"files": 1, "warnings": [], "students": [{
        "name": "选择测试", "personal": {"phone": "111", "address": "地址"},
        "applications": [{"program_name": "A"}, {"program_name": "B"}],
        "exams": [{"exam_name": "TOEFL"}],
    }]}
    selected = ["s0:student", "s0:p:address", "s0:a:1"]
    filtered = filter_import_payload(payload, selected)
    assert filtered["students"][0]["personal"] == {"address": "地址"}
    assert [row["program_name"] for row in filtered["students"][0]["applications"]] == ["B"]
    assert filtered["students"][0]["exams"] == []


def test_bulk_workbook_contains_full_import_test_data():
    from pathlib import Path
    content = (Path(__file__).parent / "fixtures" / "bulk_test_template.xlsx").read_bytes()
    payload = parse_template_workbook(content)
    assert len(payload["students"]) == 24
    assert all(len(row["applications"]) == 3 for row in payload["students"])
    assert all({exam["exam_name"] for exam in row["exams"]} == {"TOEFL", "SAT", "ACT", "AP"} for row in payload["students"])
    assert payload["students"][0]["applications"][0]["portal_q5"] == "验证码提示"


def test_exam_detail_and_component_formats_follow_exam_name():
    assert crud.exam_detail(models.Exam(exam_name="TOEFL", appointment_number="T-1")) == "T-1"
    assert crud.exam_detail(models.Exam(exam_name="SAT", record_locator="S-1")) == "S-1"
    assert crud.exam_detail(models.Exam(exam_name="AP", subject="AP Biology")) == "AP Biology"
    assert crud.exam_detail(models.Exam(exam_name="ACT")) == "-"
    assert crud.exam_component_scores(models.Exam(exam_name="TOEFL", reading="28", listening="29", speaking="25", writing="27")) == "28 / 29 / 25 / 27"
    assert crud.exam_component_scores(models.Exam(exam_name="SAT", reading="720", math="780")) == "720 / 780"
    assert crud.exam_component_scores(models.Exam(exam_name="ACT", math="34", science="33", english="35", reading="32", writing="10")) == "34 / 33 / 35 / 32 / 10"
    assert crud.exam_component_scores(models.Exam(exam_name="AP", subject="AP Biology")) == "-"


def test_import_overwrites_selected_existing_database_fields():
    from app.database import engine
    db = sessionmaker(bind=engine)()
    student = models.Student(name="已有学生", grade="12年级", phone="原电话")
    db.add(student)
    db.flush()
    db.add(models.Application(student_id=student.id, program_name="Example University", status="Submitted", portal_id=""))
    db.commit()

    payload = {"students": [{
        "name": "已有学生",
        "personal": {"phone": "新电话", "address": "补充地址"},
        "applications": [{"program_name": "Example University", "status": "Accepted", "portal_id": "new-portal", "deadline": "2027-01-01"}],
        "exams": [],
    }]}
    result = apply_import(db, payload)
    db.refresh(student)
    application = db.query(models.Application).one()
    assert student.phone == "新电话"
    assert student.address == "补充地址"
    assert application.status == "Accepted"
    assert application.portal_id == "new-portal"
    assert application.deadline == date(2027, 1, 1)
    assert result["fields_filled"] >= 3
    assert result["fields_overwritten"] >= 2
    db.close()


def test_public_data_extracts_english_and_chinese_dates():
    source = models.DataSource(name="竞赛官网", category="竞赛", url="https://example.com")
    events = _extract_events("Application deadline October 15, 2026；中国赛区截止时间 2026年11月2日。", source)
    assert {row[0] for row in events} == {date(2026, 10, 15), date(2026, 11, 2)}
