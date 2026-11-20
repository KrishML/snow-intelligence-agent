"""Phase 7: consent_log (append-only), bookings, booking_events

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUSES = (
    "consented", "flight_held", "hotel_held", "payment_authorised",
    "booked", "failed", "failed_manual_review",
)


def upgrade() -> None:
    uuid_pk = dict(
        type_=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_table(
        "consent_log",
        sa.Column("id", server_default=sa.text("gen_random_uuid()"), **uuid_pk),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("consented_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("flight_total_gbp", sa.Numeric(10, 2), nullable=False),
        sa.Column("hotel_total_gbp", sa.Numeric(10, 2), nullable=False),
        sa.Column("total_gbp", sa.Numeric(10, 2), nullable=False),
        sa.Column("price_lock_expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("plan_snapshot", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("channel IN ('web','telegram')", name="ck_consent_log_channel"),
        sa.ForeignKeyConstraint(["plan_id"], ["trip_plans.id"], name="fk_consent_log_plan_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "bookings",
        sa.Column("id", server_default=sa.text("gen_random_uuid()"), **uuid_pk),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("flight_ref", sa.String(100)),
        sa.Column("flight_booking_reference", sa.String(100)),
        sa.Column("hotel_ref", sa.String(100)),
        sa.Column("hotel_confirmation", sa.String(100)),
        sa.Column("payment_ref", sa.String(100)),
        sa.Column("error", sa.Text()),
        sa.Column("needs_manual_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True)),
        sa.CheckConstraint(
            "status IN ('" + "','".join(_STATUSES) + "')", name="ck_bookings_status"
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["trip_plans.id"], name="fk_bookings_plan_id"),
        sa.ForeignKeyConstraint(["consent_id"], ["consent_log.id"], name="fk_bookings_consent_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bookings_user_id", "bookings", ["user_id"])
    op.create_index(
        "uq_bookings_open_plan", "bookings", ["plan_id"], unique=True,
        postgresql_where=sa.text("status <> 'failed'"),
    )
    op.create_table(
        "booking_events",
        sa.Column("id", server_default=sa.text("gen_random_uuid()"), **uuid_pk),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], name="fk_booking_events_booking_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_booking_events_booking_id", "booking_events", ["booking_id"])

    # Consent records are a legal record: reject any change or removal.
    op.execute(
        """
        CREATE FUNCTION consent_log_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'consent_log is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER consent_log_no_change BEFORE UPDATE OR DELETE ON consent_log "
        "FOR EACH ROW EXECUTE FUNCTION consent_log_append_only()"
    )
    op.execute(
        "CREATE TRIGGER consent_log_no_truncate BEFORE TRUNCATE ON consent_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION consent_log_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS consent_log_no_truncate ON consent_log")
    op.execute("DROP TRIGGER IF EXISTS consent_log_no_change ON consent_log")
    op.execute("DROP FUNCTION IF EXISTS consent_log_append_only()")
    op.drop_index("ix_booking_events_booking_id", table_name="booking_events")
    op.drop_table("booking_events")
    op.drop_index("uq_bookings_open_plan", table_name="bookings")
    op.drop_index("ix_bookings_user_id", table_name="bookings")
    op.drop_table("bookings")
    op.drop_table("consent_log")
