"""Offline schema check. Does not replace a real PostgreSQL migration rehearsal."""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from app.database import Base


def test_postgres_table_definitions_compile():
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert 'CREATE TABLE' in ddl
