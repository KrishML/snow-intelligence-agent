"""Booking-service tables. Only the booking service writes these (and ``trip_plans.status``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

_JSON = JSON().with_variant(JSONB(), "postgresql")

STATUSES = (
    "consented",
    "flight_held",
    "hotel_held",
    "payment_authorised",
    "booked",
    "failed",
    "failed_manual_review",
)


class BookingBase(DeclarativeBase):
    pass


class ConsentLog(BookingBase):
    """Append-only: a database trigger rejects UPDATE and DELETE."""

    __tablename__ = "consent_log"
    __table_args__ = (
        CheckConstraint("channel IN ('web','telegram')", name="ck_consent_log_channel"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # plan_id references trip_plans.id; the FK lives in the migration because that
    # table belongs to the other service's metadata.
    plan_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(server_default=func.now())
    flight_total_gbp: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    hotel_total_gbp: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    total_gbp: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    price_lock_expires_at: Mapped[datetime] = mapped_column(nullable=False)
    plan_snapshot: Mapped[dict] = mapped_column(_JSON, nullable=False)


class Booking(BookingBase):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('" + "','".join(STATUSES) + "')", name="ck_bookings_status"
        ),
        # One live attempt per plan; only a cleanly failed attempt frees the plan.
        Index(
            "uq_bookings_open_plan",
            "plan_id",
            unique=True,
            postgresql_where=text("status <> 'failed'"),
            sqlite_where=text("status <> 'failed'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    consent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("consent_log.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    flight_ref: Mapped[str | None] = mapped_column(String(100))
    flight_booking_reference: Mapped[str | None] = mapped_column(String(100))
    hotel_ref: Mapped[str | None] = mapped_column(String(100))
    hotel_confirmation: Mapped[str | None] = mapped_column(String(100))
    payment_ref: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    needs_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(onupdate=func.now())


class BookingEvent(BookingBase):
    """Status history for audit and the status endpoint."""

    __tablename__ = "booking_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(server_default=func.now())
