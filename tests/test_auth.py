from datetime import datetime, timedelta
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import User, AuditLog, LoginSession, RememberedLogin
from app.auth import hash_password, digest, COOKIE, REMEMBER_COOKIE, SESSION_SECONDS

PASSWORD = 'test-only-password-93824'


def login(client, username='admin', password=PASSWORD, remember=False):
    client.headers['origin'] = 'http://testserver'
    return client.post('/login', data={'username': username, 'password': password, **({'remember': '1'} if remember else {})}, follow_redirects=False)


def test_anonymous_and_csrf_and_remember():
    with TestClient(app) as client:
        for path in ['/', '/students', '/tasks', '/import/template', '/portal/example/tasks', '/docs', '/accounts']:
            assert client.get(path, follow_redirects=False).status_code == 303
        assert client.get('/healthz').status_code == 200
        assert client.post('/login', data={'username': 'admin', 'password': PASSWORD}).status_code == 403
        response = login(client, remember=True)
        assert response.status_code == 303
        assert 'HttpOnly' in response.headers['set-cookie']
        assert 'Max-Age=18000' in response.headers['set-cookie']
        assert 'Max-Age=2592000' in response.headers['set-cookie']
        assert 'expires=' in response.headers['set-cookie'].lower()
        remembered_token = client.cookies.get('ams_session')
        with TestClient(app) as reopened_browser:
            reopened_browser.cookies.set('ams_session', remembered_token)
            assert reopened_browser.get('/students').status_code == 200
        html = client.get('/login').text
        assert '正在自动登录' not in html and 'name="resume"' not in html
        assert 'value="admin"' in html and f'value="{PASSWORD}"' in html
        assert '<script>' not in html
        assert client.get('/students').status_code == 200
        accounts_html = client.get('/accounts').text
        assert '管理员账号' in accounts_html and '人员列表' in accounts_html and '操作日志' in accounts_html
        assert '启用' not in accounts_html and '加载测试数据' not in accounts_html
        assert 'data-show-account="add-person-row"' in accounts_html
        assert '/static/starlabs.jpg' in accounts_html
        assert client.post('/logout', headers={'origin': 'https://evil.example'}).status_code == 403
        assert client.post('/logout').status_code == 200
        assert client.get('/', follow_redirects=False).status_code == 303
        assert f'value="{PASSWORD}"' in client.get('/login').text


def test_member_permissions_account_reset_and_audit():
    with TestClient(app) as admin, TestClient(app) as member:
        login(admin)
        with SessionLocal() as db:
            old = db.query(User).filter_by(username='member-test').first()
            if not old:
                db.add(User(username='member-test', password_hash=hash_password(PASSWORD), role='member'))
                db.commit()
            user_id = db.query(User).filter_by(username='member-test').one().id
        assert login(member, 'member-test', remember=True).status_code == 303
        assert member.get('/accounts').status_code == 403
        assert member.post('/accounts', data={}).status_code == 403
        assert member.get('/tasks').status_code == 200
        result = admin.post('/accounts', data={'user_id': user_id, 'username': 'member-test', 'password': 'a-new-password-43243', 'role': 'member', 'active': '1'}, follow_redirects=False)
        assert result.status_code == 303
        assert member.get('/tasks', follow_redirects=False).status_code == 303
        with SessionLocal() as db:
            assert db.query(AuditLog).filter_by(username='member-test', action='GET /tasks').count()
            assert db.query(LoginSession).filter_by(user_id=user_id).count() == 0
            assert db.query(RememberedLogin).filter_by(user_id=user_id).count() == 0
            assert all('a-new-password-43243' not in row.action for row in db.query(AuditLog).all())


def test_login_throttle():
    with TestClient(app) as client:
        for _ in range(5):
            login(client, 'nonexistent-throttle', 'wrong')
        response = login(client, 'nonexistent-throttle', 'wrong')
        assert '15分钟' in response.text


