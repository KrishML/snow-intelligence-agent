import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.models.base import Base

if TYPE_CHECKING:
    from app.db.models.resort import Resort
    from app.db.models.weekly_run import WeeklyRun


class SnowScore(Base):
    __tablename__ = "snow_scores"
    __table_args__ = (
        Index("idx_snow_scores_run_id", "weekly_run_id"),
        Index("idx_snow_scores_resort_id", "resort_id"),
        Index(
            "idx_snow_scores_total_desc",
            "score_total",
            postgresql_ops={"score_total": "DESC"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    weekly_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("weekly_runs.id"))
    resort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resorts.id"))
    passed_gate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    gate_failure_reason: Mapped[str | None] = mapped_column(Text)
    depth_cm: Mapped[int | None] = mapped_column(Integer)
    temperature_c: Mapped[float | None] = mapped_column()
    forecast_stable: Mapped[bool | None] = mapped_column(Boolean)
    forecast_detail: Mapped[dict | None] = mapped_column(JSONB)
    historical_reliability_pct: Mapped[float | None] = mapped_column()
    score_depth: Mapped[int | None] = mapped_column(Integer)
    score_forecast: Mapped[int | None] = mapped_column(Integer)
    score_historical: Mapped[int | None] = mapped_column(Integer)
    score_qualitative: Mapped[int | None] = mapped_column(Integer)
    score_total: Mapped[int | None] = mapped_column(Integer)
    rank: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    run: Mapped["WeeklyRun"] = relationship(back_populates="scores")
    resort: Mapped["Resort"] = relationship()
