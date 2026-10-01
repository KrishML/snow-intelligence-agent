"""
Async Open-Meteo client for fetching current conditions,
5-day forecasts, and historical weekend reliability data.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import httpx

from app.settings import settings


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ResortConditions:
    """Current settled snow depth and temperature for a resort."""

    resort_id: uuid.UUID
    snow_depth_cm: float  # converted from metres × 100
    temperature_c: float  # temperature at 2 m above ground


@dataclass
class DayForecast:
    """Single-day forecast values."""

    date: date
    temp_max_c: float
    rain_sum_mm: float
    snowfall_sum_cm: float  # already in cm from the API


@dataclass
class ForecastData:
    """5-day forecast for a resort."""

    resort_id: uuid.UUID
    days: list[DayForecast]  # exactly 5 entries


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class OpenMeteoFetchError(Exception):
    """Raised when an Open-Meteo request fails after all retries."""

    def __init__(self, resort_id: uuid.UUID, endpoint: str, status_code: int) -> None:
        self.resort_id = resort_id
        self.endpoint = endpoint
        self.status_code = status_code
        super().__init__(
            f"Failed to fetch {endpoint} for resort {resort_id}: HTTP {status_code}"
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OpenMeteoClient:
    """Async client for the Open-Meteo weather API.

    Can be used as an async context manager::

        async with OpenMeteoClient() as client:
            conditions = await client.get_current_conditions(resort_id, lat, lon)

    When constructed without an ``http_client``, the instance owns the
    underlying :class:`httpx.AsyncClient` and closes it on ``__aexit__``.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient()
        self._sem = semaphore or asyncio.Semaphore(settings.open_meteo_concurrency)

    # ------------------------------------------------------------------
    # Async context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "OpenMeteoClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_with_retry(
        self,
        url: str,
        params: dict,
        resort_id: uuid.UUID,
    ) -> dict:
        """GET *url* with *params*, retrying on 5xx / timeout with backoff.

        Returns the parsed JSON dict on HTTP 200.
        Raises :class:`OpenMeteoFetchError` on unrecoverable failure.
        """
        max_attempts = settings.open_meteo_max_retries
        timeout = settings.open_meteo_timeout_seconds

        for attempt in range(max_attempts):
            try:
                response = await self._client.get(
                    url,
                    params=params,
                    timeout=timeout,
                )
            except httpx.TimeoutException:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2**attempt + random.uniform(0, 1))
                    continue
                raise OpenMeteoFetchError(resort_id, url, 0)

            if response.status_code == 200:
                return response.json()

            if 400 <= response.status_code < 500:
                # Client errors are not retryable.
                raise OpenMeteoFetchError(resort_id, url, response.status_code)

            # 5xx — retry if attempts remain.
            if attempt < max_attempts - 1:
                await asyncio.sleep(2**attempt + random.uniform(0, 1))
            else:
                raise OpenMeteoFetchError(resort_id, url, response.status_code)

        # Should never reach here, but satisfy the type checker.
        raise OpenMeteoFetchError(resort_id, url, 0)  # pragma: no cover

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_current_conditions(
        self,
        resort_id: uuid.UUID,
        lat: float,
        lon: float,
    ) -> ResortConditions:
        """Return the most recent non-null snow depth (in cm) and its
        corresponding temperature for *resort_id*.

        Calls ``GET /v1/forecast`` with ``hourly=snow_depth,temperature_2m``
        and ``forecast_days=1``.  Searches backwards through the hourly list
        for the last non-null ``snow_depth`` value and returns the
        ``temperature_2m`` at the same index.  If all values are null,
        returns 0.0 for both.
        """
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "snow_depth,temperature_2m",
            "forecast_days": 1,
        }

        async with self._sem:
            data = await self._fetch_with_retry(
                settings.open_meteo_forecast_url, params, resort_id
            )

        hourly = data.get("hourly", {})
        snow_depths: list = hourly.get("snow_depth", [])
        temperatures: list = hourly.get("temperature_2m", [])

        # Find the last non-null snow_depth index.
        last_idx: int | None = None
        for i in range(len(snow_depths) - 1, -1, -1):
            if snow_depths[i] is not None:
                last_idx = i
                break

        if last_idx is None:
            return ResortConditions(
                resort_id=resort_id,
                snow_depth_cm=0.0,
                temperature_c=0.0,
            )

        snow_depth_m = snow_depths[last_idx]
        temp = temperatures[last_idx] if last_idx < len(temperatures) else None

        return ResortConditions(
            resort_id=resort_id,
            snow_depth_cm=snow_depth_m * 100,  # metres → centimetres
            temperature_c=temp if temp is not None else 0.0,
        )

    async def get_5day_forecast(
        self,
        resort_id: uuid.UUID,
        lat: float,
        lon: float,
    ) -> ForecastData:
        """Return a 5-day daily forecast for *resort_id*.

        Calls ``GET /v1/forecast`` with
        ``daily=temperature_2m_max,rain_sum,snowfall_sum`` and
        ``forecast_days=5``.  Null values in the response are treated as 0.0.
        """
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,rain_sum,snowfall_sum",
            "forecast_days": 5,
        }

        async with self._sem:
            data = await self._fetch_with_retry(
                settings.open_meteo_forecast_url, params, resort_id
            )

        daily = data.get("daily", {})
        times: list[str] = daily.get("time", [])
        temp_maxes: list = daily.get("temperature_2m_max", [])
        rain_sums: list = daily.get("rain_sum", [])
        snowfall_sums: list = daily.get("snowfall_sum", [])

        days: list[DayForecast] = []
        for i, t in enumerate(times):
            days.append(
                DayForecast(
                    date=date.fromisoformat(t),
                    temp_max_c=temp_maxes[i] if i < len(temp_maxes) and temp_maxes[i] is not None else 0.0,
                    rain_sum_mm=rain_sums[i] if i < len(rain_sums) and rain_sums[i] is not None else 0.0,
                    snowfall_sum_cm=snowfall_sums[i] if i < len(snowfall_sums) and snowfall_sums[i] is not None else 0.0,
                )
            )

        return ForecastData(resort_id=resort_id, days=days)

    async def get_historical_reliability(
        self,
        resort_id: uuid.UUID,
        lat: float,
        lon: float,
        mode: Literal["sports", "tourism"],
    ) -> float:
        """Return the percentage of weekend days in the last 5 years where
        snow depth met the mode-appropriate threshold.

        Calls ``GET /v1/archive`` with ``daily=snow_depth`` over the date
        range ``[today - 5*365 days, yesterday]``, then filters to Saturday
        (weekday 5) and Sunday (weekday 6) dates only.

        Returns a float in [0.0, 100.0].  Returns 0.0 if no weekend dates
        were found in the archive response.
        """
        today = date.today()
        start_date = today - timedelta(days=settings.historical_years * 365)
        end_date = today - timedelta(days=1)

        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "snow_depth",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }

        async with self._sem:
            data = await self._fetch_with_retry(
                settings.open_meteo_archive_url, params, resort_id
            )

        daily = data.get("daily", {})
        times: list[str] = daily.get("time", [])
        snow_depths: list = daily.get("snow_depth", [])

        # Determine threshold in metres.
        if mode == "sports":
            threshold_m = settings.sports_depth_gate_cm / 100  # 0.40 m
        else:
            threshold_m = settings.tourism_depth_gate_cm / 100  # 0.15 m

        total_weekend_days = 0
        matching_days = 0

        for i, t in enumerate(times):
            day = date.fromisoformat(t)
            if day.weekday() not in (5, 6):  # 5=Saturday, 6=Sunday
                continue

            total_weekend_days += 1
            depth = snow_depths[i] if i < len(snow_depths) else None
            if depth is not None and depth >= threshold_m:
                matching_days += 1

        if total_weekend_days == 0:
            return 0.0

        return (matching_days / total_weekend_days) * 100
