"""Phase 1: user_prefs

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_prefs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("residence_city", sa.String(100), nullable=False),
        sa.Column("residence_country", sa.String(100), nullable=False),
        sa.Column("preferred_airports", postgresql.JSONB(), nullable=False),
        sa.Column("excluded_airlines", postgresql.JSONB(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("budget_total_gbp", sa.Numeric(10, 2), nullable=True),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("telegram_chat_id", sa.String(40), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('sports','tourism')", name="ck_user_prefs_mode"),
        sa.CheckConstraint(
            "budget_total_gbp IS NULL OR budget_total_gbp >= 0",
            name="ck_user_prefs_budget",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_prefs_user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_prefs")
