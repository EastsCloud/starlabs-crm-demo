from datetime import date
from io import BytesIO
import uuid

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import inspect, text

from app import crud, models, schemas, school_fields
from app.database import SessionLocal, engine
from app.main import app
from app.school_records import MODELS


@pytest.fixture
def school_setup():
    with TestClient(app) as client:
        client.headers['origin'] = 'http://testserver'
        assert client.post('/login', data={'username': 'admin', 'password': 'test-only-password-93824'}).status_code == 200
        response = client.post('/students/new', data={'student_type': 'school', 'name': '中学示例学生',
            'enrollment_year': '2027', 'current_school': '示例中学', 'applying_grade': '9年级',
            'mother_info': '私密家庭联系方式', 'application_email': 'synthetic@example.com'}, follow_redirects=False)
        assert response.status_code == 303
        student_id = int(response.headers['location'].split('/')[-1])
        with SessionLocal() as db:
            other = models.Student(name='本科示例学生', grade='11年级', card_number='OLD-CARD', bank_certificate_amount='OLD-AMOUNT')
            db.add(other); db.commit(); college_id = other.id
        yield client, student_id, college_id


def test_classified_profiles_and_existing_college_preservation(school_setup):
    client, student_id, college_id = school_setup
    assert '选择学生分类' in client.get('/students/new').text
    for path in [f'/students/{student_id}', f'/students/{student_id}/edit', '/students/new?student_type=school']:
        response = client.get(path)
        assert response.status_code == 200
        assert '入学年份' in response.text and '信用卡' not in response.text and '存款证明' not in response.text
    assert '信用卡' in client.get(f'/students/{college_id}').text
    assert '本科示例学生' not in client.get('/students?student_type=school').text.split('<script>')[0]
    assert '中学示例学生' not in client.get('/students?student_type=college').text.split('<script>')[0]
    # Partial updates must not blank unrelated existing data.
    client.post(f'/students/{college_id}/edit', data={'name': '本科改名'})
    with SessionLocal() as db:
        college = db.get(models.Student, college_id)
        assert (college.student_type, college.card_number, college.bank_certificate_amount, college.grade) == ('college', 'OLD-CARD', 'OLD-AMOUNT', '11年级')
        student = db.get(models.Student, student_id)
        assert student.current_school == '示例中学' and student.enrollment_year == '2027'
        token = student.portal_token
    assert '入学年份' in client.get(f'/portal/{token}/basic').text
    response = client.post(f'/students/{student_id}/edit', data={'student_type': 'school', 'name': '保留草稿', 'enrollment_year': 'wrong'})
    assert response.status_code == 400 and '保留草稿' in response.text
    with SessionLocal() as db:
        assert db.get(models.Student, student_id).name == '中学示例学生'
    assert client.post('/students/new', data={'student_type': 'invalid', 'name': '无效分类'}).status_code == 400
    assert client.post(f'/students/{student_id}/edit', data={'student_type': 'college', 'name': '错误改类'}).status_code == 400


RECORD_CASES = [
    ('activities', {'name': '科学活动', 'activity_type': '学术', 'frequency_duration': '每周2小时', 'level': '校级'}),
    ('third-party-interviews', {'interview_type': '维立克正式面试', 'date': '2027-01-02', 'psee_score': '5', 'pwse_score': '4', 'result': '面试结果示例'}),
    ('school-meetings', {'school_name': 'Example School', 'date': '2027-01-03', 'format': '仅访校', 'notes': '访校记录示例'}),
    ('training-records', {'date': '2027-01-04', 'duration': '60分钟', 'training_type': '头脑风暴', 'method': '线上', 'notes': '培训记录示例'}),
]


