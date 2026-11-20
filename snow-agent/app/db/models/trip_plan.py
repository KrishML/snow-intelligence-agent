import uuid
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.db.models.base import Base


class TripPlan(Base):
    __tablename__ = "trip_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','booked','cancelled')",
            name="ck_trip_plan_status",
        ),
        Index("idx_trip_plans_session", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    resort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resorts.id"), nullable=False)
    outbound_date: Mapped[date] = mapped_column(Date, nullable=False)
    return_date: Mapped[date] = mapped_column(Date, nullable=False)
    flight_option: Mapped[dict | None] = mapped_column(JSONB)
    hotel_option: Mapped[dict | None] = mapped_column(JSONB)
    total_estimated_gbp: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    session_notes: Mapped[str | None] = mapped_column(Text)
    delivery_errors: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now())
