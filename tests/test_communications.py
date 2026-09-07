from datetime import date, datetime, timedelta
from io import BytesIO
import hashlib
import re

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from app.main import app
from app.database import SessionLocal, engine
from app import models
from app.auth import hash_password
from app.communication_pdf import parse_communication_pdf, compact
from app.pdf_exports import communication_pdf

PASSWORD='test-only-password-93824'


def payload(name='示例学生'):
    return dict(student_name=name,school_snapshot='示例学校',grade_snapshot='10年级',date='2026-09-05',duration='30分钟',method='线上语音',notes='跟进课程安排。',items=[
        dict(section='academic',item_name='课程',feedback='核对课程进度。'),
        dict(section='standardized',item_name='语言',feedback='安排练习。'),
        dict(section='extracurricular',item_name='活动',feedback='记录活动目标。')])


def form_data(data):
    result={key:value for key,value in data.items() if key not in ('items','student_name')}
    for section in ['academic','standardized','extracurricular']:
        rows=[item for item in data['items'] if item['section']==section]
        result[section+'_name']=[row['item_name'] for row in rows]
        result[section+'_feedback']=[row['feedback'] for row in rows]
    return result


@pytest.fixture
def setup():
    with TestClient(app) as client:
        client.headers['origin']='http://testserver'
        client.post('/login',data={'username':'admin','password':PASSWORD})
        with SessionLocal() as db:
            student=models.Student(name='示例学生',grade='10年级',national_id_number='PRIVATE-ID-DO-NOT-EXPORT',card_number='PRIVATE-CARD-DO-NOT-EXPORT')
            db.add(student);db.commit();student_id=student.id
        yield client,student_id


def test_communication_crud_and_legacy_fields(setup):
    client,student_id=setup;root=f'/students/{student_id}/communications'
    response=client.post(root+'/new',data=form_data(payload()),follow_redirects=False)
    assert response.status_code==303 and response.headers['location'].endswith('#communications')
    with SessionLocal() as db:
        record=db.query(models.Communication).one();record_id=record.id
        assert len(record.items)==3 and record.created_by_user_id
        record.summary='旧摘要保留';record.generated_tasks='旧任务保留';db.commit()
    assert '沟通记录' in client.get(f'/students/{student_id}').text
    assert '旧摘要保留' in client.get(root+f'/{record_id}').text
    changed=payload();changed['notes']='更新后的备注';changed['items']=changed['items'][:1]
    assert client.post(root+f'/{record_id}/edit',data=form_data(changed),follow_redirects=False).status_code==303
    with SessionLocal() as db:
        record=db.get(models.Communication,record_id)
        assert record.summary=='旧摘要保留' and record.generated_tasks=='旧任务保留'
        assert record.notes=='更新后的备注' and len(record.items)==1
    pdf=client.get(root+f'/{record_id}.pdf')
    assert pdf.status_code==200 and pdf.headers['cache-control']=='no-store'
    assert 'attachment' in pdf.headers['content-disposition']
    assert '旧摘要保留' in ''.join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
    assert client.get(f'/students/{student_id+1}/communications/{record_id}').status_code==404
    assert client.post(root+f'/{record_id}/delete',follow_redirects=False).status_code==303
    with SessionLocal() as db:
        assert db.query(models.CommunicationItem).count()==0
        assert db.query(models.AuditLog).filter(models.AuditLog.action.like('%communication_id%')).count()


def preview(client,student_id,data):
    response=client.post(f'/students/{student_id}/communications/import/preview',files={'file':('any-name.pdf',communication_pdf(data),'application/pdf')})
    assert response.status_code==200
    return re.search(r'name="token" value="([a-f0-9]+)"',response.text)[1],response


def test_pdf_import_preview_name_confirmation_dedup_and_cancel(setup):
    client,student_id=setup;root=f'/students/{student_id}/communications/import'
    data=payload('另一位示例学生');token,response=preview(client,student_id,data)
    assert '不一致' in response.text
    with SessionLocal() as db:
        assert db.query(models.Communication).count()==0
        saved=db.query(models.ImportPreview).one()
        assert '示例学校' not in saved.encrypted_payload
    form={**form_data(data),'token':token}
    assert client.post(root+'/confirm',data=form).status_code==400
    form['confirm_name']='yes'
    assert client.post(root+'/confirm',data=form,follow_redirects=False).status_code==303
    assert client.post(root+'/confirm',data=form).status_code==410
    token,_=preview(client,student_id,data);form['token']=token
    assert client.post(root+'/confirm',data=form).status_code==409
    with SessionLocal() as db: assert db.query(models.Communication).count()==1
    token,_=preview(client,student_id,payload())
    assert client.post(root+'/cancel',data={'token':token},follow_redirects=False).status_code==303
    assert client.post(root+'/confirm',data={**form_data(payload()),'token':token}).status_code==410


