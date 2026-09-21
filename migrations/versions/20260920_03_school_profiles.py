"""Add school profiles without rewriting or removing existing business data."""
from alembic import op
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text
revision = '20260920_03'
down_revision = '20260905_02'

def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('students', Column('student_type', String(20), nullable=False, server_default='college'))
    op.add_column('students', Column('enrollment_year', String(4), server_default=''))
    op.add_column('students', Column('birth_place', String(200), server_default=''))
    op.add_column('students', Column('nationality', String(100), server_default=''))
    op.add_column('students', Column('current_school', String(200), server_default=''))
    op.add_column('students', Column('applying_grade', String(50), server_default=''))
    op.add_column('students', Column('mother_info', Text, server_default=''))
    op.add_column('students', Column('father_info', Text, server_default=''))
    op.add_column('students', Column('application_email', String(200), server_default=''))
    op.add_column('students', Column('parent_application_email', String(200), server_default=''))
    op.add_column('students', Column('toefl_account', String(200), server_default=''))
    op.add_column('students', Column('ssat_parent_account', String(200), server_default=''))
    op.add_column('students', Column('ssat_student_account', String(200), server_default=''))
    op.add_column('students', Column('vericant_account', String(200), server_default=''))
    op.add_column('students', Column('show_and_tell', Text, server_default=''))
    op.add_column('students', Column('css_completed_date', Date, nullable=True))
    op.add_column('students', Column('css_report_date', Date, nullable=True))
    op.add_column('students', Column('css_submitted', String(20), server_default=''))
    op.add_column('applications', Column('school_state', String(100), server_default=''))
    op.add_column('applications', Column('applying_grade', String(50), server_default=''))
    op.add_column('applications', Column('application_url', String(500), server_default=''))
    op.add_column('applications', Column('application_fee', String(80), server_default=''))
    op.add_column('applications', Column('toefl_code', String(50), server_default=''))
    op.add_column('applications', Column('toefl_delivery_date', Date, nullable=True))
    op.add_column('applications', Column('toefl_delivery_score', String(80), server_default=''))
    op.add_column('applications', Column('ssat_code', String(50), server_default=''))
    op.add_column('applications', Column('ssat_delivery_date', Date, nullable=True))
    op.add_column('applications', Column('ssat_delivery_score', String(80), server_default=''))
    op.add_column('applications', Column('isee_code', String(50), server_default=''))
    op.add_column('applications', Column('isee_delivery_date', Date, nullable=True))
    op.add_column('applications', Column('isee_delivery_score', String(80), server_default=''))
    op.add_column('applications', Column('css_status', Text, server_default=''))
    op.add_column('applications', Column('school_supplemental_essay', Text, server_default=''))
    op.add_column('applications', Column('recommendations', Text, server_default=''))
    op.add_column('applications', Column('transcript_resubmit_date', Date, nullable=True))
    op.add_column('exams', Column('language', String(30), server_default=''))
    op.add_column('exams', Column('verbal', String(30), server_default=''))
    op.add_column('exams', Column('quantitative', String(30), server_default=''))
    op.add_column('exams', Column('analytical', String(30), server_default=''))
    op.create_table('student_activities',
        Column('id', Integer, primary_key=True),
        Column('student_id', Integer, ForeignKey('students.id', ondelete='CASCADE'), nullable=False),
        Column('name', String(200), nullable=False),
        Column('activity_type', String(30), nullable=False),
        Column('frequency_duration', String(300)),
        Column('level', String(30)),
        Column('created_at', DateTime()),
        Column('updated_at', DateTime()))
    op.create_index('ix_student_activities_student_id', 'student_activities', ['student_id'])
    op.create_table('third_party_interviews',
        Column('id', Integer, primary_key=True),
        Column('student_id', Integer, ForeignKey('students.id', ondelete='CASCADE'), nullable=False),
        Column('interview_type', String(50), nullable=False),
        Column('date', Date, nullable=True),
        Column('psee_score', String(80)),
        Column('pwse_score', String(80)),
        Column('result', Text),
        Column('created_at', DateTime()),
        Column('updated_at', DateTime()))
    op.create_index('ix_third_party_interviews_student_id', 'third_party_interviews', ['student_id'])
    op.create_table('school_meetings',
        Column('id', Integer, primary_key=True),
        Column('student_id', Integer, ForeignKey('students.id', ondelete='CASCADE'), nullable=False),
        Column('school_name', String(200), nullable=False),
        Column('date', Date, nullable=True),
        Column('format', String(30), nullable=False),
        Column('notes', Text),
        Column('created_at', DateTime()),
        Column('updated_at', DateTime()))
    op.create_index('ix_school_meetings_student_id', 'school_meetings', ['student_id'])
    op.create_table('training_records',
        Column('id', Integer, primary_key=True),
        Column('student_id', Integer, ForeignKey('students.id', ondelete='CASCADE'), nullable=False),
        Column('date', Date, nullable=True),
        Column('duration', String(80)),
        Column('training_type', String(30), nullable=False),
        Column('method', String(20), nullable=False),
        Column('notes', Text),
        Column('created_at', DateTime()),
        Column('updated_at', DateTime()))
    op.create_index('ix_training_records_student_id', 'training_records', ['student_id'])
    op.create_index('ix_students_student_type', 'students', ['student_type'])

def downgrade():
    raise RuntimeError('School records contain business data. Use a forward fix or restore a verified backup; do not drop these columns/tables automatically.')
