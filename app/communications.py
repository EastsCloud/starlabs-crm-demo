"""Student communications, private PDF exports and explicit archive deletion."""
import hashlib
import hmac
import json
import uuid
from datetime import date, datetime
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import or_, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app import models
from app.auth import page
from app.database import get_db
from app.communication_pdf import SECTIONS, MAX_PDF_BYTES, compact, parse_communication_pdf
from app.pdf_exports import communication_payload, communication_pdf, archive_pdf
from app.previews import save_preview, consume_preview

router = APIRouter()
FIELDS = {'date': 10, 'duration': 80, 'method': 50, 'school_snapshot': 200, 'grade_snapshot': 50, 'notes': 30000}


def student_or_404(db, student_id, lock=False):
    query = db.query(models.Student).filter_by(id=student_id)
    if lock: query = query.with_for_update()
    student = query.first()
    if not student: raise HTTPException(404, '学生不存在。')
    return student


def record_or_404(db, student_id, communication_id):
    record = db.query(models.Communication).filter_by(id=communication_id, student_id=student_id).first()
    if not record: raise HTTPException(404, '沟通记录不存在。')
    return record


def back(student_id):
    return RedirectResponse(f'/students/{student_id}#communications', status_code=303)


def form_payload(form, student):
    payload = {'student_name': student.name, 'items': []}
    for key, limit in FIELDS.items():
        payload[key] = str(form.get(key, '')).strip()
        if len(payload[key]) > limit: raise ValueError('输入内容过长，请缩短后重试。')
    try: date.fromisoformat(payload['date'])
    except ValueError: raise ValueError('请填写有效的沟通日期。') from None
    for section in SECTIONS:
        names, feedbacks = form.getlist(section+'_name'), form.getlist(section+'_feedback')
        if len(names) != len(feedbacks): raise ValueError('项目内容不完整，请刷新后重试。')
        for name, feedback in zip(names, feedbacks):
            name, feedback = str(name).strip(), str(feedback).strip()
            if len(name)>1000 or len(feedback)>20000: raise ValueError('单条项目内容过长。')
            if name or feedback: payload['items'].append(dict(section=section, item_name=name, feedback=feedback))
    if len(payload['items'])>100: raise ValueError('项目条目不能超过100条。')
    return payload


