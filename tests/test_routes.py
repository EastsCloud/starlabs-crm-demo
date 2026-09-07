import os
import re
import tempfile
import uuid
from datetime import date
from pathlib import Path



from fastapi.testclient import TestClient  # noqa: E402


class AuthenticatedClient(TestClient):
    def __enter__(self):
        super().__enter__()
        self.headers['origin'] = 'http://testserver'
        response = self.post('/login', data={'username': 'admin', 'password': os.environ['ADMIN_INITIAL_PASSWORD']}, follow_redirects=False)
        assert response.status_code == 303
        return self


TestClient = AuthenticatedClient

from app.database import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Application, DataSource, Exam, ImportantItem, PublicEvent, ReferenceItem, Student, Task, Timeline  # noqa: E402


def test_new_pages_render_with_temporary_database():
    init_db()
    db = SessionLocal()
    try:
        student = Student(name="页面测试", phone="123456", final_school="Example University", target_school="Target U", target_major="Biology", card_number="4111111111111111", card_expiry="12/30")
        db.add(student)
        db.flush()
        application = Application(student_id=student.id, program_name="Example University", status="Accepted", form_status="WIP", portal_material_progress="0%", score_delivery_status="WIP")
        db.add(application)
        task = Task(student_id=student.id, title="状态循环测试", status="WIP", category="其他")
        db.add(task)
        source = DataSource(name="测试官网", category="竞赛", url="https://example.com")
        db.add(source)
        db.flush()
        event = PublicEvent(source_id=source.id, title="报名截止", category="竞赛", event_date=date.today(), source_url=source.url)
        db.add(event)
        db.commit()
        token = student.portal_token
        student_id = student.id
        task_id = task.id
        event_id = event.id
        source_id = source.id
        application_id = application.id
        assert db.query(ReferenceItem).count() > 0
    finally:
        db.close()

    with TestClient(app) as client:
        for path in ["/", "/important-items", "/important-items/new", f"/students/{student_id}", "/statistics", "/statistics?school=Example", "/database", "/tasks", f"/tasks/new?student_id={student_id}", "/import", "/import/template", "/data-engine", "/data-engine/sources/new", f"/data-engine/sources/{source_id}/edit", f"/portal/{token}/basic"]:
            response = client.get(path)
            assert response.status_code == 200
        engine_html = client.get("/data-engine").text
        assert "新增数据源" in engine_html
        assert "添加固定官网" not in engine_html
        assert 'placeholder="关键词搜索"' in engine_html
        assert ">抓取<" in engine_html and ">编辑<" in engine_html and ">删除<" in engine_html
        filtered_engine = client.get("/data-engine?q=报名").text
        assert "报名截止" in filtered_engine
        assert "没有匹配的数据源" in filtered_engine
        assert "报名截止" not in client.get("/data-engine?category=考试").text
        detail_html = client.get(f"/students/{student_id}").text
        assert "基本信息" in detail_html
        assert "目标学校" in detail_html and "目标方向" in detail_html
        assert "信用卡卡主姓名" in detail_html and "存款证明金额（美元）" in detail_html
        assert detail_html.count('class="sensitive-toggle"') == 2
        assert "返回列表" not in detail_html
        assert "查看全部" not in detail_html
        assert "数据统计" in client.get("/statistics").text
        database_html = client.get("/database").text
        assert ">AP<" in database_html
        assert "学校清单合集" not in database_html
        assert 'action="/admin/reset-database"' not in database_html
        assert 'action="/admin/load-test-data"' not in database_html
        assert 'class="loading-overlay"' in database_html
        template_response = client.get("/import/template")
        assert template_response.content == (Path(__file__).parents[1] / "docs" / "information_template.xlsx").read_bytes()
        dashboard_html = client.get("/").text
        for label in ["admin", "近期重点", "重要事项", "学生", "任务", "导入学生"]:
            assert label in dashboard_html
        assert "规划矩阵" not in dashboard_html
        created = client.post("/important-items", data={"item_type": "竞赛", "content": "测试截止日", "participants": "页面测试", "date": date.today().isoformat(), "reminder": "7天、1天"}, follow_redirects=False)
        assert created.status_code == 303
        assert "测试截止日" in client.get("/important-items").text
        assert "测试截止日" in client.get("/").text
        assert "测试截止日" in client.get("/important-items?q=截止日&item_type=竞赛").text
        assert "测试截止日" not in client.get("/important-items?item_type=考试").text
        with SessionLocal() as db:
            important_id = db.query(ImportantItem).filter(ImportantItem.content == "测试截止日").one().id
        portal_html = client.get(f"/portal/{token}/basic").text
        assert f"<title>{student.name}</title>" in portal_html
        assert "· 只读" not in portal_html
        assert client.post(f"/tasks/{task_id}/status", follow_redirects=False).status_code == 303
        assert client.post(f"/data-engine/events/{event_id}/timeline", follow_redirects=False).status_code == 303
        assert client.post(f"/data-engine/events/{event_id}/tasks", data={"student_id": str(student_id)}, follow_redirects=False).status_code == 303
        important_prefill = client.get(f"/data-engine/events/{event_id}/important-item")
        assert "报名截止" in important_prefill.text
        assert date.today().isoformat() in important_prefill.text

        with SessionLocal() as db:
            public_point = db.query(Timeline).filter(Timeline.source_event_id == event_id).first()
            assert public_point is not None
            assert public_point.student_id is None
            assert public_point.owner == "公共"

        edit_page = client.get(f"/important-items/{important_id}/edit")
        assert edit_page.status_code == 200
        assert "测试截止日" in edit_page.text
        updated = client.post(f"/important-items/{important_id}/edit", data={"item_type": "考试", "content": "已编辑重要事项", "date": date.today().isoformat(), "participants": "测试参与人", "reminder": "1天"}, follow_redirects=False)
        assert updated.status_code == 303
        assert "已编辑重要事项" in client.get("/important-items").text
        assert client.post(f"/applications/{application_id}/expand/online", data={"next": f"/students/{student_id}"}, follow_redirects=False).status_code == 303
        assert client.get(f"/students/{student_id}").text.count("填表状态") >= 1
        assert client.post(f"/applications/{application_id}/quick/form_status", data={"next": f"/students/{student_id}"}, follow_redirects=False).status_code == 303
        db = SessionLocal()
        try:
            assert db.get(Task, task_id).status == "待确认"
            assert db.query(Timeline).filter(Timeline.student_id.is_(None), Timeline.source_event_id == event_id).count() == 1
            assert db.query(Task).filter(Task.student_id == student_id, Task.title == "报名截止").count() == 1
            application = db.get(Application, application_id)
            assert application.online_details_expanded is True
            assert application.form_status == "待提交"
            assert db.query(ImportantItem).filter(ImportantItem.content == "已编辑重要事项").count() == 1
        finally:
            db.close()

        assert client.post(f"/data-engine/sources/{source_id}/delete", data={"next": "/data-engine"}, follow_redirects=False).status_code == 303
        with SessionLocal() as db:
            assert db.get(DataSource, source_id) is None
            assert db.get(PublicEvent, event_id) is None


