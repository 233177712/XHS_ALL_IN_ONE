"""add tags to ai_drafts

Revision ID: e716e37aa5c1
Revises: 37a4bd3e2351
Create Date: 2026-05-05 19:28:34.726716
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e716e37aa5c1'
down_revision: str | None = '37a4bd3e2351'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('ai_drafts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tags', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('ai_drafts', schema=None) as batch_op:
        batch_op.drop_column('tags')