def fingerprint(payload):
    normalized = {key: compact(payload.get(key, '')) for key in ['student_name', *FIELDS]}
    normalized['items'] = [{key:compact(item.get(key,'')) for key in ['section','item_name','feedback']} for item in payload['items']]
    return hashlib.sha256(json.dumps(normalized,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def fill_record(record, payload):
    for key in FIELDS:
        setattr(record,key,date.fromisoformat(payload[key]) if key=='date' else payload[key])
    record.items = [models.CommunicationItem(**item,sort_order=index) for index,item in enumerate(payload['items'])]
    record.updated_at = datetime.utcnow()


def editor(request, student, payload=None, record=None, error='', token=None, source_name='', status_code=200):
    payload = payload or dict(student_name=student.name,date=date.today().isoformat(),grade_snapshot=student.grade,items=[])
    return page(request,'communication_form.html',status_code=status_code,student=student,payload=payload,
        record=record,error=error,sections=SECTIONS,token=token,source_name=source_name,
        name_mismatch=bool(source_name and compact(source_name)!=compact(student.name)))


@router.get('/students/{student_id}/communications/new')
def new_communication(student_id:int,request:Request,db:Session=Depends(get_db)):
    return editor(request,student_or_404(db,student_id))


@router.post('/students/{student_id}/communications/new')
async def create_communication(student_id:int,request:Request,db:Session=Depends(get_db)):
    student=student_or_404(db,student_id)
    form=await request.form()
    try: payload=form_payload(form,student)
    except ValueError as exc: return editor(request,student,error=str(exc),status_code=400)
    record=models.Communication(student_id=student_id,created_by_user_id=request.state.user.id,contact_person='')
    fill_record(record,payload);db.add(record);db.commit()
    return back(student_id)


@router.get('/students/{student_id}/communications/import')
def import_page(student_id:int,request:Request,db:Session=Depends(get_db)):
    return page(request,'communication_import.html',student=student_or_404(db,student_id),error='')


@router.post('/students/{student_id}/communications/import/preview')
async def preview_pdf(student_id:int,request:Request,file:UploadFile=File(...),db:Session=Depends(get_db)):
    student=student_or_404(db,student_id)
    try:
        if file.content_type not in ('application/pdf','application/octet-stream'):
            raise ValueError('请上传PDF文件。')
        payload=parse_communication_pdf(await file.read(MAX_PDF_BYTES+1))
    except ValueError as exc:
        return page(request,'communication_import.html',status_code=400,student=student,error=str(exc))
    finally:
        await file.close()
    token=save_preview(db,request.state.user.id,'communication',payload,student_id)
    db.commit()
    return editor(request,student,payload,token=token,source_name=payload['student_name'])


@router.post('/students/{student_id}/communications/import/confirm')
async def confirm_pdf(student_id:int,request:Request,db:Session=Depends(get_db)):
    student=student_or_404(db,student_id)
    form=await request.form();token=str(form.get('token',''))
    original=consume_preview(db,token,request.state.user.id,'communication',student_id)
    try:
        if compact(original['student_name'])!=compact(student.name) and form.get('confirm_name')!='yes':
            raise ValueError('PDF姓名与当前学生不同，请核对并明确确认归属。')
        payload=form_payload(form,student)
    except ValueError as exc:
        db.rollback()
        return editor(request,student,original,error=str(exc),token=token,source_name=original['student_name'],status_code=400)
    content_hash=fingerprint(payload)
    if db.query(models.Communication).filter_by(student_id=student_id,import_fingerprint=content_hash).first():
        db.commit()
        return page(request,'communication_import.html',status_code=409,student=student,error='这份沟通记录已经导入，未重复保存。')
    record=models.Communication(student_id=student_id,created_by_user_id=request.state.user.id,import_fingerprint=content_hash,contact_person='')
    fill_record(record,payload);db.add(record)
    try: db.commit()
    except IntegrityError:
        db.rollback()
        return page(request,'communication_import.html',status_code=409,student=student,error='这份沟通记录已经导入，未重复保存。')
    return back(student_id)


@router.post('/students/{student_id}/communications/import/cancel')
async def cancel_pdf(student_id:int,request:Request,db:Session=Depends(get_db)):
    form=await request.form()
    consume_preview(db,str(form.get('token','')),request.state.user.id,'communication',student_id)
    db.commit();return back(student_id)


@router.get('/students/{student_id}/communications/{communication_id}/edit')
def edit_communication(student_id:int,communication_id:int,request:Request,db:Session=Depends(get_db)):
    student=student_or_404(db,student_id);record=record_or_404(db,student_id,communication_id)
    return editor(request,student,communication_payload(record,student),record=record)


@router.post('/students/{student_id}/communications/{communication_id}/edit')
async def save_communication(student_id:int,communication_id:int,request:Request,db:Session=Depends(get_db)):
    student=student_or_404(db,student_id);record=record_or_404(db,student_id,communication_id)
    form=await request.form()
    try: payload=form_payload(form,student)
    except ValueError as exc: return editor(request,student,communication_payload(record,student),record,error=str(exc),status_code=400)
    fill_record(record,payload)
    db.commit();return back(student_id)


@router.post('/students/{student_id}/communications/{communication_id}/delete')
def delete_communication(student_id:int,communication_id:int,request:Request,db:Session=Depends(get_db)):
    record=record_or_404(db,student_id,communication_id)
    db.delete(record);db.commit();return back(student_id)


@router.get('/students/{student_id}/communications/{communication_id}.pdf')
def export_communication(student_id:int,communication_id:int,request:Request,db:Session=Depends(get_db)):
    student=student_or_404(db,student_id);record=record_or_404(db,student_id,communication_id)
    return pdf_response(communication_pdf(communication_payload(record,student)),f'{student.name}-沟通记录-{communication_id}.pdf')


@router.get('/students/{student_id}/communications/{communication_id}')
def view_communication(student_id:int,communication_id:int,request:Request,db:Session=Depends(get_db)):
    student=student_or_404(db,student_id);record=record_or_404(db,student_id,communication_id)
    return page(request,'communication_detail.html',student=student,record=record,payload=communication_payload(record,student),sections=SECTIONS)


def pdf_response(content,filename,extra=None):
    return Response(content,media_type='application/pdf',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe=''),
        'Cache-Control':'no-store',**(extra or {})})


