"""Phase 2 schema: qualitative_signals, webcam_snapshots, trip_plans, pgvector tables

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Enable pgvector extension ──────────────────────────────────────────
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    # ── 2. qualitative_signals ────────────────────────────────────────────────
    op.create_table(
        "qualitative_signals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
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
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("raw_snippet", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("sentiment", sa.String(20), nullable=True),
        sa.Column(
            "negative_keywords",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source_type IN ('reddit','rss')",
            name="ck_qualitative_signals_source_type",
        ),
        sa.CheckConstraint(
            "sentiment IN ('positive','neutral','negative')",
            name="ck_qualitative_signals_sentiment",
        ),
        sa.ForeignKeyConstraint(
            ["weekly_run_id"],
            ["weekly_runs.id"],
            name="fk_qualitative_signals_weekly_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["resort_id"],
            ["resorts.id"],
            name="fk_qualitative_signals_resort_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # GIN index on negative_keywords array column
    op.execute(sa.text(
        "CREATE INDEX idx_qualitative_signals_keywords "
        "ON qualitative_signals USING gin(negative_keywords)"
    ))

    # B-tree index on (weekly_run_id, resort_id)
    op.create_index(
        "idx_qualitative_signals_run_resort",
        "qualitative_signals",
        ["weekly_run_id", "resort_id"],
    )

    # ── 3. webcam_snapshots ───────────────────────────────────────────────────
    op.create_table(
        "webcam_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
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
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("classification", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "classification IN ('snow_good','snow_poor','no_snow','camera_offline')",
            name="ck_webcam_snapshots_classification",
        ),
        sa.ForeignKeyConstraint(
            ["weekly_run_id"],
            ["weekly_runs.id"],
            name="fk_webcam_snapshots_weekly_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["resort_id"],
            ["resorts.id"],
            name="fk_webcam_snapshots_resort_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Composite B-tree index on (resort_id, captured_at DESC)
    op.execute(sa.text(
        "CREATE INDEX idx_webcam_snapshots_resort_time "
        "ON webcam_snapshots (resort_id, captured_at DESC)"
    ))

    # ── 4. trip_plans ─────────────────────────────────────────────────────────
    op.create_table(
        "trip_plans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("session_id", sa.String(100), nullable=False),
        sa.Column(
            "resort_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("outbound_date", sa.Date(), nullable=False),
        sa.Column("return_date", sa.Date(), nullable=False),
        sa.Column(
            "flight_option",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "hotel_option",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("total_estimated_gbp", sa.Numeric(10, 2), nullable=True),
        sa.Column("session_notes", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft','booked','cancelled')",
            name="ck_trip_plans_status",
        ),
        sa.ForeignKeyConstraint(
            ["resort_id"],
            ["resorts.id"],
            name="fk_trip_plans_resort_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Index on session_id
    op.create_index(
        "idx_trip_plans_session",
        "trip_plans",
        ["session_id"],
    )

    # ── 5. pgvector tables (raw DDL — vector(1536) not known to SQLAlchemy Core)

    # preference_embeddings
    op.execute(sa.text("""
        CREATE TABLE preference_embeddings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            text TEXT NOT NULL,
            embedding vector(1536) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX ON preference_embeddings "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists=100)"
    ))

    # trip_feedback_embeddings
    op.execute(sa.text("""
        CREATE TABLE trip_feedback_embeddings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            text TEXT NOT NULL,
            embedding vector(1536) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX ON trip_feedback_embeddings "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists=100)"
    ))

    # resort_knowledge_embeddings
    op.execute(sa.text("""
        CREATE TABLE resort_knowledge_embeddings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            resort_id UUID NOT NULL REFERENCES resorts(id),
            season VARCHAR(9) NOT NULL,
            text TEXT NOT NULL,
            embedding vector(1536) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX ON resort_knowledge_embeddings "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists=100)"
    ))

    # session_summary_embeddings
    op.execute(sa.text("""
        CREATE TABLE session_summary_embeddings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            session_id VARCHAR(100) NOT NULL,
            text TEXT NOT NULL,
            embedding vector(1536) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX ON session_summary_embeddings "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists=100)"
    ))


def downgrade() -> None:
    # Drop pgvector tables (no named indexes to drop — they used auto-named indexes)
    op.execute(sa.text("DROP TABLE IF EXISTS session_summary_embeddings"))
    op.execute(sa.text("DROP TABLE IF EXISTS resort_knowledge_embeddings"))
    op.execute(sa.text("DROP TABLE IF EXISTS trip_feedback_embeddings"))
    op.execute(sa.text("DROP TABLE IF EXISTS preference_embeddings"))

    # Drop trip_plans
    op.drop_index("idx_trip_plans_session", table_name="trip_plans")
    op.drop_table("trip_plans")

    # Drop webcam_snapshots
    op.execute(sa.text("DROP INDEX IF EXISTS idx_webcam_snapshots_resort_time"))
    op.drop_table("webcam_snapshots")

    # Drop qualitative_signals
    op.drop_index("idx_qualitative_signals_run_resort", table_name="qualitative_signals")
    op.execute(sa.text("DROP INDEX IF EXISTS idx_qualitative_signals_keywords"))
    op.drop_table("qualitative_signals")

    # Drop pgvector extension (only if no other tables use it)
    op.execute(sa.text("DROP EXTENSION IF EXISTS vector"))
