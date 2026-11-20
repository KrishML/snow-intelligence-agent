"""Phase 6: allow 'official' qualitative signal source

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAME = "ck_qualitative_signals_source_type"


def upgrade() -> None:
    op.drop_constraint(_NAME, "qualitative_signals", type_="check")
    op.create_check_constraint(
        _NAME, "qualitative_signals", "source_type IN ('reddit','rss','official')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE qualitative_signals SET source_type = 'rss' WHERE source_type = 'official'"
    )
    op.drop_constraint(_NAME, "qualitative_signals", type_="check")
    op.create_check_constraint(
        _NAME, "qualitative_signals", "source_type IN ('reddit','rss')"
    )
