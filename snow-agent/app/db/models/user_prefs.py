import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, JSON, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.models.base import Base

_JSON_LIST = JSON().with_variant(JSONB(), "postgresql")


class UserPrefs(Base):
    __tablename__ = "user_prefs"
    __table_args__ = (
        CheckConstraint("mode IN ('sports','tourism')", name="ck_user_prefs_mode"),
        CheckConstraint(
            "budget_total_gbp IS NULL OR budget_total_gbp >= 0",
            name="ck_user_prefs_budget",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(unique=True, nullable=False)
    residence_city: Mapped[str] = mapped_column(String(100), nullable=False)
    residence_country: Mapped[str] = mapped_column(String(100), nullable=False)
    preferred_airports: Mapped[list[str]] = mapped_column(_JSON_LIST, nullable=False)
    excluded_airlines: Mapped[list[str]] = mapped_column(_JSON_LIST, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    budget_total_gbp: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    email: Mapped[str | None] = mapped_column(String(254))
    telegram_chat_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now())
