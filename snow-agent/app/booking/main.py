"""Booking service entry point: ``uvicorn app.booking.main:app``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.booking.api import router
from app.booking.config import booking_settings
from app.booking.db import BookingSessionLocal, engine as booking_engine
from app.booking.engine import BookingEngine
from app.booking.notify import ChannelNotifier
from app.booking.providers import DuffelFlightProvider, StripePaymentProvider, UnconfiguredHotelProvider
from app.booking.telegram import TelegramBookingHandler, build_application
from app.isolation import assert_booking_environment
from app.logging_config import configure_logging
from app.readiness import database_check, run_checks


def build_engine() -> BookingEngine:
    return BookingEngine(
        BookingSessionLocal,
        DuffelFlightProvider(booking_settings),
        UnconfiguredHotelProvider(),
        StripePaymentProvider(booking_settings),
        ChannelNotifier(),
        booking_settings,
    )


def create_app(engine: BookingEngine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # Refuse to run if chat-only secrets leaked into this container.
        configure_logging()
        assert_booking_environment()
        app.state.engine = engine or build_engine()
        bot = None
        if booking_settings.telegram_booking_enabled and booking_settings.telegram_bot_token:
            bot = build_application(
                TelegramBookingHandler(app.state.engine, booking_settings),
                booking_settings.telegram_bot_token,
            )
            await bot.initialize()
            await bot.start()
            await bot.updater.start_polling()
        try:
            yield
        finally:
            if bot is not None:
                await bot.updater.stop()
                await bot.stop()
                await bot.shutdown()

    app = FastAPI(title="Snow Agent Booking Service", lifespan=lifespan)
    app.include_router(router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    async def ready() -> JSONResponse:
        ok, details = await run_checks({"database": database_check(booking_engine)})
        return JSONResponse(
            {"status": "ready" if ok else "not_ready", "checks": details},
            status_code=200 if ok else 503,
        )

    return app


app = create_app()
