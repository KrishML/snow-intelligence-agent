"""Booking-service database access (its own engine, separate from the chat service)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.booking.config import booking_settings

engine = create_async_engine(booking_settings.database_url, pool_pre_ping=True)
BookingSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
