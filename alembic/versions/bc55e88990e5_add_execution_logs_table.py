"""add execution_logs table

Revision ID: bc55e88990e5
Revises: dbbf495e5dfc
Create Date: 2026-05-19 13:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bc55e88990e5'
down_revision: Union[str, None] = 'dbbf495e5dfc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'execution_logs' not in existing_tables:
        op.create_table(
            'execution_logs',
            sa.Column('id', sa.String(length=64), nullable=False),
            sa.Column('tenant_id', sa.String(length=128), nullable=True),
            sa.Column('created_by', sa.String(length=128), nullable=True),
            sa.Column('request_id', sa.String(length=128), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('job_id', sa.String(length=128), nullable=False),
            sa.Column('stream', sa.String(length=16), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('adapter_name', sa.String(length=64), nullable=False),
            sa.Column('logged_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )

    existing_indexes = {ix['name'] for ix in inspector.get_indexes('execution_logs')}
    if 'ix_execution_log_job_id' not in existing_indexes:
        op.create_index('ix_execution_log_job_id', 'execution_logs', ['job_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_execution_log_job_id', table_name='execution_logs')
    op.drop_table('execution_logs')
