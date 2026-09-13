"""drop assignment point settings

Табло больше не считает баллы: место определяется числом задач, закрытых
в срок. Настройки цены задачи и бонуса за полный комплект после этого
ни на что не влияли, поэтому уезжают из схемы вместе с формой выдачи.

Жёсткий дедлайн остаётся — он решает, засчитывать ли решение вообще.

Revision ID: c4a90e17b3d2
Revises: d7cf1d8097b0
Create Date: 2026-09-14 01:05:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c4a90e17b3d2'
down_revision: str | None = 'd7cf1d8097b0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('assignments', schema=None) as batch_op:
        batch_op.drop_column('points_per_problem')
        batch_op.drop_column('full_clear_bonus')


def downgrade() -> None:
    with op.batch_alter_table('assignments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('full_clear_bonus', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('points_per_problem', sa.Float(), nullable=True))