@pytest.mark.parametrize('remember', [False, True])
def test_session_fixed_five_hours_and_refresh(remember):
    with TestClient(app) as client:
        before = datetime.utcnow()
        login(client, remember=remember)
        token = client.cookies.get(COOKIE)
        with SessionLocal() as db:
            session = db.get(LoginSession, digest(token))
            deadline = session.expires_at
            assert before + timedelta(seconds=SESSION_SECONDS) <= deadline <= datetime.utcnow() + timedelta(seconds=SESSION_SECONDS)
        for _ in range(2):
            response = client.get('/tasks?owner=学生', follow_redirects=False)
            assert response.status_code == 200
            assert 'sessionExpiry' in response.text
        with SessionLocal() as db:
            session = db.get(LoginSession, digest(token))
            assert session.expires_at == deadline  # Refresh must not extend the session.
            session.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        response = client.get('/tasks', follow_redirects=False)
        assert response.status_code == 303 and response.headers['location'] == '/login'
        assert response.headers['cache-control'] == 'no-store'
        with SessionLocal() as db:
            assert db.get(LoginSession, digest(token)) is None
        assert (f'value="{PASSWORD}"' in client.get('/login').text) == remember


def test_remember_only_prefills_and_can_be_cleared():
    with TestClient(app) as client, TestClient(app) as reopened:
        login(client, remember=True)
        token = client.cookies.get(REMEMBER_COOKIE)
        with SessionLocal() as db:
            saved = db.get(RememberedLogin, digest(token))
            assert PASSWORD not in saved.encrypted_password
            assert saved.token_hash != token
        reopened.cookies.set(REMEMBER_COOKIE, token, domain='testserver.local', path='/')
        assert reopened.get('/students', follow_redirects=False).status_code == 303
        assert f'value="{PASSWORD}"' in reopened.get('/login').text
        assert reopened.post('/login', data={'resume': '1'}, headers={'origin': 'http://testserver'}, follow_redirects=False).status_code == 401
        login(reopened, remember=False)
        assert not reopened.cookies.get(REMEMBER_COOKIE)
        assert f'value="{PASSWORD}"' not in reopened.get('/login').text
        with SessionLocal() as db:
            assert db.get(RememberedLogin, digest(token)) is None


def test_expired_and_tampered_remember_information():
    with TestClient(app) as client:
        login(client, remember=True)
        token = client.cookies.get(REMEMBER_COOKIE)
        with SessionLocal() as db:
            db.get(RememberedLogin, digest(token)).encrypted_password = 'tampered'
            db.commit()
        assert f'value="{PASSWORD}"' not in client.get('/login').text
        login(client, remember=True)
        token = client.cookies.get(REMEMBER_COOKIE)
        with SessionLocal() as db:
            db.get(RememberedLogin, digest(token)).expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        assert f'value="{PASSWORD}"' not in client.get('/login').text


class AccountMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = {}
        self.row = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'tr':
            self.row = attrs.get('id')
            if self.row:
                self.rows[self.row] = [(tag, attrs)]
        elif self.row:
            self.rows[self.row].append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == 'tr':
            self.row = None


def test_person_rows_and_validation_state():
    with TestClient(app) as client:
        login(client)
        result = client.post('/accounts', data={'username': 'row-test', 'password': PASSWORD, 'kind': 'person'}, follow_redirects=False)
        assert result.status_code == 303
        with SessionLocal() as db:
            user_id = db.query(User).filter_by(username='row-test').one().id
        rows = AccountMarkup(client.get('/accounts').text).rows
        assert 'hidden' in rows['add-person-row'][0][1]
        editors = [attrs for tag, attrs in rows[f'person-{user_id}'] if 'data-account-editor' in attrs]
        assert editors and all('hidden' in attrs for attrs in editors)
        assert f'person-edit-{user_id}' not in rows  # One physical row per person.
        rows = AccountMarkup(client.get(f'/accounts?edit={user_id}').text).rows
        assert all('hidden' not in attrs for tag, attrs in rows[f'person-{user_id}'] if 'data-account-editor' in attrs)
        result = client.post('/accounts', data={'user_id': user_id, 'username': 'admin', 'password': '', 'kind': 'person'})
        assert result.status_code == 400
        rows = AccountMarkup(result.text).rows
        assert 'hidden' in rows['add-person-row'][0][1]
        assert all('hidden' not in attrs for tag, attrs in rows[f'person-{user_id}'] if 'data-account-editor' in attrs)
        result = client.post('/accounts', data={'username': 'draft-person', 'password': 'short', 'kind': 'person'})
        assert result.status_code == 400 and 'value="draft-person"' in result.text
        assert 'hidden' not in AccountMarkup(result.text).rows['add-person-row'][0][1]
        result = client.post('/accounts', data={'user_id': user_id, 'username': 'renamed-row', 'password': '', 'kind': 'person'}, follow_redirects=False)
        assert result.status_code == 303
        assert login(client, 'renamed-row').status_code == 303
