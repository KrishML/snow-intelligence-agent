"""Phase 5: trip plan ownership and delivery errors

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Plans created before ownership existed belong to the single default user.
_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.add_column(
        "trip_plans", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.execute(
        sa.text(
            "UPDATE trip_plans SET user_id = CAST(:uid AS uuid) WHERE user_id IS NULL"
        ).bindparams(uid=_DEFAULT_USER_ID)
    )
    op.create_index("ix_trip_plans_user_id", "trip_plans", ["user_id"])
    op.add_column(
        "trip_plans",
        sa.Column("delivery_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trip_plans", "delivery_errors")
    op.drop_index("ix_trip_plans_user_id", table_name="trip_plans")
    op.drop_column("trip_plans", "user_id")