@pytest.mark.parametrize('kind,data', RECORD_CASES)
def test_school_record_crud_scope_and_audit(school_setup, kind, data):
    client, student_id, college_id = school_setup
    base = f'/students/{student_id}/school-records/{kind}'
    assert client.get(base+'/new').status_code == 200
    assert client.get(f'/students/{college_id}/school-records/{kind}/new').status_code == 404
    response = client.post(base+'/new', data={**data, 'student_id': college_id}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'].endswith('#'+kind)
    with SessionLocal() as db:
        item = db.query(MODELS[kind]).one(); item_id = item.id
        assert item.student_id == student_id
        token = db.get(models.Student, student_id).portal_token
        other = models.Student(name='另一中学学生', student_type='school', enrollment_year='2028')
        db.add(other); db.commit(); other_id = other.id
    assert client.get(f'/portal/{token}/{kind}').status_code == 200
    assert client.get(base+f'/{item_id}/edit').status_code == 200
    assert client.post(f'/students/{other_id}/school-records/{kind}/{item_id}/delete').status_code == 404
    changed = {**data}
    first_field = school_fields.RECORD_FIELDS[kind][1][0]
    changed[first_field.name] = (first_field.options[-1] if first_field.options else '2027-02-01' if first_field.kind == 'date' else '修改后名称')
    assert client.post(base+f'/{item_id}/edit', data=changed).status_code == 200
    with SessionLocal() as db:
        item = db.get(MODELS[kind], item_id)
        assert str(getattr(item, first_field.name)) == changed[first_field.name]
    assert client.post(base+f'/{item_id}/delete').status_code == 200
    with SessionLocal() as db:
        assert db.query(MODELS[kind]).count() == 0
        logs = db.query(models.AuditLog).filter(models.AuditLog.action.like('%school-records%')).all()
        assert logs and any('delete' in log.action and log.status == 303 for log in logs)


def application_payload():
    result = {}
    for field in school_fields.APPLICATION_FIELDS:
        result[field.name] = ('2027-01-15' if field.kind == 'date' else 'https://example.com/application' if field.kind == 'url'
                             else field.options[0] if field.options else 'PRIVATE-CREDENTIAL' if field.private else '示例'+field.label)
    return result


def test_school_applications_all_fields_validation_and_pdf(school_setup):
    client, student_id, college_id = school_setup
    base = f'/students/{student_id}/school-records/applications'
    data = application_payload()
    assert client.get(f'/applications/new?student_id={student_id}', follow_redirects=False).headers['location'] == base+'/new'
    assert client.post(base+'/new', data={**data, 'deadline': 'bad-date'}).status_code == 400
    assert client.post(base+'/new', data=data).status_code == 200
    with SessionLocal() as db:
        item = db.query(models.Application).one(); item_id = item.id
        for field, value in data.items():
            assert str(getattr(item, field)) == value
        token = db.get(models.Student, student_id).portal_token
    for path in [f'/students/{student_id}', f'/students/{student_id}/applications', base+f'/{item_id}/edit']:
        assert client.get(path).status_code == 200
    assert 'PRIVATE-CREDENTIAL' not in client.get(f'/portal/{token}/applications').text
    assert client.get(f'/applications/{item_id}/edit', follow_redirects=False).headers['location'] == base+f'/{item_id}/edit'
    assert client.post(f'/applications/{item_id}/edit', data={'student_id': college_id, 'program_name': 'wrong'}).status_code == 400
    data['recommendations'] = '推荐信已递交'
    assert client.post(base+f'/{item_id}/edit', data=data).status_code == 200
    response = client.get(f'/students/{student_id}/archive.pdf')
    assert response.status_code == 200
    text_value = ''.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    assert '推荐信已递交' in text_value and 'SSAT' in text_value and '2027' in text_value
    assert 'PRIVATE-CREDENTIAL' not in text_value and '私密家庭联系方式' not in text_value


@pytest.mark.parametrize('name,scores,expected', [
    ('TOEFL Junior', {'listening': '280', 'language': '275', 'reading': '290'}, 'Language: 275'),
    ('SSAT', {'verbal': '750', 'quantitative': '760', 'analytical': '740'}, 'Q: 760'),
])
def test_new_exam_components_and_task_nodes(school_setup, name, scores, expected):
    client, student_id, _ = school_setup
    response = client.post('/exams/new', data={'student_id': student_id, 'exam_name': name, 'total': '845', **scores,
                           'next': f'/students/{student_id}'})
    assert response.status_code == 200 and expected in response.text
    with SessionLocal() as db:
        exam = db.query(models.Exam).one(); exam_id = exam.id
        assert exam.exam_name == name and expected in crud.exam_component_scores(exam)
    assert client.get(f'/exams/{exam_id}/edit').status_code == 200
    client.post('/tasks/new', data={'student_id': student_id, 'title': '面试准备', 'category': '面试', 'due_date': '2027-01-02', 'time_node': '8年级寒假'})
    with SessionLocal() as db:
        crud.normalize_task_time_fields(db)
        task = db.query(models.Task).one()
        assert task.time_node == '8年级寒假' and task.category == '面试'
        assert crud.time_node_start_date(task.student, '10年级上学期') is None
    form = client.get(f'/tasks/new?student_id={student_id}').text
    for node in ['申请季', '5年级', '6年级寒暑假', '7年级寒暑假', '8年级暑假', '9年级暑假']:
        assert node in form
    assert len(schemas.TIME_NODES) == len(set(schemas.TIME_NODES))


def test_new_records_invalidate_archive_and_cascade_delete(school_setup):
    client, student_id, _ = school_setup
    old_export = client.get(f'/students/{student_id}/archive.pdf')
    for kind, data in RECORD_CASES:
        assert client.post(f'/students/{student_id}/school-records/{kind}/new', data=data).status_code == 200
    form = {'archive_id': old_export.headers['x-archive-id'], 'sha256': old_export.headers['x-content-sha256'],
            'student_name': '中学示例学生', 'nas_confirm': 'yes'}
    assert client.post(f'/students/{student_id}/delete', data=form).status_code == 400
    response = client.get(f'/students/{student_id}/archive.pdf')
    pdf_text = ''.join(p.extract_text() for p in PdfReader(BytesIO(response.content)).pages)
    for value in ['科学活动', '面试结果示例', '访校记录示例', '培训记录示例']:
        assert value in pdf_text
    form.update(archive_id=response.headers['x-archive-id'], sha256=response.headers['x-content-sha256'])
    assert client.post(f'/students/{student_id}/delete', data=form, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        for kind, _ in RECORD_CASES:
            assert db.query(MODELS[kind]).count() == 0


def test_additive_migration_preserves_all_existing_columns():
    from alembic import command
    from alembic.config import Config
    schema = 'ams_old_test_'+uuid.uuid4().hex
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(text('CREATE SCHEMA '+schema))
            conn.execute(text('SET LOCAL search_path TO '+schema))
            config = Config('alembic.ini'); config.attributes['connection'] = conn
            command.upgrade(config, '20260905_02')
            conn.execute(text("INSERT INTO students(id,name,grade,card_number,notes) VALUES(1,'旧学生','11年级','KEEP-CARD','KEEP-NOTES')"))
            conn.execute(text("INSERT INTO applications(id,student_id,program_name,application_password) VALUES(1,1,'Old University','KEEP-PASSWORD')"))
            conn.execute(text("INSERT INTO exams(student_id,exam_name,total) VALUES(1,'TOEFL','100')"))
            conn.execute(text("INSERT INTO tasks(student_id,title,time_node) VALUES(1,'旧任务','11年级上学期')"))
            conn.execute(text("INSERT INTO communications(student_id,summary) VALUES(1,'旧沟通')"))
            tables = ['students', 'applications', 'exams', 'tasks', 'communications']
            snapshots = {}
            for table in tables:
                cols = ','.join('"'+c['name']+'"' for c in inspect(conn).get_columns(table))
                snapshots[table] = (cols, conn.execute(text(f'SELECT {cols} FROM {table} ORDER BY id')).all())
            command.upgrade(config, 'head'); command.upgrade(config, 'head')
            for table, (cols, rows) in snapshots.items():
                assert conn.execute(text(f'SELECT {cols} FROM {table} ORDER BY id')).all() == rows
            assert conn.execute(text('SELECT student_type FROM students')).scalar() == 'college'
            assert conn.execute(text('SELECT count(*) FROM student_activities')).scalar() == 0
        finally:
            transaction.rollback()


def test_college_excel_import_does_not_overwrite_same_name_school_student(school_setup):
    from app.importer import apply_import
    _, student_id, _ = school_setup
    with SessionLocal() as db:
        apply_import(db, {'students': [{'name': '中学示例学生', 'personal': {'phone': 'college-only'}, 'applications': [], 'exams': []}]})
        assert db.get(models.Student, student_id).phone != 'college-only'
        assert db.query(models.Student).filter_by(name='中学示例学生', student_type='college').one().phone == 'college-only'


def test_high_scores_keep_test_scales_separate_and_ignore_non_numeric_totals():
    exams = [models.Exam(exam_name=name, total=score) for name, score in [
        ('TOEFL Junior', '850'), ('TOEFL Junior', '890'), ('TOEFL', '103'),
        ('TOEFL', '待出分'), ('SSAT', '2200'), ('SSAT', 'NaN')]]
    assert crud.school_exam_high_scores(exams) == [('TOEFL Junior', '890'), ('TOEFL', '103'), ('SSAT', '2200')]
