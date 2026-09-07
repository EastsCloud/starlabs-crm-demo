from io import BytesIO, StringIO
import csv
from datetime import date
import json
import os
from pathlib import Path
import secrets
import uuid

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.database import SessionLocal, get_db, init_db
from app.previews import save_preview, consume_preview
from app.importer import apply_import, filter_import_payload, merge_import_payloads, parse_template_workbook, preview_context
from app.public_data import refresh_source, start_worker, validate_public_url


app = FastAPI(title="学生规划管理系统")
from app.auth import install, bootstrap, remember_cipher
install(app)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup():
    remember_cipher()
    init_db()
    bootstrap()
    db = SessionLocal()
    try:
        crud.normalize_task_time_fields(db)
        crud.normalize_application_detail_fields(db)
    finally:
        db.close()
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        start_worker()


def redirect(url: str):
    return RedirectResponse(url=url, status_code=303)


def fallback_next(request: Request, default: str):
    return request.query_params.get("next") or request.headers.get("referer") or default


def get_or_404(db: Session, model, object_id: int):
    obj = db.get(model, object_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Not found")
    return obj


def common_context(db: Session):
    return {
        "students": db.query(models.Student).order_by(models.Student.name.asc()).all(),
        "schemas": schemas,
        "task_due_state": crud.task_due_state,
        "application_due_state": crud.application_due_state,
        "follow_up_state": crud.follow_up_state,
        "display_time": crud.display_time,
        "display_task_time": crud.display_task_time,
        "exam_detail": crud.exam_detail,
        "exam_component_scores": crud.exam_component_scores,
        "timeline_group": timeline_group,
        "timeline_group_label": timeline_group_label,
        "parse_reminder_days": crud.parse_reminder_days,
    }


def student_category_by_slug(slug: str):
    category = schemas.STUDENT_CATEGORY_SLUGS.get(slug)
    if not category:
        raise HTTPException(status_code=404)
    return category


def advisor_category_by_slug(slug: str):
    category = schemas.ADVISOR_CATEGORY_SLUGS.get(slug)
    if not category:
        raise HTTPException(status_code=404)
    return category


def selected_or_custom(form, field, default="其他"):
    custom = form.get(f"{field}_custom", "").strip()
    value = form.get(field, default).strip()
    if value == "其他" and custom:
        return custom
    return value or default


def parse_student_id(form, required=True):
    value = (form.get("student_id") or "").strip()
    if value:
        return int(value)
    if required:
        raise HTTPException(status_code=400, detail="请选择学生")
    return None


def parse_query_int(value):
    if value in (None, ""):
        return None
    return int(value)


def update_fields(obj, data):
    for key, value in data.items():
        setattr(obj, key, value)
    return obj


def parse_student_form(form):
    return {
        "name": form.get("name", "").strip(),
        "grade": form.get("grade", "").strip(),
        "target_school": form.get("target_school", "").strip(),
        "target_major": form.get("target_major", "").strip(),
        "priority_level": form.get("priority_level", "中"),
        "birth_date": crud.parse_date(form.get("birth_date")),
        "phone": form.get("phone", "").strip(),
        "address": form.get("address", "").strip(),
        "chinese_address": form.get("chinese_address", "").strip(),
        "national_id_number": form.get("national_id_number", "").strip(),
        "passport_number": form.get("passport_number", "").strip(),
        "passport_issue_date": crud.parse_date(form.get("passport_issue_date")),
        "passport_expiry_date": crud.parse_date(form.get("passport_expiry_date")),
        "bank_certificate_amount": form.get("bank_certificate_amount", "").strip(),
        "credit_card_holder": form.get("credit_card_holder", "").strip(),
        "cardholder_phone": form.get("cardholder_phone", "").strip(),
        "card_channel": form.get("card_channel", "").strip(),
        "card_number": form.get("card_number", "").strip(),
        "card_expiry": form.get("card_expiry", "").strip(),
        "notes": form.get("notes", "").strip(),
    }


MATRIX_GROUPS = [
    ("未添加时间信息", ["未添加时间信息"]),
    ("10年级前", ["10年级前"]),
    ("10年级", ["10年级上学期", "10年级寒假", "10年级下学期"]),
    ("10-11暑假", ["10-11暑假"]),
    ("11年级上学期", ["11年级上学期"]),
    ("11年级寒假", ["11年级寒假"]),
    ("11年级下学期", ["11年级下学期"]),
    ("11-12暑假", ["11-12暑假"]),
    ("12年级上学期", ["12年级上学期"]),
    ("12年级上学期后", ["12年级上学期后"]),
]


def matrix_columns():
    return [{"key": f"node_{i}", "label": label, "nodes": nodes} for i, (label, nodes) in enumerate(MATRIX_GROUPS)]


def matrix_rows(db: Session):
    rows = []
    columns = matrix_columns()
    for student in db.query(models.Student).order_by(models.Student.name.asc()).all():
        cells = {}
        for col in columns:
            tasks = (
                db.query(models.Task)
                .filter(models.Task.student_id == student.id, models.Task.time_node.in_(col["nodes"]))
                .order_by(models.Task.id.asc())
                .all()
            )
            cells[col["key"]] = "\n".join(task.title for task in tasks)
        rows.append({"student": student, "cells": cells, "notes": student.notes or ""})
    return columns, rows


def apply_task_filters(query, student_id=None, status=None, priority=None, category=None, due=None, q=None):
    if student_id:
        query = query.filter(models.Task.student_id == student_id)
    if status:
        query = query.filter(models.Task.status == status)
    if priority:
        query = query.filter(models.Task.priority == priority)
    if category:
        query = query.filter(models.Task.category == category)
    if q:
        like = f"%{q}%"
        query = query.outerjoin(models.Student).filter(or_(
            models.Task.title.ilike(like), models.Task.description.ilike(like),
            models.Task.category.ilike(like), models.Task.status.ilike(like),
            models.Task.priority.ilike(like), models.Student.name.ilike(like),
        ))
    rows = query.order_by(models.Task.due_date.asc().nullslast(), models.Task.id.desc()).all()
    if due == "overdue":
        rows = [row for row in rows if crud.task_due_state(row) == "overdue"]
    elif due == "soon":
        rows = [row for row in rows if crud.task_due_state(row) == "soon"]
    return rows


def task_data(form, db):
    student_id = parse_student_id(form)
    student = db.get(models.Student, student_id)
    due_date = crud.resolve_task_date(form, student)
    return {
        "student_id": student_id,
        "title": form.get("title", "").strip(),
        "category": selected_or_custom(form, "category"),
        "status": form.get("status", "WIP"),
        "priority": form.get("priority", "中"),
        "owner": "学生",
        "due_date": due_date,
        "time_node": crud.resolve_time_node(form, "time_node", student, due_date),
        "description": form.get("description", "").strip(),
        "reminder_days": form.get("reminder_days", "7,1").strip() or "7,1",
    }


def timeline_group(task, today=None):
    today = today or date.today()
    if task.status in ["已完成", "已取消"]:
        return "done_past"
    if task.due_date and task.due_date < today:
        return "overdue"
    if task.due_date == today:
        return "today"
    return "future"


def timeline_group_label(group):
    return {"done_past": "已完成/已过期", "overdue": "已逾期", "today": "今日", "future": "今日后"}.get(group, "今日后")


def timeline_sort_key(task, today=None):
    today = today or date.today()
    order = {"done_past": 0, "overdue": 1, "today": 2, "future": 3}
    group = timeline_group(task, today)
    sort_date = task.due_date or date.max
    return (order[group], sort_date, task.id)


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("dashboard.html", {"request": request, **common_context(db), **crud.dashboard_stats(db)})


@app.get("/important-items")
def important_items(request: Request, q: str | None = None, item_type: str | None = None, db: Session = Depends(get_db)):
    query = db.query(models.ImportantItem)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            models.ImportantItem.item_type.ilike(like), models.ImportantItem.content.ilike(like),
            models.ImportantItem.participants.ilike(like), models.ImportantItem.reminder.ilike(like),
        ))
    if item_type:
        query = query.filter(models.ImportantItem.item_type == item_type)
    rows = query.order_by(models.ImportantItem.date.asc().nullslast(), models.ImportantItem.id.desc()).all()
    return templates.TemplateResponse("important_items.html", {"request": request, **common_context(db), "rows": rows, "filters": {"q": q or "", "item_type": item_type or ""}})


