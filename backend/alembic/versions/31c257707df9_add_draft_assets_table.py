"""add draft_assets table

Revision ID: 31c257707df9
Revises: a54213910de0
Create Date: 2026-05-05 16:18:01.030961
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '31c257707df9'
down_revision: str | None = 'a54213910de0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'draft_assets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('draft_id', sa.Integer(), sa.ForeignKey('ai_drafts.id'), nullable=False, index=True),
        sa.Column('asset_type', sa.String(32), nullable=False),
        sa.Column('url', sa.Text(), server_default='', nullable=False),
        sa.Column('local_path', sa.Text(), server_default='', nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_table('draft_assets')
