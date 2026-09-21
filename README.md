# Starlabs AMS

A server-rendered application management system for small education consulting teams. The interface is in Chinese and focuses on long-term student records, application progress, deadlines, communication history, and operational follow-up.

This public repository is a portfolio-safe snapshot of the production codebase. It contains no production credentials, database contents, private deployment configuration, or identifiable student records.

## Highlights

- Student profiles covering core information, applications, courses, standardized tests, projects, tasks, timelines, and communication records.
- Separate undergraduate and middle/high school profiles, including school applications, activities, interviews, coaching, TOEFL Junior and SSAT scores.
- Dashboard views for priorities, follow-ups, important dates, and application statistics.
- Excel student import with preview, selective field updates, and multi-file support; school templates accept both XLS and XLSX, with a clean downloadable template.
- Structured communication-record import from a fixed PDF layout, with editable preview and duplicate protection.
- Student archive PDF export with sensitive credentials excluded.
- Team authentication, administrator-managed accounts, session controls, and audit logs.
- PostgreSQL persistence with Alembic migrations and isolated PostgreSQL test schemas.

## Architecture

- FastAPI for routing and request handling
- SQLAlchemy and PostgreSQL for persistence
- Alembic for versioned schema migrations
- Jinja2, HTML, CSS, and lightweight JavaScript for the interface
- ReportLab, pdfplumber, and pypdf for PDF workflows
- Pytest and GitHub Actions for automated verification

## Local setup

Python 3.12 and PostgreSQL are recommended.

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env.local
```

Configure `.env.local`, then load those values into the current shell and run:

```powershell
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

On Windows, `run.bat` loads `.env.local` for application startup after the database has been migrated.

## Tests

Tests require a separate PostgreSQL database named `test_*` or `*_test`. They create and remove a random isolated schema and refuse to fall back to the application database.

```powershell
python -m pip install -r requirements-dev.txt
$env:TEST_DATABASE_URL = 'postgresql+psycopg://test_user:test_password@127.0.0.1:5432/ams_test'
python -m pytest tests -q
```

The files under `docs/` are anonymized samples for exercising the import workflows. Do not use real student information in a public fork.

## Repository scope

The hosted production service and its operational documentation remain private. This repository is maintained as a code portfolio and is not the production deployment source.
