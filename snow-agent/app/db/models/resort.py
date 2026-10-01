import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Boolean, CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.models.base import Base


class Resort(Base):
    __tablename__ = "resorts"
    __table_args__ = (
        CheckConstraint("mode IN ('sports','tourism','both')", name="ck_resorts_mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(50), nullable=False)
    region: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[float] = mapped_column(nullable=False)
    longitude: Mapped[float] = mapped_column(nullable=False)
    altitude_base_m: Mapped[int] = mapped_column(Integer, nullable=False)
    altitude_peak_m: Mapped[int | None] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    nearest_airports: Mapped[list[str]] = mapped_column(ARRAY(String(10)), nullable=False)
    piste_count: Mapped[int | None] = mapped_column(Integer)
    lift_count: Mapped[int | None] = mapped_column(Integer)
    webcam_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
