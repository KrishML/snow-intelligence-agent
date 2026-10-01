import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.models.base import Base

if TYPE_CHECKING:
    from app.db.models.snow_score import SnowScore


class WeeklyRun(Base):
    __tablename__ = "weekly_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','complete','failed')", name="ck_weekly_runs_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_date: Mapped[date] = mapped_column(nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    resorts_checked: Mapped[int | None] = mapped_column(Integer)
    resorts_passed: Mapped[int | None] = mapped_column(Integer)
    error_log: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column()

    scores: Mapped[list["SnowScore"]] = relationship(back_populates="run")
