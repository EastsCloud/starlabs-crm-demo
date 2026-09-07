"""Structured communications, encrypted import previews and archive receipts."""
from alembic import op
import sqlalchemy as sa
revision = '20260905_02'
down_revision = '20260905_01'

def upgrade():
    for column in [
        sa.Column('duration', sa.String(80), server_default=''),
        sa.Column('school_snapshot', sa.String(200), server_default=''),
        sa.Column('grade_snapshot', sa.String(50), server_default=''),
        sa.Column('notes', sa.Text(), server_default=''),
        sa.Column('created_by_user_id', sa.Integer()),
        sa.Column('updated_at', sa.DateTime()),
        sa.Column('import_fingerprint', sa.String(64)),
    ]:
        op.add_column('communications', column)
    op.create_foreign_key('fk_communication_creator', 'communications', 'users', ['created_by_user_id'], ['id'], ondelete='SET NULL')
    op.create_unique_constraint('uq_communication_import', 'communications', ['student_id', 'import_fingerprint'])
    op.execute('UPDATE communications SET updated_at = created_at')
    op.create_table('communication_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('communication_id', sa.Integer(), sa.ForeignKey('communications.id', ondelete='CASCADE'), nullable=False),
        sa.Column('section', sa.String(30), nullable=False),
        sa.Column('item_name', sa.Text(), nullable=False),
        sa.Column('feedback', sa.Text(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False))
    op.create_index('ix_communication_items_communication_id', 'communication_items', ['communication_id'])
    op.create_table('import_previews',
        sa.Column('token_hash', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('students.id', ondelete='CASCADE')),
        sa.Column('kind', sa.String(30), nullable=False),
        sa.Column('encrypted_payload', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False))
    op.create_index('ix_import_previews_expires_at', 'import_previews', ['expires_at'])
    op.create_table('archive_exports',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('students.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_index('ix_archive_exports_student_id', 'archive_exports', ['student_id'])
    # Do not retain card verification codes. Backup/rollback policy is in deployment docs.
    op.drop_column('students', 'card_cvv')

def downgrade():
    raise RuntimeError('This migration removes CVV and adds business data; restore the pre-migration backup to roll back.')
