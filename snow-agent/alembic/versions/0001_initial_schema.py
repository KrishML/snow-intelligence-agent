"""Initial schema: resorts, weekly_runs, snow_scores

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── resorts ──────────────────────────────────────────────────────────────
    op.create_table(
        "resorts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("country", sa.String(50), nullable=False),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("altitude_base_m", sa.Integer(), nullable=False),
        sa.Column("altitude_peak_m", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column(
            "nearest_airports",
            postgresql.ARRAY(sa.String(10)),
            nullable=False,
        ),
        sa.Column("piste_count", sa.Integer(), nullable=True),
        sa.Column("lift_count", sa.Integer(), nullable=True),
        sa.Column("webcam_url", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "mode IN ('sports','tourism','both')",
            name="ck_resorts_mode",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── weekly_runs ───────────────────────────────────────────────────────────
    op.create_table(
        "weekly_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("resorts_checked", sa.Integer(), nullable=True),
        sa.Column("resorts_passed", sa.Integer(), nullable=True),
        sa.Column("error_log", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running','complete','failed')",
            name="ck_weekly_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── snow_scores ───────────────────────────────────────────────────────────
    op.create_table(
        "snow_scores",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "weekly_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "resort_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("passed_gate", sa.Boolean(), nullable=False),
        sa.Column("gate_failure_reason", sa.Text(), nullable=True),
        sa.Column("depth_cm", sa.Integer(), nullable=True),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("forecast_stable", sa.Boolean(), nullable=True),
        sa.Column(
            "forecast_detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("historical_reliability_pct", sa.Float(), nullable=True),
        sa.Column("score_depth", sa.Integer(), nullable=True),
        sa.Column("score_forecast", sa.Integer(), nullable=True),
        sa.Column("score_historical", sa.Integer(), nullable=True),
        sa.Column("score_qualitative", sa.Integer(), nullable=True),
        sa.Column("score_total", sa.Integer(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["weekly_run_id"],
            ["weekly_runs.id"],
            name="fk_snow_scores_weekly_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["resort_id"],
            ["resorts.id"],
            name="fk_snow_scores_resort_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── Indexes on snow_scores ─────────────────────────────────────────────
    op.create_index(
        "idx_snow_scores_run_id",
        "snow_scores",
        ["weekly_run_id"],
    )
    op.create_index(
        "idx_snow_scores_resort_id",
        "snow_scores",
        ["resort_id"],
    )
    op.create_index(
        "idx_snow_scores_total_desc",
        "snow_scores",
        [sa.text("score_total DESC")],
        # postgresql_ops argument not needed — DESC is expressed in the column expression
    )


def downgrade() -> None:
    # Drop indexes first, then tables in reverse dependency order
    op.drop_index("idx_snow_scores_total_desc", table_name="snow_scores")
    op.drop_index("idx_snow_scores_resort_id", table_name="snow_scores")
    op.drop_index("idx_snow_scores_run_id", table_name="snow_scores")
    op.drop_table("snow_scores")
    op.drop_table("weekly_runs")
    op.drop_table("resorts")
