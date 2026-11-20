"""Read-only chat tools and draft-plan creation."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from amadeus import Client
from langchain_core.tools import ToolException, tool
from sqlalchemy import select

from app.chat.store import SessionStore
from app.clients.amadeus_hotels import search_hotels
from app.clients.open_meteo import OpenMeteoClient
from app.db.models.resort import Resort
from app.db.models.snow_score import SnowScore
from app.db.models.trip_plan import TripPlan
from app.db.models.webcam_snapshot import WebcamSnapshot
from app.db.models.weekly_run import WeeklyRun
from app.db.session import AsyncSessionLocal
from app.plans.service import dispatch_plan, offer_total_gbp, plan_payload
from app.prefs.registry import get_city
from app.prefs.service import Preferences, default_preferences
from app.settings import settings
from app.webcam.classifier import WebcamClassifier

logger = logging.getLogger(__name__)

_LIVE_CALL_LIMIT = 10


def create_chat_tools(
    session_id: str,
    user_id: uuid.UUID,
    state: dict[str, Any],
    prefs: Preferences | None = None,
    store: SessionStore | None = None,
) -> list:
    """Create the seven permitted tools bound to one authenticated chat session.

    With a store, live-call counts, travel offers and the plan flag live in Redis;
    otherwise they live in ``state`` for the lifetime of the process.
    """
    prefs = prefs or default_preferences(user_id)
    mode = prefs.mode

    async def _reserve_live_call() -> bool:
        if store is not None:
            return await store.reserve_live_call(session_id, _LIVE_CALL_LIMIT)
        if state["live_calls"] >= _LIVE_CALL_LIMIT:
            return False
        state["live_calls"] += 1
        return True

    async def _hotel_options() -> list[dict[str, Any]]:
        if store is not None:
            return await store.load_hotel_options(session_id)
        return state.get("hotel_options", [])

    async def _flight_options() -> list[dict[str, Any]]:
        if store is not None:
            return await store.load_flight_options(session_id)
        return state.get("flight_options", [])

    async def _plan_created() -> bool:
        if store is not None:
            meta = await store.get_meta(session_id)
            return bool(meta and meta.get("plan_created"))
        return state["plan_created"]

    @tool
    async def filter_results(
        min_depth_cm: int | None = None,
        max_altitude_m: int | None = None,
        min_altitude_m: int | None = None,
        countries: list[str] | None = None,
        min_score: int | None = None,
    ) -> list[dict[str, Any]]:
        """Filter ranked resorts from the latest completed weekly cache."""
        async with AsyncSessionLocal() as session:
            latest_run = await _get_latest_run(session, mode)
            if latest_run is None:
                return []

            query = (
                select(SnowScore, Resort)
                .join(Resort, Resort.id == SnowScore.resort_id)
                .where(
                    SnowScore.weekly_run_id == latest_run.id,
                    SnowScore.passed_gate.is_(True),
                )
            )
            if min_depth_cm is not None:
                query = query.where(SnowScore.depth_cm >= min_depth_cm)
            if max_altitude_m is not None:
                query = query.where(Resort.altitude_base_m <= max_altitude_m)
            if min_altitude_m is not None:
                query = query.where(Resort.altitude_base_m >= min_altitude_m)
            if countries:
                query = query.where(Resort.country.in_(countries))
            if min_score is not None:
                query = query.where(SnowScore.score_total >= min_score)
            query = query.order_by(SnowScore.score_total.desc()).limit(10)
            rows = (await session.execute(query)).all()
            return [
                {
                    "resort_id": str(resort.id),
                    "name": resort.name,
                    "country": resort.country,
                    "depth_cm": score.depth_cm,
                    "score": score.score_total,
                    "historical_reliability_pct": score.historical_reliability_pct,
                    "rank": score.rank,
                }
                for score, resort in rows
            ]

    @tool
    async def fetch_live_snow(resort_id: str) -> dict[str, Any]:
        """Fetch current snow and forecast for one resort; limited to 10 calls per session."""
        if not await _reserve_live_call():
            raise ToolException("Live snow lookup limit reached for this session.")
        resort_uuid = _parse_uuid(resort_id)
        async with AsyncSessionLocal() as session:
            resort = await session.get(Resort, resort_uuid)
        if resort is None:
            raise ToolException("Unknown resort.")
        async with OpenMeteoClient() as client:
            conditions, forecast = await asyncio.gather(
                client.get_current_conditions(
                    resort.id, resort.latitude, resort.longitude
                ),
                client.get_5day_forecast(
                    resort.id, resort.latitude, resort.longitude
                ),
            )
        return {
            "resort": resort.name,
            "snow_depth_cm": conditions.snow_depth_cm,
            "temperature_c": conditions.temperature_c,
            "forecast": [
                {
                    "date": day.date.isoformat(),
                    "temp_max_c": day.temp_max_c,
                    "rain_sum_mm": day.rain_sum_mm,
                    "snowfall_sum_cm": day.snowfall_sum_cm,
                }
                for day in forecast.days
            ],
            "source": "live Open-Meteo",
        }

    @tool
    async def fetch_webcam(resort_id: str) -> dict[str, Any]:
        """Return the latest cached webcam classification, capturing a fresh image if absent."""
        resort_uuid = _parse_uuid(resort_id)
        async with AsyncSessionLocal() as session:
            snapshot_result = await session.execute(
                select(WebcamSnapshot)
                .where(WebcamSnapshot.resort_id == resort_uuid)
                .order_by(WebcamSnapshot.captured_at.desc())
                .limit(1)
            )
            snapshot = snapshot_result.scalars().first()
            if snapshot is not None:
                return {
                    "classification": snapshot.classification,
                    "description": snapshot.description,
                    "file_path": snapshot.file_path,
                    "captured_at": snapshot.captured_at.isoformat(),
                    "cached": True,
                }
            resort = await session.get(Resort, resort_uuid)
        if resort is None:
            raise ToolException("Unknown resort.")
        async with WebcamClassifier() as classifier:
            result = await classifier.classify(resort, uuid.uuid4())
        return {
            "classification": result.classification,
            "description": result.description,
            "file_path": result.file_path,
            "captured_at": result.captured_at.isoformat(),
            "cached": False,
        }

    @tool
    async def search_flights_readonly(
        resort_id: str,
        outbound_date: date,
        return_date: date,
        origin_airport: str | None = None,
        destination_airports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search current flight offers for a gate-passing resort; this tool cannot book.

        origin_airport defaults to the traveller's preferred airport.
        """
        if not settings.amadeus_client_id or not settings.amadeus_client_secret:
            raise ToolException("Read-only Amadeus flight search is not configured.")
        origin_airport = (origin_airport or prefs.origin_airport).upper()
        city = get_city(prefs.residence_city)
        if city is None or origin_airport not in city.airports:
            raise ToolException(
                f"Origin must be an airport serving {prefs.residence_city}."
            )
        if return_date <= outbound_date:
            raise ToolException("Return date must be after outbound date.")
        resort_uuid = _parse_uuid(resort_id)
        async with AsyncSessionLocal() as session:
            resort, run = await _get_latest_passing_resort(session, resort_uuid, mode)
        if resort is None:
            raise ToolException("Travel search is only available for a resort that passed the snow gate.")
        destinations = destination_airports or resort.nearest_airports
        destinations = [code for code in destinations if code in resort.nearest_airports]
        if not destinations:
            raise ToolException("No valid destination airport is available for this resort.")

        def _search() -> list[dict[str, Any]]:
            client = Client(
                client_id=settings.amadeus_client_id,
                client_secret=settings.amadeus_client_secret,
            )
            offers: list[dict[str, Any]] = []
            for destination in destinations:
                params: dict[str, Any] = {
                    "originLocationCode": origin_airport,
                    "destinationLocationCode": destination,
                    "departureDate": outbound_date.isoformat(),
                    "returnDate": return_date.isoformat(),
                    "adults": 1,
                    "currencyCode": "GBP",
                    "max": 5,
                }
                if prefs.excluded_airlines:
                    params["excludedAirlineCodes"] = ",".join(prefs.excluded_airlines)
                response = client.shopping.flight_offers_search.get(**params)
                offers.extend(response.data)
            return sorted(
                (_with_gbp_total(offer) for offer in offers if _is_gbp_offer(offer)),
                key=lambda offer: Decimal(offer["total_gbp"]),
            )[:5]

        try:
            offers = await asyncio.to_thread(_search)
        except Exception as exc:
            logger.warning("Amadeus read-only search failed: %s", exc)
            raise ToolException("Live flight search is temporarily unavailable.") from exc
        state["flight_options"] = offers
        if store is not None:
            await store.save_flight_options(session_id, offers)
        return {
            "resort": resort.name,
            "weekly_run_id": str(run.id),
            "origin_airport": origin_airport,
            "currency": "GBP",
            "offers": offers,
            "booking_available": False,
        }

    @tool
    async def search_hotels_readonly(
        resort_id: str,
        checkin: date,
        checkout: date,
        max_price_per_night_gbp: float | None = None,
    ) -> dict[str, Any]:
        """Search current hotel offers near a gate-passing resort; this tool cannot book."""
        if not settings.amadeus_client_id or not settings.amadeus_client_secret:
            raise ToolException("Read-only Amadeus hotel search is not configured.")
        if checkout <= checkin:
            raise ToolException("Checkout date must be after check-in.")
        resort_uuid = _parse_uuid(resort_id)
        async with AsyncSessionLocal() as session:
            resort, run = await _get_latest_passing_resort(session, resort_uuid, mode)
        if resort is None:
            raise ToolException("Travel search is only available for a resort that passed the snow gate.")
        max_nightly = (
            Decimal(str(max_price_per_night_gbp))
            if max_price_per_night_gbp is not None
            else None
        )

        def _search() -> list[dict[str, Any]]:
            client = Client(
                client_id=settings.amadeus_client_id,
                client_secret=settings.amadeus_client_secret,
            )
            return search_hotels(
                client,
                resort.latitude,
                resort.longitude,
                checkin,
                checkout,
                max_nightly,
            )

        try:
            hotels = await asyncio.to_thread(_search)
        except Exception as exc:
            logger.warning("Amadeus hotel search failed: %s", exc)
            raise ToolException("Live hotel search is temporarily unavailable.") from exc
        state["hotel_options"] = hotels
        if store is not None:
            await store.save_hotel_options(session_id, hotels)
        return {
            "resort": resort.name,
            "weekly_run_id": str(run.id),
            "currency": "GBP",
            "hotels": hotels,
            "booking_available": False,
        }

    @tool
    async def get_historical_reliability(resort_id: str) -> dict[str, Any]:
        """Read five-year weekend reliability from the latest weekly cache."""
        resort_uuid = _parse_uuid(resort_id)
        async with AsyncSessionLocal() as session:
            latest_run = await _get_latest_run(session, mode)
            if latest_run is None:
                return {"available": False}
            score_result = await session.execute(
                select(SnowScore).where(
                    SnowScore.weekly_run_id == latest_run.id,
                    SnowScore.resort_id == resort_uuid,
                )
            )
            score = score_result.scalars().first()
        if score is None:
            return {"available": False}
        return {
            "available": True,
            "historical_reliability_pct": score.historical_reliability_pct,
            "weekly_run_id": str(latest_run.id),
        }

    @tool
    async def create_plan_artifact(
        resort_id: str,
        outbound_date: date,
        return_date: date,
        flight_option: str,
        hotel_option: str,
        notes: str = "",
    ) -> dict[str, Any]:
        """Create one draft trip plan; this does not make or imply a booking."""
        if await _plan_created():
            raise ToolException("Only one plan artifact can be created per chat session.")
        if return_date <= outbound_date:
            raise ToolException("Return date must be after outbound date.")
        try:
            flight = json.loads(flight_option)
            hotel = json.loads(hotel_option)
        except json.JSONDecodeError as exc:
            raise ToolException("Flight and hotel selections must be valid JSON.") from exc
        if not isinstance(flight, dict) or not isinstance(hotel, dict):
            raise ToolException("Flight and hotel selections must be JSON objects.")
        if flight not in await _flight_options():
            raise ToolException(
                "The flight selection must match an offer returned by live search in this session."
            )
        if hotel not in await _hotel_options():
            raise ToolException(
                "The hotel selection must match an offer returned by hotel search in this session."
            )
        if hotel.get("checkin") != outbound_date.isoformat() or hotel.get(
            "checkout"
        ) != return_date.isoformat():
            raise ToolException("Hotel stay dates must match the trip dates.")
        async with AsyncSessionLocal() as session:
            resort, _ = await _get_latest_passing_resort(
                session, _parse_uuid(resort_id), mode
            )
            if resort is None:
                raise ToolException("A plan requires a resort that passed the snow gate.")
            flight_total = offer_total_gbp(flight)
            hotel_total = offer_total_gbp(hotel)
            if flight_total is None or hotel_total is None:
                raise ToolException(
                    "Flight and hotel selections must both carry explicit GBP prices."
                )
            estimated_total = flight_total + hotel_total
            plan = TripPlan(
                id=uuid.uuid4(),
                user_id=user_id,
                session_id=session_id,
                resort_id=resort.id,
                outbound_date=outbound_date,
                return_date=return_date,
                flight_option=flight,
                hotel_option=hotel,
                total_estimated_gbp=estimated_total,
                session_notes=notes[:4000] if notes else None,
                status="draft",
            )
            session.add(plan)
            await session.commit()
        state["plan_created"] = True
        if store is not None:
            await store.mark_plan_created(session_id)
        payload = plan_payload(plan, resort.name)
        failures = await dispatch_plan(payload, prefs)
        if failures:
            async with AsyncSessionLocal() as session:
                stored = await session.get(TripPlan, plan.id)
                if stored is not None:
                    stored.delivery_errors = failures
                    await session.commit()
        return {
            "plan_id": str(plan.id),
            "status": "draft",
            "resort": resort.name,
            "flight_total_gbp": str(flight_total),
            "hotel_total_gbp": str(hotel_total),
            "total_estimated_gbp": str(estimated_total),
            "delivery_failures": failures,
            "message": "Draft plan created. No booking or payment was made.",
        }
        return {
            "plan_id": str(plan.id),
            "status": "draft",
            "resort": resort.name,
            "total_estimated_gbp": (
                str(estimated_total) if estimated_total is not None else None
            ),
            "message": "Draft plan created. No booking or payment was made.",
        }

    return [
        filter_results,
        fetch_live_snow,
        fetch_webcam,
        search_flights_readonly,
        search_hotels_readonly,
        get_historical_reliability,
        create_plan_artifact,
    ]