def test_preview_user_binding_and_expiry(setup):
    admin,student_id=setup;token,_=preview(admin,student_id,payload())
    with SessionLocal() as db:
        db.add(models.User(username='member-pdf',role='member',password_hash=hash_password(PASSWORD)));db.commit()
    with TestClient(app) as member:
        member.headers['origin']='http://testserver';member.post('/login',data={'username':'member-pdf','password':PASSWORD})
        assert member.post(f'/students/{student_id}/communications/import/confirm',data={**form_data(payload()),'token':token}).status_code==410
        assert member.get(f'/students/{student_id}/archive-delete').status_code==403
        assert member.post(f'/students/{student_id}/delete',data={}).status_code==403
    with SessionLocal() as db:
        db.query(models.ImportPreview).update({'expires_at':datetime.utcnow()-timedelta(seconds=1)});db.commit()
    assert admin.post(f'/students/{student_id}/communications/import/confirm',data={**form_data(payload()),'token':token}).status_code==410


def test_pdf_parser_roundtrip_and_invalid_files():
    data=payload();parsed=parse_communication_pdf(communication_pdf(data))
    assert parsed['student_name']==data['student_name'] and parsed['date']==data['date']
    assert [compact(i['feedback']) for i in parsed['items']]==[compact(i['feedback']) for i in data['items']]
    for content in [b'not pdf', b'%PDF-'+b'x'*(5*1024*1024), b'%PDF-1.7 broken']:
        with pytest.raises(ValueError): parse_communication_pdf(content)
    writer=PdfWriter();writer.add_blank_page(width=595,height=842);out=BytesIO();writer.write(out)
    with pytest.raises(ValueError,match='扫描件'): parse_communication_pdf(out.getvalue())
    writer.encrypt('test-password');out=BytesIO();writer.write(out)
    with pytest.raises(ValueError,match='加密'): parse_communication_pdf(out.getvalue())
    writer=PdfWriter()
    for _ in range(11): writer.add_blank_page(width=595,height=842)
    out=BytesIO();writer.write(out)
    with pytest.raises(ValueError,match='10页'): parse_communication_pdf(out.getvalue())


def test_archive_safe_content_and_independent_verified_delete(setup):
    client,student_id=setup
    with SessionLocal() as db:
        db.add_all([models.Application(student_id=student_id,program_name='申请测试大学',portal_password='NEVER-EXPORT-PORTAL',application_password='NEVER-EXPORT-LOGIN',portal_a1='NEVER-EXPORT-ANSWER'),
            models.Course(student_id=student_id,name='课程记录'),models.Exam(student_id=student_id,exam_name='SAT'),
            models.Project(student_id=student_id,project_name='活动规划'),models.Task(student_id=student_id,title='已完成任务',status='已完成'),
            models.Timeline(student_id=student_id,content='个人时间点'),models.Timeline(content='公共时间点')]);db.commit()
    client.post(f'/students/{student_id}/communications/new',data=form_data(payload()))
    response=client.get(f'/students/{student_id}/archive.pdf')
    assert response.status_code==200
    text=''.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    for expected in ['基本信息','申请测试大学','选课信息','标化记录','活动规划','已完成任务','个人时间点','公共时间点','全部沟通记录','核对课程进度']:
        assert expected in text
    for secret in ['NEVER-EXPORT','PRIVATE-ID','PRIVATE-CARD']: assert secret not in text
    with SessionLocal() as db: assert db.get(models.Student,student_id) is not None
    checksum=hashlib.sha256(response.content).hexdigest()
    assert checksum==response.headers['x-content-sha256']
    form={'archive_id':response.headers['x-archive-id'],'student_name':'示例学生','nas_confirm':'yes','sha256':checksum}
    assert client.post(f'/students/{student_id}/delete',data={**form,'sha256':'0'*64}).status_code==400
    with SessionLocal() as db:
        db.get(models.Student,student_id).notes='归档后修改';db.commit()
    assert client.post(f'/students/{student_id}/delete',data=form).status_code==400
    response=client.get(f'/students/{student_id}/archive.pdf')
    form.update(archive_id=response.headers['x-archive-id'],sha256=response.headers['x-content-sha256'])
    assert client.post(f'/students/{student_id}/delete',data=form,follow_redirects=False).status_code==303
    with SessionLocal() as db:
        assert db.get(models.Student,student_id) is None
        for model in [models.Communication,models.CommunicationItem,models.Application,models.Course,models.Exam,models.Project,models.Task,models.ArchiveExport]:
            assert db.query(model).count()==0
        assert db.query(models.Timeline).one().content=='公共时间点'
        assert db.query(models.AuditLog).filter(models.AuditLog.action.like('archive delete%')).count()==1