def source_hash(student):
    def fields(record):
        return {c.key:str(getattr(record,c.key)) for c in inspect(type(record)).columns}
    data={'student':fields(student)}
    for relation in ['applications','courses','exams','projects','tasks','timelines','communications','activities','third_party_interviews','school_meetings','training_records']:
        data[relation]=[fields(record) for record in sorted(getattr(student,relation),key=lambda r:r.id)]
    data['items']=[fields(item) for record in sorted(student.communications,key=lambda r:r.id) for item in record.items]
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


@router.get('/students/{student_id}/archive.pdf')
def export_archive(student_id:int,request:Request,db:Session=Depends(get_db)):
    # A stable transaction keeps the exported sections and deletion receipt consistent.
    db.connection(execution_options={'isolation_level':'REPEATABLE READ'})
    student=student_or_404(db,student_id)
    archive_id=uuid.uuid4().hex;now=datetime.utcnow()
    timelines=db.query(models.Timeline).filter(or_(models.Timeline.student_id==student_id,models.Timeline.student_id.is_(None))).all()
    content=archive_pdf(student,timelines,archive_id,now)
    checksum=hashlib.sha256(content).hexdigest()
    db.add(models.ArchiveExport(id=archive_id,student_id=student_id,user_id=request.state.user.id,sha256=checksum,source_hash=source_hash(student)))
    db.add(models.AuditLog(username=request.state.user.username,action=f'archive export student_id={student_id}; archive_id={archive_id}; sha256={checksum}',status=200))
    db.commit()
    return pdf_response(content,f'{student.name}-综合归档.pdf',{'X-Archive-ID':archive_id,'X-Content-SHA256':checksum})


def require_admin(request):
    if request.state.user.role!='admin': raise HTTPException(403,'只有管理员可删除学生线上档案。')


@router.get('/students/{student_id}/archive-delete')
def delete_page(student_id:int,request:Request,db:Session=Depends(get_db)):
    require_admin(request);student=student_or_404(db,student_id)
    receipt=db.query(models.ArchiveExport).filter_by(student_id=student_id).order_by(models.ArchiveExport.created_at.desc()).first()
    return page(request,'archive_delete.html',student=student,receipt=receipt,error='')


@router.post('/students/{student_id}/delete')
async def delete_student(student_id:int,request:Request,db:Session=Depends(get_db)):
    require_admin(request);student=student_or_404(db,student_id,lock=True);form=await request.form()
    receipt=db.query(models.ArchiveExport).filter_by(id=str(form.get('archive_id','')),student_id=student_id).first()
    error=''
    if not receipt: error='请先导出归档PDF，再核验本地文件。'
    elif form.get('student_name')!=student.name or form.get('nas_confirm')!='yes': error='请完整输入学生姓名，并确认已保存及核验NAS文件。'
    elif not hmac.compare_digest(str(form.get('sha256','')).strip().lower(),receipt.sha256): error='文件SHA-256不匹配，请重新核验本地文件。'
    elif source_hash(student)!=receipt.source_hash: error='导出后学生资料已更新，请重新导出并核验。'
    if error: return page(request,'archive_delete.html',status_code=400,student=student,receipt=receipt,error=error)
    db.add(models.AuditLog(username=request.state.user.username,action=f'archive delete student_id={student_id}; archive_id={receipt.id}; sha256={receipt.sha256}',status=200))
    # ORM delete-orphan cascade includes Timeline, despite the legacy SET NULL FK.
    db.delete(student);db.commit()
    return RedirectResponse('/students',status_code=303)