@app.get("/important-items/new")
def new_important_item(request: Request, content: str | None = None, item_date: str | None = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse("important_item_form.html", {
        "request": request, **common_context(db), "item": None,
        "prefill": {"content": (content or "").strip(), "date": crud.parse_date(item_date)},
        "action": "/important-items",
    })


@app.post("/important-items")
async def create_important_item(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    content = form.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="请填写重要事项内容")
    item = models.ImportantItem(
        item_type=form.get("item_type", "其他").strip() or "其他",
        content=content,
        participants=form.get("participants", "").strip(),
        date=crud.parse_date(form.get("date")),
        reminder=form.get("reminder", "").strip(),
    )
    crud.commit(db, item)
    return redirect("/important-items")


@app.get("/important-items/{item_id}/edit")
def edit_important_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, models.ImportantItem, item_id)
    return templates.TemplateResponse("important_item_form.html", {
        "request": request, **common_context(db), "item": item, "prefill": {},
        "action": f"/important-items/{item.id}/edit",
    })


@app.post("/important-items/{item_id}/edit")
async def update_important_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, models.ImportantItem, item_id)
    form = await request.form()
    content = form.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="请填写重要事项内容")
    item.item_type = form.get("item_type", "其他").strip() or "其他"
    item.content = content
    item.participants = form.get("participants", "").strip()
    item.date = crud.parse_date(form.get("date"))
    item.reminder = form.get("reminder", "").strip()
    crud.commit(db, item)
    return redirect("/important-items")


@app.post("/important-items/{item_id}/delete")
def delete_important_item(item_id: int, db: Session = Depends(get_db)):
    crud.delete(db, get_or_404(db, models.ImportantItem, item_id))
    return redirect("/important-items")


