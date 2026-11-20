import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, Index
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.db.models.base import Base


class QualitativeSignal(Base):
    __tablename__ = "qualitative_signals"
    __table_args__ = (
        CheckConstraint("source_type IN ('reddit','rss','official')", name="ck_qualitative_source_type"),
        CheckConstraint("sentiment IN ('positive','neutral','negative')", name="ck_qualitative_sentiment"),
        Index("idx_qualitative_signals_run_resort", "weekly_run_id", "resort_id"),
        Index(
            "idx_qualitative_signals_keywords",
            "negative_keywords",
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    weekly_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("weekly_runs.id"), nullable=False)
    resort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resorts.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_snippet: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[str | None] = mapped_column(String(20))
    negative_keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    score: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
