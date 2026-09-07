"""Fail closed unless an explicit test database is configured; isolate every run."""
import os
import re
import uuid
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from cryptography.fernet import Fernet

raw = os.environ.get('TEST_DATABASE_URL', '')
if not raw:
    raise pytest.UsageError('TEST_DATABASE_URL is required. Tests never fall back to a local file or production database.')
url = make_url(raw)
if url.get_backend_name() != 'postgresql' or not re.fullmatch(r'(test_[a-z0-9_]+|[a-z0-9_]+_test)', url.database or ''):
    raise pytest.UsageError('TEST_DATABASE_URL must point to a PostgreSQL database named test_* or *_test.')
production = os.environ.get('DATABASE_URL')
if production and make_url(production).set(drivername='postgresql') == url.set(drivername='postgresql'):
    raise pytest.UsageError('TEST_DATABASE_URL must differ from the configured production DATABASE_URL.')
TEST_SCHEMA = 'ams_test_' + uuid.uuid4().hex
os.environ.update(DATABASE_URL=raw, APP_ENV='test', TEST_SCHEMA=TEST_SCHEMA,
    ADMIN_INITIAL_PASSWORD='test-only-password-93824', COOKIE_SECURE='0', APP_ORIGIN='http://testserver',
    REMEMBER_ENCRYPTION_KEY=Fernet.generate_key().decode())
control_engine = create_engine(url.set(drivername='postgresql+psycopg'), hide_parameters=True)


def pytest_sessionstart(session):
    from alembic import command
    from alembic.config import Config
    with control_engine.begin() as connection:
        connection.execute(text('CREATE SCHEMA ' + TEST_SCHEMA))
    command.upgrade(Config('alembic.ini'), 'head')


@pytest.fixture(autouse=True)
def clean_test_data():
    from app.database import Base, engine
    from app import models
    with engine.begin() as connection:
        tables = ', '.join('"'+table.name+'"' for table in Base.metadata.sorted_tables)
        connection.execute(text('TRUNCATE ' + tables + ' RESTART IDENTITY CASCADE'))
    yield


def pytest_sessionfinish(session, exitstatus):
    from app.database import engine
    engine.dispose()
    with control_engine.begin() as connection:
        connection.execute(text('DROP SCHEMA IF EXISTS ' + TEST_SCHEMA + ' CASCADE'))
    control_engine.dispose()
