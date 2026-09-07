"""Database-backed sessions, account administration and request audit trail."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import schemas
from app.database import SessionLocal
from app.models import User, LoginSession, RememberedLogin, AuditLog, LoginAttempt

router = APIRouter()
views = Jinja2Templates(directory='app/templates')
COOKIE = 'ams_session'
REMEMBER_COOKIE = 'ams_remember'
SESSION_SECONDS = 5 * 3600
REMEMBER_SECONDS = 30 * 86400


@lru_cache(maxsize=1)
def remember_cipher():
    key = os.environ.get('REMEMBER_ENCRYPTION_KEY')
    if not key:
        raise RuntimeError('REMEMBER_ENCRYPTION_KEY is required.')
    return Fernet(key)


def set_login_cookie(response, name, token, seconds):
    response.set_cookie(
        name, token, httponly=True,
        secure=os.environ.get('COOKIE_SECURE', '1') == '1',
        samesite='lax', path='/', max_age=seconds,
        expires=datetime.now(timezone.utc) + timedelta(seconds=seconds),
    )


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password):
    salt = secrets.token_hex(16)
    result = hashlib.scrypt(password.encode(), salt=salt.encode(), n=32768, r=8, p=3, maxmem=64 * 1024 * 1024).hex()
    return f'{salt}:{result}'


def verify(password, encoded):
    salt, expected = encoded.split(':')
    actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=32768, r=8, p=3, maxmem=64 * 1024 * 1024).hex()
    return hmac.compare_digest(expected, actual)


def bootstrap():
    remember_cipher()
    with SessionLocal() as db:
        if db.query(User).count():
            return
        password = os.environ.get('ADMIN_INITIAL_PASSWORD', '')
        if len(password) < 12:
            raise RuntimeError('Set ADMIN_INITIAL_PASSWORD (at least 12 characters) to initialize admin.')
        db.add(User(username='admin', password_hash=hash_password(password), role='admin'))
        db.commit()


def page(request, name, status_code=200, **context):
    context.setdefault('students', [])
    context.setdefault('schemas', schemas)
    return views.TemplateResponse(request=request, name=name, context=context, status_code=status_code)


def install(app):
    app.include_router(router)

    @app.middleware('http')
    async def authentication(request, call_next):
        path = request.url.path
        if path == '/healthz':
            return HTMLResponse('ok')
        if path.startswith('/static/'):
            return await call_next(request)
        with SessionLocal() as db:
            session = db.get(LoginSession, digest(request.cookies.get(COOKIE, '')))
            now = datetime.utcnow()
            # Reject expired sessions and legacy sessions exceeding the new limit.
            valid = session and now < session.expires_at <= now + timedelta(seconds=SESSION_SECONDS)
            user = db.get(User, session.user_id) if valid else None
            if user and not user.active:
                user = None
            request.state.user = user
            request.state.session_expires_at = session.expires_at.replace(tzinfo=timezone.utc).isoformat() if user else None
            if session and not user:
                db.delete(session)
            if not user and path != '/login':
                db.add(AuditLog(username='(未登录)', action=f'{request.method} protected request', status=303))
                db.commit()
                response = RedirectResponse('/login', status_code=303)
                response.delete_cookie(COOKIE)
                response.headers['Cache-Control'] = 'no-store'
                return response
            if path.startswith('/accounts') and (not user or user.role != 'admin'):
                db.add(AuditLog(username=user.username, action=f'{request.method} account access denied', status=403))
                db.commit()
                return HTMLResponse('仅管理员可访问', status_code=403)
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                # Fail closed, including login: browser form POSTs send Origin.
                expected = os.environ.get('APP_ORIGIN', str(request.base_url).rstrip('/'))
                if request.headers.get('origin') != expected or request.headers.get('sec-fetch-site') == 'cross-site':
                    db.add(AuditLog(username=user.username if user else '(未登录)', action='CSRF origin rejected', status=403))
                    db.commit()
                    return HTMLResponse('请求来源校验失败，请刷新页面后重试。', status_code=403)
            username = user.username if user else '(未登录)'
            try:
                response = await call_next(request)
            except Exception:
                db.add(AuditLog(username=username, action=f'{request.method} {getattr(request.scope.get("route"), "path", path)}'[:300], status=500))
                db.commit()
                raise
            # Query strings and request bodies may contain passwords or private data.
            route = request.scope.get('route')
            action = f'{request.method} {getattr(route, "path", path)}'
            identifiers = {k: v for k, v in request.path_params.items() if k.endswith('_id')}
            if identifiers:
                action += ' ' + str(identifiers)
            db.add(AuditLog(username=getattr(request.state, 'audit_username', username), action=action[:300], status=response.status_code))
            db.commit()
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Referrer-Policy'] = 'same-origin'
            return response


@router.get('/login')
def login_page(request: Request):
    credentials = dict(username='', password='', remembered=False)
    with SessionLocal() as db:
        saved = db.get(RememberedLogin, digest(request.cookies.get(REMEMBER_COOKIE, '')))
        user = db.get(User, saved.user_id) if saved and saved.expires_at > datetime.utcnow() else None
        if user and user.active:
            try:
                password = remember_cipher().decrypt(saved.encrypted_password.encode(), ttl=REMEMBER_SECONDS).decode()
                credentials = dict(username=user.username, password=password, remembered=True)
            except InvalidToken:
                pass
    response = page(request, 'login.html', error='', **credentials)
    if not credentials['remembered']:
        response.delete_cookie(REMEMBER_COOKIE)
    if not request.state.user:
        response.delete_cookie(COOKIE)
    return response


@router.post('/login')
async def login(request: Request):
    form = await request.form()
    with SessionLocal() as db:
        username = str(form.get('username', '')).strip()[:120]
        password = str(form.get('password', ''))
        request.state.audit_username = username
        key = digest(username.casefold())
        attempt = db.get(LoginAttempt, key)
        now = datetime.utcnow()
        if attempt and attempt.until > now and attempt.count >= 5:
            return page(request, 'login.html', status_code=429, error='尝试次数过多，请15分钟后再试。', username=username, remembered=bool(form.get('remember')))
        user = db.query(User).filter_by(username=username, active=True).first()
        if len(password) > 1024 or not verify(password, user.password_hash if user else DUMMY_HASH):
            if not attempt:
                attempt = LoginAttempt(key=key, count=0, until=now + timedelta(minutes=15))
                db.add(attempt)
            elif attempt.until <= now:
                attempt.count = 0
                attempt.until = now + timedelta(minutes=15)
            attempt.count += 1
            db.commit()
            return page(request, 'login.html', status_code=401, error='账号或密码错误。', username=username, remembered=bool(form.get('remember')))
        if attempt:
            db.delete(attempt)
        old = db.get(LoginSession, digest(request.cookies.get(COOKIE, '')))
        if old:
            db.delete(old)
        token = secrets.token_urlsafe(48)
        remember = bool(form.get('remember'))
        db.add(LoginSession(token_hash=digest(token), user_id=user.id, expires_at=now + timedelta(seconds=SESSION_SECONDS)))
        db.query(RememberedLogin).filter(
            (RememberedLogin.token_hash == digest(request.cookies.get(REMEMBER_COOKIE, ''))) |
            (RememberedLogin.expires_at <= now)
        ).delete(synchronize_session=False)
        if remember:
            remember_token = secrets.token_urlsafe(48)
            db.add(RememberedLogin(
                token_hash=digest(remember_token), user_id=user.id,
                encrypted_password=remember_cipher().encrypt(password.encode()).decode(),
                expires_at=now + timedelta(seconds=REMEMBER_SECONDS),
            ))
        db.commit()
    response = RedirectResponse('/', status_code=303)
    set_login_cookie(response, COOKIE, token, SESSION_SECONDS)
    if remember:
        set_login_cookie(response, REMEMBER_COOKIE, remember_token, REMEMBER_SECONDS)
    else:
        response.delete_cookie(REMEMBER_COOKIE)
    return response


DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


@router.post('/logout')
def logout(request: Request):
    with SessionLocal() as db:
        db.query(LoginSession).filter_by(token_hash=digest(request.cookies.get(COOKIE, ''))).delete()
        db.commit()
    response = RedirectResponse('/login', status_code=303)
    response.delete_cookie(COOKIE)
    return response


def account_context(db, error='', edit_id=None):
    administrator = db.query(User).filter_by(role='admin').order_by(User.id).first()
    personnel = db.query(User).filter(User.id != administrator.id).order_by(User.username).all() if administrator else []
    editing = db.get(User, edit_id) if edit_id else None
    if editing and administrator and editing.id == administrator.id:
        editing = None
    logs = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(20).all()
    return dict(administrator=administrator, personnel=personnel, editing=editing, logs=logs, error=error)


@router.get('/accounts')
def accounts(request: Request, edit: int | None = None, add: bool = False):
    with SessionLocal() as db:
        return page(request, 'accounts.html', **account_context(db, edit_id=edit), adding=add)


@router.post('/accounts')
async def save_account(request: Request):
    form = await request.form()
    with SessionLocal() as db:
        user = db.get(User, int(form['user_id'])) if form.get('user_id') else User()
        username = str(form.get('username', '')).strip()
        password = str(form.get('password', ''))
        kind = str(form.get('kind', 'person'))
        role = 'admin' if kind == 'admin' else 'member'
        active = True
        duplicate = db.query(User).filter(User.username == username, User.id != (user.id or 0)).first() if user else None
        invalid = not user or not username or len(username) > 120 or duplicate or role not in ('admin', 'member')
        invalid = invalid or ((password or not user.id) and not 12 <= len(password) <= 1024)
        invalid = invalid or (kind == 'admin' and (not user.id or user.id != request.state.user.id))
        if invalid:
            user_id = user.id if user else None
            return page(request, 'accounts.html', status_code=400, **account_context(db, error='账号不能重复，密码至少需要12位。', edit_id=user_id), adding=not user_id and kind != 'admin', submitted=form)
        user.username, user.role, user.active = username, role, active
        if password:
            user.password_hash = hash_password(password)
        if user.id:
            db.query(LoginSession).filter_by(user_id=user.id).delete()
            db.query(RememberedLogin).filter_by(user_id=user.id).delete()
        db.add(user)
        db.flush()
        db.add(AuditLog(username=request.state.user.username, action=f'账号设置 user_id={user.id}; role={role}; active={active}; password_changed={bool(password)}', status=200))
        db.commit()
    return RedirectResponse('/accounts', status_code=303)


@router.get('/accounts/audit')
def audit(request: Request, page_number: int = 1):
    return RedirectResponse('/accounts#operation-logs', status_code=303)
