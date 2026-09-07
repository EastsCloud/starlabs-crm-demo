from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import ipaddress
import re
import socket
from threading import Event, Thread
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app import models
from app.database import SessionLocal


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


DATE_PATTERNS = [
    re.compile(r"\b(20\d{2})(?:[-/.]|年)(\d{1,2})(?:[-/.]|月)(\d{1,2})日?\b"),
    re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b", re.I),
]
MONTHS = {name.lower(): index for index, name in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}
_worker_started = False
_stop_event = Event()


def validate_public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("只支持有效的 http/https 官网地址")
    for info in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("不允许抓取内网或本机地址")
    return url


def _extract_events(text, source):
    found = []
    for pattern_index, pattern in enumerate(DATE_PATTERNS):
        for match in pattern.finditer(text):
            if pattern_index == 0:
                year, month, day = map(int, match.groups())
            else:
                month_name, day, year = match.groups()
                year, month, day = int(year), MONTHS[month_name.lower()], int(day)
            try:
                event_date = date(year, month, day)
            except ValueError:
                continue
            context = text[max(0, match.start() - 90):min(len(text), match.end() + 90)].strip()
            lowered = context.lower()
            kind = "截止日期" if any(word in lowered for word in ["deadline", "due", "close", "截止"]) else "重要日期"
            title = f"{source.name} · {kind}"
            found.append((event_date, title, kind, context[:500]))
    unique = {}
    for event in found:
        unique[(event[0], event[1])] = event
    return list(unique.values())[:100]


def refresh_source(db, source):
    try:
        validate_public_url(source.url)
        request = Request(source.url, headers={"User-Agent": "StudentPlannerDateMonitor/1.0"})
        with urlopen(request, timeout=15) as response:
            body = response.read(2_000_000).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        parser = TextExtractor()
        parser.feed(body)
        text = " ".join(parser.parts)
        events = _extract_events(text, source)
        for event_date, title, kind, context in events:
            exists = db.query(models.PublicEvent).filter(models.PublicEvent.source_id == source.id, models.PublicEvent.title == title, models.PublicEvent.event_date == event_date).first()
            if not exists:
                db.add(models.PublicEvent(source_id=source.id, title=title, category=source.category, event_date=event_date, date_kind=kind, source_url=source.url, source_text=context, review_status="待确认", reminder_days="7,1"))
        source.last_status = f"成功，发现 {len(events)} 个日期"
        source.last_error = ""
    except Exception as exc:
        source.last_status = "抓取失败"
        source.last_error = str(exc)[:1000]
    source.last_checked_at = datetime.utcnow()
    db.commit()


def refresh_due_sources():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        for source in db.query(models.DataSource).filter(models.DataSource.enabled.is_(True)).all():
            if not source.last_checked_at or source.last_checked_at + timedelta(hours=source.refresh_hours or 24) <= now:
                refresh_source(db, source)
    finally:
        db.close()


def start_worker():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True

    def run():
        while not _stop_event.is_set():
            refresh_due_sources()
            _stop_event.wait(3600)

    Thread(target=run, name="public-data-worker", daemon=True).start()