@app.get("/statistics")
def statistics(request: Request, school: str | None = None, student_id: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    student_id_value = parse_query_int(student_id)
    school_query = (school or "").strip()
    school_results = []
    if school_query:
        school_results = (
            db.query(models.Application)
            .join(models.Student)
            .filter(models.Application.program_name.ilike(f"%{school_query}%"))
            .order_by(models.Student.name.asc(), models.Application.deadline.asc().nullslast())
            .all()
        )
    status_results = []
    if student_id_value and status:
        status_results = (
            db.query(models.Application)
            .filter(models.Application.student_id == student_id_value, models.Application.status == status)
            .order_by(models.Application.program_name.asc())
            .all()
        )
    return templates.TemplateResponse("statistics.html", {
        "request": request,
        **common_context(db),
        "rows": crud.application_statistics(db),
        "school_names": crud.distinct_values(db, models.Application, "program_name"),
        "application_statuses": crud.distinct_values(db, models.Application, "status"),
        "filters": {"school": school_query, "student_id": student_id_value, "status": status or ""},
        "school_results": school_results,
        "status_results": status_results,
    })


@app.get("/database")
def reference_database(request: Request, category: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    query = db.query(models.ReferenceItem)
    if category:
        query = query.filter(models.ReferenceItem.category == category)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(models.ReferenceItem.category.ilike(like), models.ReferenceItem.name.ilike(like), models.ReferenceItem.code.ilike(like), models.ReferenceItem.secondary_code.ilike(like)))
    rows = query.order_by(models.ReferenceItem.category.asc(), models.ReferenceItem.sort_order.asc(), models.ReferenceItem.name.asc()).all()
    categories = crud.distinct_values(db, models.ReferenceItem, "category")
    return templates.TemplateResponse("database.html", {"request": request, **common_context(db), "rows": rows, "categories": categories, "filters": {"category": category or "", "q": q or ""}})


@app.get("/import")
def import_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("import.html", {"request": request, **common_context(db), "preview": None, "token": None, "result": None})


@app.get("/import/template")
def download_import_template():
    template_path = Path(__file__).resolve().parent.parent / "docs" / "information_template.xlsx"
    return FileResponse(template_path, filename="information_template.xlsx")


@app.post("/import/preview")
async def import_preview(request: Request, files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个 .xlsx 文件")
    payloads = []
    for file in files:
        if not (file.filename or "").lower().endswith(".xlsx"):
            raise HTTPException(status_code=400, detail=f"{file.filename or '文件'} 不是 .xlsx 文件")
        content = await file.read(20_000_001)
        if len(content) > 20_000_000:
            raise HTTPException(status_code=400, detail=f"{file.filename} 不能超过 20MB")
        try:
            payload = parse_template_workbook(content)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"无法解析 {file.filename}：{exc}") from exc
        payload["warnings"] = [f"{file.filename}：{warning}" for warning in payload.get("warnings", [])]
        payloads.append(payload)
    preview = merge_import_payloads(payloads)
    existing_names = {
        name for (name,) in db.query(models.Student.name).filter(
            models.Student.name.in_([row["name"] for row in preview["students"]])
        ).all()
    }
    for row in preview["students"]:
        row["existing"] = row["name"] in existing_names
    preview["existing_count"] = len(existing_names)
    token = save_preview(db, request.state.user.id, 'excel', preview)
    db.commit()
    return templates.TemplateResponse("import.html", {"request": request, **common_context(db), **preview_context(), "preview": preview, "token": token, "result": None})


@app.post("/import/apply")
async def import_apply(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    token = (form.get("token") or "").strip()
    if not re_fullmatch_hex(token):
        raise HTTPException(status_code=400, detail="无效导入令牌")
    payload = consume_preview(db, token, request.state.user.id, 'excel')
    if form.get("selection_mode") == "explicit":
        payload = filter_import_payload(payload, form.getlist("selected"))
    result = apply_import(db, payload)
    return templates.TemplateResponse("import.html", {"request": request, **common_context(db), "preview": None, "token": None, "result": result})


def re_fullmatch_hex(value):
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


@app.get("/data-engine")
def data_engine(request: Request, q: str | None = None, category: str | None = None, db: Session = Depends(get_db)):
    source_query = db.query(models.DataSource)
    event_query = db.query(models.PublicEvent).join(models.DataSource)
    if category:
        source_query = source_query.filter(models.DataSource.category == category)
        event_query = event_query.filter(models.PublicEvent.category == category)
    if q:
        like = f"%{q.strip()}%"
        source_query = source_query.filter(or_(
            models.DataSource.name.ilike(like), models.DataSource.category.ilike(like), models.DataSource.url.ilike(like),
            models.DataSource.last_status.ilike(like), models.DataSource.last_error.ilike(like),
        ))
        event_query = event_query.filter(or_(
            models.PublicEvent.title.ilike(like), models.PublicEvent.category.ilike(like),
            models.PublicEvent.date_kind.ilike(like), models.PublicEvent.source_text.ilike(like),
            models.PublicEvent.review_status.ilike(like),
            models.DataSource.name.ilike(like), models.DataSource.url.ilike(like),
        ))
    sources = source_query.order_by(models.DataSource.name).all()
    events = event_query.order_by(models.PublicEvent.event_date.asc(), models.PublicEvent.id.desc()).all()
    return templates.TemplateResponse("data_engine.html", {
        "request": request, **common_context(db), "sources": sources, "events": events,
        "filters": {"q": q or "", "category": category or ""},
        "current_url": str(request.url),
    })


@app.get("/data-engine/sources/new")
def new_data_source(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("data_source_form.html", {
        "request": request, **common_context(db), "source": None,
        "action": "/data-engine/sources", "next_url": fallback_next(request, "/data-engine"),
    })


@app.post("/data-engine/sources")
async def create_data_source(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        url = validate_public_url(form.get("url", "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = form.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写数据源名称")
    source = models.DataSource(name=name, category=form.get("category", "其他"), url=url, refresh_hours=max(1, int(form.get("refresh_hours") or 24)))
    crud.commit(db, source)
    return redirect(form.get("next") or "/data-engine")


@app.get("/data-engine/sources/{source_id}/edit")
def edit_data_source(source_id: int, request: Request, db: Session = Depends(get_db)):
    source = get_or_404(db, models.DataSource, source_id)
    return templates.TemplateResponse("data_source_form.html", {
        "request": request, **common_context(db), "source": source,
        "action": f"/data-engine/sources/{source.id}/edit", "next_url": fallback_next(request, "/data-engine"),
    })


@app.post("/data-engine/sources/{source_id}/edit")
async def update_data_source(source_id: int, request: Request, db: Session = Depends(get_db)):
    source = get_or_404(db, models.DataSource, source_id)
    form = await request.form()
    try:
        source.url = validate_public_url(form.get("url", "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = form.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写数据源名称")
    source.name = name
    source.category = form.get("category", "其他")
    source.refresh_hours = max(1, int(form.get("refresh_hours") or 24))
    crud.commit(db, source)
    return redirect(form.get("next") or "/data-engine")


@app.post("/data-engine/sources/{source_id}/refresh")
async def refresh_data_source(source_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    refresh_source(db, get_or_404(db, models.DataSource, source_id))
    return redirect(form.get("next") or "/data-engine")


@app.post("/data-engine/sources/{source_id}/delete")
async def delete_data_source(source_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    crud.delete(db, get_or_404(db, models.DataSource, source_id))
    return redirect(form.get("next") or "/data-engine")


@app.post("/data-engine/events/{event_id}/review")
async def review_public_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, models.PublicEvent, event_id)
    form = await request.form()
    item.review_status = form.get("review_status", "待确认")
    item.reminder_days = form.get("reminder_days", "7,1").strip() or "7,1"
    crud.commit(db, item)
    return redirect("/data-engine")


@app.post("/data-engine/events/{event_id}/timeline")
def public_event_to_timeline(event_id: int, db: Session = Depends(get_db)):
    event = get_or_404(db, models.PublicEvent, event_id)
    exists = db.query(models.Timeline).filter(models.Timeline.student_id.is_(None), models.Timeline.source_event_id == event.id).first()
    if not exists:
        db.add(models.Timeline(student_id=None, owner="公共", category=event.category, content=event.title, status="WIP", date=event.event_date, time_node="未添加时间信息", source_event_id=event.id, reminder_days=event.reminder_days, notes=event.source_url))
        db.commit()
    return redirect("/data-engine")


@app.post("/data-engine/events/{event_id}/tasks")
async def public_event_to_tasks(event_id: int, request: Request, db: Session = Depends(get_db)):
    event = get_or_404(db, models.PublicEvent, event_id)
    form = await request.form()
    student_id = parse_query_int(form.get("student_id"))
    if not student_id:
        raise HTTPException(status_code=400, detail="请选择学生")
    student = get_or_404(db, models.Student, student_id)
    exists = db.query(models.Task).filter(models.Task.student_id == student.id, models.Task.title == event.title, models.Task.due_date == event.event_date).first()
    if not exists:
        db.add(models.Task(student_id=student.id, title=event.title, category="项目和活动" if event.category in ["科研", "竞赛"] else ("标化考试" if event.category == "考试" else "其他"), status="WIP", priority="中", due_date=event.event_date, time_node=crud.infer_time_node(student, event.event_date), description=f"来源：{event.source_url}", reminder_days=event.reminder_days))
        db.commit()
    return redirect("/data-engine")


@app.get("/data-engine/events/{event_id}/important-item")
def public_event_to_important_item(event_id: int, request: Request, db: Session = Depends(get_db)):
    event = get_or_404(db, models.PublicEvent, event_id)
    return templates.TemplateResponse("important_item_form.html", {
        "request": request, **common_context(db), "item": None,
        "prefill": {"content": event.title, "date": event.event_date},
        "action": "/important-items",
    })


@app.get("/matrix")
def matrix(request: Request, db: Session = Depends(get_db)):
    columns, rows = matrix_rows(db)
    return templates.TemplateResponse("matrix.html", {"request": request, **common_context(db), "matrix_columns": columns, "matrix_rows": rows})


@app.post("/matrix")
async def update_matrix(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    columns = matrix_columns()
    for student in db.query(models.Student).all():
        for col in columns:
            content = form.get(f"cell_{student.id}_{col['key']}", "").strip()
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            tasks = db.query(models.Task).filter(models.Task.student_id == student.id, models.Task.time_node.in_(col["nodes"])).order_by(models.Task.id.asc()).all()
            for idx, line in enumerate(lines):
                node = col["nodes"][0]
                if idx < len(tasks):
                    task = tasks[idx]
                else:
                    task = models.Task(student_id=student.id, owner="学生", category="其他", status="未开始", priority="中", time_node=node, due_date=crud.time_node_start_date(student, node))
                    db.add(task)
                task.title = line
                task.time_node = task.time_node if task.time_node in col["nodes"] else node
                if not task.due_date:
                    task.due_date = crud.time_node_start_date(student, task.time_node)
                task.category = task.category or "其他"
            for task in tasks[len(lines):]:
                db.delete(task)
        student.notes = form.get(f"notes_{student.id}", "").strip()
    db.commit()
    return redirect("/matrix")


@app.get("/matrix/export")
def export_matrix(format: str = "csv", db: Session = Depends(get_db)):
    columns, rows = matrix_rows(db)
    headers = ["学生", *[col["label"] for col in columns], "备注"]
    data = [[row["student"].name, *[row["cells"][col["key"]] for col in columns], row["notes"]] for row in rows]
    if format == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "规划矩阵"
        ws.append(headers)
        for row in data:
            ws.append(row)
        output = BytesIO()
        wb.save(output)
        return Response(output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=student_matrix.xlsx"})
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(data)
    return Response(output.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=student_matrix.csv"})


@app.get("/students")
def students(request: Request, q: str | None = None, grade: str | None = None, priority: str | None = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse("students.html", {"request": request, **common_context(db), "rows": crud.get_students(db, q, grade, priority), "q": q or "", "grade": grade or "", "priority": priority or "", "grades": crud.distinct_values(db, models.Student, "grade")})


@app.get("/students/new")
def new_student(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("student_form.html", {"request": request, **common_context(db), "student": None, "action": "/students/new", "next_url": fallback_next(request, "/students")})


@app.post("/students/new")
async def create_student(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    student = models.Student(**parse_student_form(form))
    crud.commit(db, student)
    return redirect(form.get("next") or f"/students/{student.id}")


@app.get("/students/{student_id}")
def student_detail(student_id: int, request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("student_detail.html", {"request": request, **common_context(db), "student": get_or_404(db, models.Student, student_id), "communication_rows": db.query(models.Communication).filter_by(student_id=student_id).order_by(models.Communication.date.desc().nullslast(), models.Communication.id.desc()).all()})


@app.get("/students/{student_id}/edit")
def edit_student(student_id: int, request: Request, db: Session = Depends(get_db)):
    student = get_or_404(db, models.Student, student_id)
    return templates.TemplateResponse("student_form.html", {"request": request, **common_context(db), "student": student, "action": f"/students/{student.id}/edit", "next_url": fallback_next(request, f"/students/{student.id}")})


@app.post("/students/{student_id}/edit")
async def update_student(student_id: int, request: Request, db: Session = Depends(get_db)):
    student = get_or_404(db, models.Student, student_id)
    form = await request.form()
    update_fields(student, parse_student_form(form))
    crud.commit(db, student)
    return redirect(form.get("next") or f"/students/{student.id}")


@app.post("/students/{student_id}/priority")
async def toggle_student_priority(student_id: int, request: Request, db: Session = Depends(get_db)):
    student = get_or_404(db, models.Student, student_id)
    form = await request.form()
    new_priority = crud.cycle_priority(student)
    note = (form.get("note") or "").strip()
    if new_priority == "高" and note:
        student.notes = note
    crud.commit(db, student)
    return redirect(form.get("next") or f"/students/{student.id}")


@app.get("/portal/{token}/{section}")
def student_portal(token: str, section: str, request: Request, db: Session = Depends(get_db)):
    if section not in {"basic", "applications", "courses", "exams", "tasks"}:
        raise HTTPException(status_code=404)
    student = db.query(models.Student).filter(models.Student.portal_token == token).first()
    if not student:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("portal_student.html", {"request": request, **common_context(db), "student": student, "section": section})


@app.get("/students/{student_id}/applications")
def student_applications(student_id: int, request: Request, db: Session = Depends(get_db)):
    student = get_or_404(db, models.Student, student_id)
    return templates.TemplateResponse("student_records.html", {"request": request, **common_context(db), "student": student, "record_type": "applications", "rows": student.applications})


@app.get("/students/{student_id}/courses")
def student_courses(student_id: int, request: Request, db: Session = Depends(get_db)):
    student = get_or_404(db, models.Student, student_id)
    return templates.TemplateResponse("student_records.html", {"request": request, **common_context(db), "student": student, "record_type": "courses", "rows": student.courses})


@app.get("/students/{student_id}/exams")
def student_exams(student_id: int, request: Request, db: Session = Depends(get_db)):
    student = get_or_404(db, models.Student, student_id)
    return templates.TemplateResponse("student_records.html", {"request": request, **common_context(db), "student": student, "record_type": "exams", "rows": student.exams})


@app.get("/tasks")
def tasks(request: Request, student_id: str | None = None, status: str | None = None, priority: str | None = None, category: str | None = None, due: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    student_id_value = parse_query_int(student_id)
    rows = apply_task_filters(db.query(models.Task), student_id_value, status, priority, category, due, q)
    return templates.TemplateResponse("tasks.html", {"request": request, **common_context(db), "rows": rows, "filters": {"student_id": student_id_value, "status": status or "", "priority": priority or "", "category": category or "", "due": due or "", "q": q or ""}, "page_title": "任务", "page_description": "统一管理全部任务，可按学生、类型、状态和到期时间筛选"})


@app.get("/tasks/new")
def new_task(request: Request, student_id: str | None = None, category: str | None = None, db: Session = Depends(get_db)):
    student_id_value = parse_query_int(student_id)
    item = models.Task(student_id=student_id_value, category=category or "其他")
    return templates.TemplateResponse("task_form.html", {"request": request, **common_context(db), "item": item, "is_new": True, "student_id": student_id_value, "action": "/tasks/new", "next_url": fallback_next(request, "/tasks")})


@app.get("/tasks/new/student/{slug}")
def new_student_category_task(slug: str, request: Request, student_id: str | None = None, db: Session = Depends(get_db)):
    category = student_category_by_slug(slug)
    return redirect(f"/tasks/new?category={category}" + (f"&student_id={student_id}" if student_id else ""))


@app.get("/tasks/new/advisor/{slug}")
def new_advisor_category_task(slug: str, request: Request, student_id: str | None = None, db: Session = Depends(get_db)):
    category = advisor_category_by_slug(slug)
    return redirect(f"/tasks/new?category={category}" + (f"&student_id={student_id}" if student_id else ""))


@app.post("/tasks/new")
async def create_task(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    task = models.Task(**task_data(form, db))
    crud.commit(db, task)
    return redirect(form.get("next") or "/tasks")


@app.get("/tasks/{task_id}/edit")
def edit_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, models.Task, task_id)
    return templates.TemplateResponse("task_form.html", {"request": request, **common_context(db), "item": item, "is_new": False, "student_id": item.student_id, "action": f"/tasks/{item.id}/edit", "next_url": fallback_next(request, "/tasks")})


@app.post("/tasks/{task_id}/edit")
async def update_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, models.Task, task_id)
    form = await request.form()
    update_fields(item, task_data(form, db))
    crud.commit(db, item)
    return redirect(form.get("next") or "/tasks")


@app.post("/tasks/{task_id}/complete")
def complete_task(task_id: int, db: Session = Depends(get_db)):
    item = get_or_404(db, models.Task, task_id)
    item.status = "已完成"
    crud.commit(db, item)
    return redirect("/tasks")


@app.post("/tasks/{task_id}/status")
async def cycle_task_status(task_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, models.Task, task_id)
    form = await request.form()
    statuses = schemas.TASK_STATUSES
    item.status = statuses[(statuses.index(item.status) + 1) % len(statuses)] if item.status in statuses else statuses[0]
    crud.commit(db, item)
    return redirect(form.get("next") or "/tasks")


@app.post("/tasks/{task_id}/priority")
async def cycle_task_priority(task_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, models.Task, task_id)
    form = await request.form()
    crud.cycle_priority(item)
    crud.commit(db, item)
    return redirect(form.get("next") or "/tasks")


@app.post("/applications/{item_id}/status")
async def inline_application_status(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, models.Application, item_id)
    form = await request.form()
    status = form.get("status", "WIP")
    if status not in schemas.APPLICATION_STATUSES:
        raise HTTPException(status_code=400, detail="无效申请状态")
    item.status = status
    crud.commit(db, item)
    return redirect(form.get("next") or f"/students/{item.student_id}/applications")


@app.post("/applications/{item_id}/expand/{section}")
async def toggle_application_details(item_id: int, section: str, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, models.Application, item_id)
    fields = {"online": "online_details_expanded", "portal": "portal_details_expanded", "delivery": "delivery_details_expanded"}
    if section not in fields:
        raise HTTPException(status_code=404)
    field = fields[section]
    setattr(item, field, not bool(getattr(item, field)))
    form = await request.form()
    crud.commit(db, item)
    return redirect(form.get("next") or f"/students/{item.student_id}")


@app.post("/applications/{item_id}/details/{section}")
async def update_application_details(item_id: int, section: str, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, models.Application, item_id)
    form = await request.form()
    if section == "online":
        status = form.get("form_status", "WIP")
        if status not in schemas.APPLICATION_FORM_STATUSES:
            raise HTTPException(status_code=400, detail="无效填表状态")
        item.form_status = item.online_application_status = status
        item.submission_date = crud.parse_date(form.get("submission_date"))
        item.supplemental_essay = "Y" if form.get("supplemental_essay") else "N"
        item.transcript_required = "Y" if form.get("transcript_required") else "N"
        for field in ["application_system", "application_username", "application_password"]:
            setattr(item, field, form.get(field, "").strip())
    elif section == "portal":
        progress = form.get("portal_material_progress", "0%")
        if progress not in schemas.PORTAL_PROGRESS_OPTIONS:
            raise HTTPException(status_code=400, detail="无效 Portal 进度")
        item.portal_material_progress = item.portal_status = progress
        for field in ["portal_id", "portal_password", "portal_q1", "portal_a1", "portal_q2", "portal_a2", "portal_q3", "portal_a3", "portal_q4", "portal_a4", "portal_q5", "portal_a5"]:
            setattr(item, field, form.get(field, "").strip())
        item.portal_security_qa = "\n\n".join(
            f"Q: {getattr(item, f'portal_q{i}')}\nA: {getattr(item, f'portal_a{i}')}"
            for i in range(1, 6) if getattr(item, f"portal_q{i}") or getattr(item, f"portal_a{i}")
        )
    elif section == "delivery":
        status = form.get("score_delivery_status", "WIP")
        if status not in schemas.SCORE_DELIVERY_STATUSES:
            raise HTTPException(status_code=400, detail="无效递送状态")
        item.score_delivery_status = status
        for field in ["ceeb_code", "language_delivery", "sat_delivery", "act_code", "act_delivery", "ap_delivery", "other_delivery"]:
            setattr(item, field, form.get(field, "").strip())
        item.delivery_date = crud.parse_date(form.get("delivery_date"))
    else:
        raise HTTPException(status_code=404)
    crud.commit(db, item)
    return redirect(form.get("next") or f"/students/{item.student_id}")


@app.post("/applications/{item_id}/quick/{field}")
async def quick_application_detail(item_id: int, field: str, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, models.Application, item_id)
    form = await request.form()
    if field == "form_status":
        statuses = schemas.APPLICATION_FORM_STATUSES
        current = item.form_status or item.online_application_status or statuses[0]
        value = statuses[(statuses.index(current) + 1) % len(statuses)] if current in statuses else statuses[0]
        item.form_status = item.online_application_status = value
    elif field == "portal_material_progress":
        value = form.get("value", "0%")
        if value not in schemas.PORTAL_PROGRESS_OPTIONS:
            raise HTTPException(status_code=400, detail="无效 Portal 进度")
        item.portal_material_progress = item.portal_status = value
    elif field == "score_delivery_status":
        statuses = schemas.SCORE_DELIVERY_STATUSES
        current = item.score_delivery_status or statuses[0]
        item.score_delivery_status = statuses[(statuses.index(current) + 1) % len(statuses)] if current in statuses else statuses[0]
    else:
        raise HTTPException(status_code=404)
    crud.commit(db, item)
    return redirect(form.get("next") or f"/students/{item.student_id}")


@app.post("/tasks/{task_id}/delete")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    crud.delete(db, get_or_404(db, models.Task, task_id))
    return redirect("/tasks")


def project_data(form, db):
    student_id = parse_student_id(form)
    student = db.get(models.Student, student_id)
    start_date = crud.parse_date(form.get("start_date"))
    end_date = crud.parse_date(form.get("end_date"))
    return {"student_id": student_id, "project_name": form.get("project_name", "").strip(), "project_type": selected_or_custom(form, "project_type"), "status": form.get("status", "未开始"), "start_date": start_date, "start_time_node": crud.resolve_time_node(form, "start_time_node", student, start_date), "end_date": end_date, "end_time_node": crud.resolve_time_node(form, "end_time_node", student, end_date), "outcome": form.get("outcome", "").strip(), "risk_level": form.get("risk_level", "中"), "notes": form.get("notes", "").strip()}


def application_data(form, db):
    student_id = parse_student_id(form)
    student = db.get(models.Student, student_id)
    deadline = crud.parse_date(form.get("deadline"))
    result_date = crud.parse_date(form.get("result_date"))
    text_fields = ["country_batch", "country", "batch", "portal_id", "portal_password", "portal_material_progress", "form_status", "supplemental_essay", "transcript_required", "ceeb_code", "language_delivery", "sat_delivery", "act_code", "act_delivery", "ap_delivery", "other_delivery", "application_system", "application_username", "application_password", "portal_security_qa", "portal_url", "online_application_status", "portal_status", "score_delivery_status", "portal_q1", "portal_a1", "portal_q2", "portal_a2", "portal_q3", "portal_a3", "portal_q4", "portal_a4", "portal_q5", "portal_a5"]
    data = {"student_id": student_id, "program_name": form.get("program_name", "").strip(), "program_type": selected_or_custom(form, "program_type"), "status": form.get("status", "WIP"), "deadline": deadline, "deadline_time_node": "未添加时间信息", "result_date": result_date, "result_time_node": "未添加时间信息", "result": form.get("result", "").strip(), "materials_status": form.get("materials_status", "").strip(), "next_step": form.get("next_step", "").strip(), "notes": form.get("notes", "").strip(), "submission_date": crud.parse_date(form.get("submission_date")), "delivery_date": crud.parse_date(form.get("delivery_date"))}
    data.update({field: form.get(field, "").strip() for field in text_fields})
    data["form_status"] = data["online_application_status"] = data["form_status"] or data["online_application_status"] or "WIP"
    data["portal_material_progress"] = data["portal_status"] = data["portal_material_progress"] or data["portal_status"] or "0%"
    data["score_delivery_status"] = data["score_delivery_status"] or "WIP"
    data["supplemental_essay"] = "Y" if form.get("supplemental_essay") in {"Y", "on", "true", "1"} else "N"
    data["transcript_required"] = "Y" if form.get("transcript_required") in {"Y", "on", "true", "1"} else "N"
    data["portal_security_qa"] = "\n\n".join(f"Q: {data[f'portal_q{i}']}\nA: {data[f'portal_a{i}']}" for i in range(1, 6) if data[f"portal_q{i}"] or data[f"portal_a{i}"])
    return data


def exam_data(form, db):
    student_id = parse_student_id(form)
    student = db.get(models.Student, student_id)
    exam_date = crud.parse_date(form.get("exam_date"))
    exam_name = form.get("exam_name", "TOEFL").strip().upper()
    if exam_name not in schemas.EXAM_NAMES:
        raise HTTPException(status_code=400, detail="无效考试名称")
    fields = ["reading", "listening", "speaking", "writing", "math", "science", "english", "total", "appointment_number", "record_locator", "subject"]
    data = {"student_id": student_id, "exam_name": exam_name, "exam_date": exam_date, "exam_time_node": crud.resolve_time_node(form, "exam_time_node", student, exam_date), "status": form.get("status", "已完成"), "score": form.get("total", "").strip()}
    data.update({field: form.get(field, "").strip() for field in fields})
    allowed = {
        "TOEFL": {"reading", "listening", "speaking", "writing", "total", "appointment_number"},
        "SAT": {"reading", "math", "total", "record_locator"},
        "ACT": {"math", "science", "english", "reading", "writing", "total"},
        "AP": {"subject", "total"},
    }[exam_name]
    for field in fields:
        if field not in allowed:
            data[field] = ""
    data["component_score"] = "/".join(data[field] or "-" for field in {
        "TOEFL": ["reading", "listening", "speaking", "writing"], "SAT": ["reading", "math"],
        "ACT": ["math", "science", "english", "reading", "writing"], "AP": [],
    }[exam_name]) or "-"
    return data


def communication_data(form, db):
    student_id = parse_student_id(form)
    student = db.get(models.Student, student_id)
    date_value = crud.parse_date(form.get("date"))
    next_follow_up = crud.parse_date(form.get("next_follow_up"))
    return {"student_id": student_id, "date": date_value, "date_time_node": crud.resolve_time_node(form, "date_time_node", student, date_value), "contact_person": selected_or_custom(form, "contact_person", "家长"), "method": selected_or_custom(form, "method", "微信"), "summary": form.get("summary", "").strip(), "next_follow_up": next_follow_up, "follow_up_time_node": crud.resolve_time_node(form, "follow_up_time_node", student, next_follow_up), "generated_tasks": form.get("generated_tasks", "").strip()}


def course_data(form, db):
    return {"student_id": parse_student_id(form), "semester": form.get("semester", "").strip(), "category": form.get("category", "").strip(), "name": form.get("name", "").strip(), "level": form.get("level", "").strip(), "grade": form.get("grade", "").strip(), "credits": form.get("credits", "").strip()}


RESOURCE_CONFIG = {
    "projects": (models.Project, project_data, "project"),
    "applications": (models.Application, application_data, "application"),
    "exams": (models.Exam, exam_data, "exam"),
    "communications": (models.Communication, communication_data, "communication"),
    "courses": (models.Course, course_data, "course"),
}


@app.get("/projects")
def projects(request: Request, student_id: str | None = None, project_type: str | None = None, status: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    student_id_value = parse_query_int(student_id)
    query = db.query(models.Project).join(models.Student)
    if student_id_value:
        query = query.filter(models.Project.student_id == student_id_value)
    if project_type:
        query = query.filter(models.Project.project_type == project_type)
    if status:
        query = query.filter(models.Project.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(models.Project.project_name.ilike(like), models.Project.outcome.ilike(like), models.Project.notes.ilike(like), models.Student.name.ilike(like)))
    return templates.TemplateResponse("archive/projects.html", {"request": request, **common_context(db), "rows": query.order_by(models.Project.start_date.desc().nullslast()).all(), "filters": {"student_id": student_id_value, "project_type": project_type or "", "status": status or "", "q": q or ""}})


@app.get("/applications")
def applications(request: Request, student_id: str | None = None, status: str | None = None, program_type: str | None = None, due: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    student_id_value = parse_query_int(student_id)
    query = db.query(models.Application).join(models.Student)
    if student_id_value:
        query = query.filter(models.Application.student_id == student_id_value)
    if status:
        query = query.filter(models.Application.status == status)
    if program_type:
        query = query.filter(models.Application.program_type == program_type)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(models.Application.program_name.ilike(like), models.Application.materials_status.ilike(like), models.Application.next_step.ilike(like), models.Application.notes.ilike(like), models.Student.name.ilike(like)))
    rows = query.order_by(models.Application.deadline.asc().nullslast()).all()
    if due == "soon":
        rows = [row for row in rows if crud.application_due_state(row) == "soon"]
    return templates.TemplateResponse("archive/applications.html", {"request": request, **common_context(db), "rows": rows, "filters": {"student_id": student_id_value, "status": status or "", "program_type": program_type or "", "due": due or "", "q": q or ""}})


@app.get("/exams")
def exams(request: Request, student_id: str | None = None, status: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    student_id_value = parse_query_int(student_id)
    query = db.query(models.Exam).join(models.Student)
    if student_id_value:
        query = query.filter(models.Exam.student_id == student_id_value)
    if status:
        query = query.filter(models.Exam.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(models.Exam.exam_name.ilike(like), models.Exam.score.ilike(like), models.Exam.next_action.ilike(like), models.Student.name.ilike(like)))
    return templates.TemplateResponse("archive/exams.html", {"request": request, **common_context(db), "rows": query.order_by(models.Exam.exam_date.desc().nullslast()).all(), "filters": {"student_id": student_id_value, "status": status or "", "q": q or ""}})


@app.get("/communications")
def communications(request: Request, student_id: str | None = None, due: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    student_id_value = parse_query_int(student_id)
    query = db.query(models.Communication).join(models.Student)
    if student_id_value:
        query = query.filter(models.Communication.student_id == student_id_value)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(models.Communication.summary.ilike(like), models.Communication.generated_tasks.ilike(like), models.Student.name.ilike(like)))
    rows = query.order_by(models.Communication.date.desc().nullslast()).all()
    if due == "soon":
        rows = [row for row in rows if crud.follow_up_state(row) == "soon"]
    elif due == "overdue":
        rows = [row for row in rows if crud.follow_up_state(row) == "overdue"]
    return templates.TemplateResponse("archive/communications.html", {"request": request, **common_context(db), "rows": rows, "filters": {"student_id": student_id_value, "due": due or "", "q": q or ""}})


@app.get("/timeline")
def timeline(request: Request, participant: str | None = None, category: str | None = None, status_type: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    query = db.query(models.Task).outerjoin(models.Student)
    if participant:
        query = query.filter(models.Task.student_id == parse_query_int(participant))
    if category:
        query = query.filter(models.Task.category == category)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            models.Task.title.ilike(like), models.Task.description.ilike(like),
            models.Task.category.ilike(like), models.Task.status.ilike(like),
            models.Task.priority.ilike(like), models.Student.name.ilike(like),
        ))
    tasks = query.all()
    if status_type == "done":
        tasks = [row for row in tasks if row.status == "已完成"]
    elif status_type == "active":
        tasks = [row for row in tasks if row.status in ["WIP", "待确认"]]
    elif status_type == "not_started":
        tasks = [row for row in tasks if row.status == "WIP"]
    elif status_type == "in_progress":
        tasks = [row for row in tasks if row.status == "待确认"]
    rows = [{"title": item.title, "student": item.student, "category": item.category, "status": item.status, "date": item.due_date, "notes": item.description, "url": f"/tasks/{item.id}/edit", "kind": "任务"} for item in tasks]
    point_query = db.query(models.Timeline).outerjoin(models.Student)
    if participant:
        point_query = point_query.filter(models.Timeline.student_id == parse_query_int(participant))
    if category:
        point_query = point_query.filter(models.Timeline.category == category)
    if q:
        like = f"%{q}%"
        point_query = point_query.filter(or_(
            models.Timeline.content.ilike(like), models.Timeline.notes.ilike(like),
            models.Timeline.category.ilike(like), models.Timeline.status.ilike(like),
            models.Student.name.ilike(like),
        ))
    for item in point_query.all():
        rows.append({"title": item.content, "student": item.student, "category": item.category, "status": item.status, "date": item.date, "notes": item.notes, "url": item.notes if (item.notes or "").startswith("http") else "/timeline", "kind": "时间点"})
    today = date.today()
    for row in rows:
        row["group"] = "done_past" if row["status"] == "已完成" else ("overdue" if row["date"] and row["date"] < today else ("today" if row["date"] == today else "future"))
    rows.sort(key=lambda row: ({"done_past": 0, "overdue": 1, "today": 2, "future": 3}[row["group"]], row["date"] or date.max, row["title"]))
    return templates.TemplateResponse("timeline.html", {"request": request, **common_context(db), "rows": rows, "filters": {"participant": participant or "", "category": category or "", "status_type": status_type or "", "q": q or ""}})


@app.get("/advisor")
def advisor(request: Request, participant: str | None = None, category: str | None = None, status: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    return redirect("/tasks" + (f"?category={category}" if category else ""))


@app.get("/advisor/{slug}")
def advisor_category_page(slug: str, request: Request, participant: str | None = None, status: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    category = advisor_category_by_slug(slug)
    return redirect(f"/tasks?category={category}")


@app.get("/student-tasks/{slug}")
def student_category_page(slug: str, request: Request, student_id: str | None = None, status: str | None = None, priority: str | None = None, due: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    category = student_category_by_slug(slug)
    return redirect(f"/tasks?category={category}" + (f"&student_id={student_id}" if student_id else ""))


from app.communications import router as communication_router
app.include_router(communication_router)


@app.get("/{resource}/new")
def new_resource(resource: str, request: Request, student_id: str | None = None, db: Session = Depends(get_db)):
    if resource not in RESOURCE_CONFIG:
        raise HTTPException(status_code=404)
    _, _, form_type = RESOURCE_CONFIG[resource]
    student_id_value = parse_query_int(student_id)
    if resource == "communications" and student_id_value:
        return redirect(f"/students/{student_id_value}/communications/new")
    template_name = "record_form.html" if resource in {"applications", "exams", "courses"} else "archive/record_form.html"
    return templates.TemplateResponse(template_name, {"request": request, **common_context(db), "type": form_type, "item": None, "is_new": True, "student_id": student_id_value, "action": f"/{resource}/new", "next_url": fallback_next(request, f"/students/{student_id_value}" if student_id_value else f"/{resource}")})


@app.post("/{resource}/new")
async def create_resource(resource: str, request: Request, db: Session = Depends(get_db)):
    if resource not in RESOURCE_CONFIG:
        raise HTTPException(status_code=404)
    model, parser, _ = RESOURCE_CONFIG[resource]
    form = await request.form()
    item = model(**parser(form, db))
    crud.commit(db, item)
    return redirect(form.get("next") or f"/{resource}")


@app.get("/{resource}/{item_id}/edit")
def edit_resource(resource: str, item_id: int, request: Request, db: Session = Depends(get_db)):
    if resource not in RESOURCE_CONFIG:
        raise HTTPException(status_code=404)
    model, _, form_type = RESOURCE_CONFIG[resource]
    item = get_or_404(db, model, item_id)
    if resource == 'communications':
        return redirect(f'/students/{item.student_id}/communications/{item.id}/edit')
    template_name = "record_form.html" if resource in {"applications", "exams", "courses"} else "archive/record_form.html"
    return templates.TemplateResponse(template_name, {"request": request, **common_context(db), "type": form_type, "item": item, "is_new": False, "student_id": item.student_id, "action": f"/{resource}/{item.id}/edit", "next_url": fallback_next(request, f"/students/{item.student_id}")})


@app.post("/{resource}/{item_id}/edit")
async def update_resource(resource: str, item_id: int, request: Request, db: Session = Depends(get_db)):
    if resource not in RESOURCE_CONFIG:
        raise HTTPException(status_code=404)
    model, parser, _ = RESOURCE_CONFIG[resource]
    item = get_or_404(db, model, item_id)
    form = await request.form()
    update_fields(item, parser(form, db))
    crud.commit(db, item)
    return redirect(form.get("next") or f"/{resource}")


@app.post("/{resource}/{item_id}/delete")
async def delete_resource(resource: str, item_id: int, request: Request, db: Session = Depends(get_db)):
    if resource not in RESOURCE_CONFIG:
        raise HTTPException(status_code=404)
    form = await request.form()
    crud.delete(db, get_or_404(db, RESOURCE_CONFIG[resource][0], item_id))
    return redirect(form.get("next") or f"/{resource}")