def test_migrations_match_models_and_are_repeatable():
    from alembic import command
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.autogenerate import compare_metadata
    command.upgrade(Config('alembic.ini'),'head')
    with engine.connect() as connection:
        diffs=compare_metadata(MigrationContext.configure(connection),models.Base.metadata)
        assert not diffs


def test_long_pdf_preserves_all_text_across_page_boundaries():
    data=payload()
    for item in data['items']: item['feedback']='中文长段落换行测试，确认跨页内容完整且表头重复。'*140
    data['notes']='备注跨页内容完整性检查。'*300
    content=communication_pdf(data)
    assert len(PdfReader(BytesIO(content)).pages)>4
    parsed=parse_communication_pdf(content)
    assert [compact(i['feedback']) for i in parsed['items']]==[compact(i['feedback']) for i in data['items']]
    assert compact(parsed['notes'])==compact(data['notes'])


def test_rejected_pdf_upload_does_not_write_preview(setup):
    client,student_id=setup
    for mime,content in [('image/png',b'%PDF-'),('application/pdf',b'invalid')]:
        response=client.post(f'/students/{student_id}/communications/import/preview',files={'file':('input.pdf',content,mime)})
        assert response.status_code==400
    with SessionLocal() as db:
        assert db.query(models.ImportPreview).count()==0
        assert db.query(models.Communication).count()==0


def test_existing_postgres_schema_upgrade_preserves_legacy_communications():
    import uuid
    from sqlalchemy import text, inspect
    from alembic import command
    from alembic.config import Config
    schema='ams_legacy_test_'+uuid.uuid4().hex
    with engine.connect() as connection:
        transaction=connection.begin()
        try:
            connection.execute(text('CREATE SCHEMA '+schema))
            connection.execute(text('SET LOCAL search_path TO '+schema))
            config=Config('alembic.ini');config.attributes['connection']=connection
            command.upgrade(config,'20260905_01')
            connection.execute(text("INSERT INTO students (id,name,card_cvv) VALUES (1,'旧库示例学生','unused-test-code')"))
            connection.execute(text("INSERT INTO communications (student_id,summary,generated_tasks) VALUES (1,'旧库摘要保留','旧库任务保留')"))
            command.upgrade(config,'head')
            row=connection.execute(text('SELECT summary,generated_tasks,notes FROM communications')).one()
            assert row==('旧库摘要保留','旧库任务保留','')
            assert 'card_cvv' not in {column['name'] for column in inspect(connection).get_columns('students')}
        finally:
            transaction.rollback()  # Includes the disposable schema itself.


def test_database_configuration_fails_closed():
    import os,subprocess,sys
    from pathlib import Path
    env=os.environ.copy();env['PYTHONPATH']=os.pathsep.join(sys.path)
    for url in ['', 'sqlite:///:memory:']:
        env['DATABASE_URL']=url
        result=subprocess.run([sys.executable,'-c','import app.database'],env=env,capture_output=True,text=True)
        assert result.returncode!=0 and 'DATABASE_URL' in result.stderr
    env.pop('TEST_DATABASE_URL',None)
    result=subprocess.run([sys.executable,'-c',"import runpy; runpy.run_path('tests/conftest.py')"],env=env,capture_output=True,text=True)
    assert result.returncode!=0 and 'TEST_DATABASE_URL is required' in result.stderr