def test_import_route_previews_multiple_files_and_is_idempotent():
    init_db()
    workbook_path = Path(__file__).parent / "fixtures" / "bulk_test_template.xlsx"
    workbook_bytes = workbook_path.read_bytes()
    db = SessionLocal()
    try:
        existing = db.query(Student).filter(Student.name == "批量测试学生01").first()
        if existing is None:
            existing = Student(name="批量测试学生01", grade="12年级")
            db.add(existing)
        existing.phone = "系统原电话"
        db.commit()
    finally:
        db.close()

    def preview_and_apply(client):
        response = client.post("/import/preview", files=[
            ("files", ("first.xlsx", workbook_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("files", ("second.xlsx", workbook_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
        ])
        assert response.status_code == 200
        assert "已解析 2 个文件" in response.text
        assert "共识别 24 位学生" in response.text
        assert "重复学生已按 A2 姓名合并" not in response.text
        assert "（A2）" not in response.text
        assert "已有学生，将按勾选内容增加或覆盖信息" in response.text
        token = re.search(r'name="token" value="([0-9a-f]{32})"', response.text).group(1)
        applied = client.post("/import/apply", data={"token": token})
        assert applied.status_code == 200

    with TestClient(app) as client:
        import_html = client.get("/import").text
        assert import_html.index("提交后会先显示预览") < import_html.index('type="file"')
        assert 'data-loading="正在解析学生文件并生成预览…"' in import_html
        preview_and_apply(client)
        db = SessionLocal()
        try:
            existing = db.query(Student).filter(Student.name == "批量测试学生01").one()
            assert existing.phone == "13988100001"
            assert existing.address == "测试城市第1区国际教育路101号"
            imported = db.query(Student).filter(Student.name == "批量测试学生02").one()
            first_counts = (db.query(Application).filter(Application.student_id == imported.id).count(), db.query(Exam).filter(Exam.student_id == imported.id).count())
        finally:
            db.close()
        preview_and_apply(client)
        db = SessionLocal()
        try:
            imported = db.query(Student).filter(Student.name == "批量测试学生02").one()
            assert (db.query(Application).filter(Application.student_id == imported.id).count(), db.query(Exam).filter(Exam.student_id == imported.id).count()) == first_counts
        finally:
            db.close()


def test_removed_database_mutation_routes_are_unavailable():
    with TestClient(app) as client:
        assert client.post('/admin/load-test-data', follow_redirects=False).status_code == 404
        assert client.post('/admin/reset-database', follow_redirects=False).status_code == 404
