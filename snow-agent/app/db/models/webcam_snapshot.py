import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.db.models.base import Base


class WebcamSnapshot(Base):
    __tablename__ = "webcam_snapshots"
    __table_args__ = (
        CheckConstraint(
            "classification IN ('snow_good','snow_poor','no_snow','camera_offline')",
            name="ck_webcam_classification",
        ),
        Index(
            "idx_webcam_snapshots_resort_time",
            "resort_id",
            text("captured_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    weekly_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("weekly_runs.id"), nullable=False)
    resort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resorts.id"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    classification: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