async def _get_latest_run(session, mode: str) -> WeeklyRun | None:
    result = await session.execute(
        select(WeeklyRun)
        .where(
            WeeklyRun.status == "complete",
            WeeklyRun.mode == mode,
        )
        .order_by(WeeklyRun.run_date.desc(), WeeklyRun.started_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _get_latest_passing_resort(
    session, resort_id: uuid.UUID, mode: str
) -> tuple[Resort | None, WeeklyRun | None]:
    latest_run = await _get_latest_run(session, mode)
    if latest_run is None:
        return None, None
    result = await session.execute(
        select(Resort)
        .join(SnowScore, SnowScore.resort_id == Resort.id)
        .where(
            Resort.id == resort_id,
            SnowScore.weekly_run_id == latest_run.id,
            SnowScore.passed_gate.is_(True),
        )
    )
    return result.scalars().first(), latest_run


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError) as exc:
        raise ToolException("Invalid resort ID.") from exc


def _is_gbp_offer(offer: dict[str, Any]) -> bool:
    price = offer.get("price") or {}
    return price.get("currency") == "GBP" and price.get("grandTotal") is not None


def _with_gbp_total(offer: dict[str, Any]) -> dict[str, Any]:
    """Add explicit top-level GBP fields so plans never infer a price or currency."""
    return {**offer, "currency": "GBP", "total_gbp": str(offer["price"]["grandTotal"])}


