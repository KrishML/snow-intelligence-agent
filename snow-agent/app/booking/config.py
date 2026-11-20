"""Booking-service configuration; deliberately independent of ``app.settings``."""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class BookingSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.booking", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://snow:snow@localhost:5432/snow_agent"
    booking_api_token: str = ""
    default_user_id: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")

    duffel_api_key: str = ""
    duffel_base_url: str = "https://api.duffel.com"
    stripe_secret_key: str = ""
    stripe_base_url: str = "https://api.stripe.com"
    hotel_booking_api_key: str = ""

    price_lock_minutes: int = 10
    provider_timeout_seconds: float = 20.0
    # Total GBP above which a booking is refused outright, as a runaway-spend guard.
    max_booking_total_gbp: Decimal = Decimal("5000")

    # Telegram booking (Phase 8) stays off until web booking has proven itself.
    telegram_booking_enabled: bool = False
    telegram_booking_min_bookings: int = 4
    telegram_confirm_window_minutes: int = 5
    # Saved details used for Telegram bookings, since a chat cannot collect them safely.
    telegram_booking_payment_method_id: str = ""
    telegram_booking_traveller_json: str = ""

    # Notification channels for booking confirmations and failures.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    sendgrid_api_key: str = ""
    sendgrid_from_email: str = "reports@snow-agent.io"
    delivery_email_address: str = ""


booking_settings = BookingSettings()
