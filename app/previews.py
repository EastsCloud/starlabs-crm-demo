"""Short-lived encrypted previews, scoped to the current user and student."""
import json
import secrets
from datetime import datetime, timedelta
from fastapi import HTTPException
from cryptography.fernet import InvalidToken
from app.auth import digest, remember_cipher
from app.models import ImportPreview


def save_preview(db, user_id, kind, payload, student_id=None):
    db.query(ImportPreview).filter(ImportPreview.expires_at <= datetime.utcnow()).delete(synchronize_session=False)
    token = secrets.token_hex(16)
    db.add(ImportPreview(token_hash=digest(token), user_id=user_id, student_id=student_id,
        kind=kind, encrypted_payload=remember_cipher().encrypt(json.dumps(payload, ensure_ascii=False).encode()).decode(),
        expires_at=datetime.utcnow() + timedelta(minutes=30)))
    return token


def consume_preview(db, token, user_id, kind, student_id=None):
    preview = db.query(ImportPreview).filter_by(token_hash=digest(token), user_id=user_id,
        kind=kind, student_id=student_id).with_for_update().first()
    if not preview or preview.expires_at <= datetime.utcnow():
        raise HTTPException(410, '预览已失效或已使用，请重新上传。')
    try:
        payload = json.loads(remember_cipher().decrypt(preview.encrypted_payload.encode(), ttl=1800))
    except (InvalidToken, ValueError):
        raise HTTPException(410, '预览已失效，请重新上传。') from None
    db.delete(preview)
    db.flush()
    return payload
