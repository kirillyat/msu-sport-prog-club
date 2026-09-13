"""relax assignment target constraint

Задание теперь бывает и для всего клуба — тогда пусты обе ссылки. Старое
ограничение требовало ровно одну из них, и такое задание не создавалось.

Alembic не умеет автогенерировать изменения CHECK, поэтому в 0006 оно
не попало: `alembic check` их не сравнивает. SQLite не умеет менять
ограничение на месте — таблицу приходится пересобирать.

Revision ID: b7d41c0a92f5
Revises: 3830f69ca7f1
Create Date: 2026-09-13 23:58:12.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d41c0a92f5'
down_revision: str | None = '3830f69ca7f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "(group_id IS NOT NULL AND user_id IS NULL) OR (group_id IS NULL AND user_id IS NOT NULL)"
NEW = "group_id IS NULL OR user_id IS NULL"


def _table() -> sa.Table:
    """Схема assignments после 0006 без CHECK — его ставит сам batch. Явная, чтобы
    не зависеть от рефлексии: SQLite всё равно не отдаёт CHECK обратно."""
    return sa.Table(
        "assignments",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "problem_set_id",
            sa.Integer(),
            sa.ForeignKey("problem_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE")),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True)),
        sa.Column("hard_deadline", sa.Boolean(), nullable=False),
        sa.Column("points_per_problem", sa.Float()),
        sa.Column("full_clear_bonus", sa.Float()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("count_prior_solves", sa.Boolean(), nullable=False),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "assignments", copy_from=_table(), recreate="always",
    ) as batch_op:
        batch_op.create_check_constraint("ck_assignment_single_target", NEW)


def downgrade() -> None:
    # Задания для всего клуба старому ограничению не удовлетворяют — сначала убираем.
    op.execute("DELETE FROM assignments WHERE group_id IS NULL AND user_id IS NULL")
    with op.batch_alter_table(
        "assignments", copy_from=_table(), recreate="always",
    ) as batch_op:
        batch_op.create_check_constraint("ck_assignment_single_target", OLD)
