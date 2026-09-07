"""Baseline: adopt the existing production schema or initialize an empty database."""
import json
from pathlib import Path
from alembic import op
import sqlalchemy as sa
revision = '20260905_01'
down_revision = None

def upgrade():
    schema = json.loads((Path(__file__).parents[1] / 'baseline.json').read_text(encoding='utf-8'))
    for name, definition in schema.items():
        inspector = sa.inspect(op.get_bind())
        if inspector.has_table(name):
            columns = {c['name'] for c in inspector.get_columns(name)}
            if set(definition['columns']) - columns:
                raise RuntimeError('Existing schema differs from baseline: ' + name)
        else:
            op.execute(definition['sql'])
            for index in definition['indexes']:
                op.execute(index)

def downgrade():
    raise RuntimeError('Baseline rollback requires restoring a verified database backup.')
