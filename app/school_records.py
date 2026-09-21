"""Student-scoped school records using the existing authentication/audit middleware."""
from types import SimpleNamespace
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app import models
from app.database import get_db
from app.school_fields import RECORD_FIELDS, parse_fields

router = APIRouter()
MODELS = {"activities": models.StudentActivity, "third-party-interviews": models.ThirdPartyInterview,
          "school-meetings": models.SchoolMeeting, "training-records": models.TrainingRecord,
          "applications": models.Application}


def context(db, student_id, kind, record_id=None):
    student = db.get(models.Student, student_id)
    if not student or student.student_type != "school" or kind not in MODELS:
        raise HTTPException(404, "美初美高档案或记录不存在。")
    item = None
    if record_id is not None:
        item = db.query(MODELS[kind]).filter_by(id=record_id, student_id=student_id).first()
        if not item:
            raise HTTPException(404, "记录不存在。")
    return student, item


def render(request, db, student, kind, item=None, error="", status_code=200, editing=False):
    from app.main import templates, common_context
    title, fields = RECORD_FIELDS[kind]
    return templates.TemplateResponse("school_record_form.html", {
        "request": request, **common_context(db), "student": student, "kind": kind,
        "title": title, "fields": fields, "item": item, "error": error, "editing": editing,
    }, status_code=status_code)


def back(student_id, kind):
    return RedirectResponse(f"/students/{student_id}#{kind}", status_code=303)


@router.get("/students/{student_id}/school-records/{kind}/new")
def new_record(student_id: int, kind: str, request: Request, db: Session = Depends(get_db)):
    student, _ = context(db, student_id, kind)
    initial = SimpleNamespace(applying_grade=student.applying_grade, status="WIP") if kind == "applications" else None
    return render(request, db, student, kind, initial)


@router.post("/students/{student_id}/school-records/{kind}/new")
async def create_record(student_id: int, kind: str, request: Request, db: Session = Depends(get_db)):
    student, _ = context(db, student_id, kind)
    form = await request.form()
    try:
        values = parse_fields(form, RECORD_FIELDS[kind][1])
    except ValueError as exc:
        return render(request, db, student, kind, SimpleNamespace(**dict(form)), str(exc), 400)
    item = MODELS[kind](student_id=student_id, **values)
    if kind == "applications":
        item.program_type = "中学"
        item.country = "美国"
    db.add(item)
    db.commit()
    return back(student_id, kind)


@router.get("/students/{student_id}/school-records/{kind}/{record_id}/edit")
def edit_record(student_id: int, kind: str, record_id: int, request: Request, db: Session = Depends(get_db)):
    student, item = context(db, student_id, kind, record_id)
    return render(request, db, student, kind, item, editing=True)


@router.post("/students/{student_id}/school-records/{kind}/{record_id}/edit")
async def update_record(student_id: int, kind: str, record_id: int, request: Request, db: Session = Depends(get_db)):
    student, item = context(db, student_id, kind, record_id)
    form = await request.form()
    try:
        values = parse_fields(form, RECORD_FIELDS[kind][1])
    except ValueError as exc:
        return render(request, db, student, kind, SimpleNamespace(**dict(form)), str(exc), 400, editing=True)
    for key, value in values.items():
        setattr(item, key, value)
    db.commit()
    return back(student_id, kind)


@router.post("/students/{student_id}/school-records/{kind}/{record_id}/delete")
def delete_record(student_id: int, kind: str, record_id: int, request: Request, db: Session = Depends(get_db)):
    _, item = context(db, student_id, kind, record_id)
    db.delete(item)
    db.commit()
    return back(student_id, kind)
