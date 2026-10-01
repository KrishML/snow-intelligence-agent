"""
Integration tests for the Open-Meteo async client.

All HTTP calls are intercepted by pytest-httpx (``httpx_mock`` fixture) — no
real network requests are ever made.  ``asyncio.sleep`` is patched where retry
behaviour is exercised to keep the test suite fast.

Tests
-----
1.  get_current_conditions returns correct depth and temperature
2.  get_current_conditions handles all-null snow depth
3.  get_5day_forecast returns 5 DayForecast entries with correct values
4.  get_5day_forecast treats null values as 0.0
5.  get_historical_reliability returns correct percentage (sports mode)
6.  get_historical_reliability handles tourism mode threshold
7.  Retry on 5xx — succeeds on second attempt
8.  Raises OpenMeteoFetchError after all retries exhausted
9.  Raises OpenMeteoFetchError immediately on 4xx (no retry)

Requirements: 7.2, 29.2
"""

from __future__ import annotations

import re
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

from app.clients.open_meteo import OpenMeteoClient, OpenMeteoFetchError

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# Regex patterns match the base URL regardless of query parameters.
FORECAST_URL_RE = re.compile(r"https://api\.open-meteo\.com/v1/forecast.*")
ARCHIVE_URL_RE = re.compile(r"https://archive-api\.open-meteo\.com/v1/archive.*")

RESORT_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
LAT, LON = 47.0, 11.0


# ---------------------------------------------------------------------------
# Test 1: get_current_conditions returns correct depth and temperature
# ---------------------------------------------------------------------------


async def test_get_current_conditions_returns_correct_values(
    httpx_mock: HTTPXMock,
) -> None:
    """
    The client returns the last non-null snow_depth (× 100 → cm) and its
    corresponding temperature.

    Mock response has snow_depth [null, 0.45, 0.47] → last non-null is index 2
    (0.47 m × 100 = 47.0 cm), temperature at index 2 is -2.1 °C.

    Validates: Requirements 7.2, 29.2
    """
    httpx_mock.add_response(
        url=FORECAST_URL_RE,
        json={
            "hourly": {
                "time": ["2025-01-10T00:00", "2025-01-10T01:00", "2025-01-10T02:00"],
                "snow_depth": [None, 0.45, 0.47],
                "temperature_2m": [None, -2.5, -2.1],
            }
        },
    )

    async with httpx.AsyncClient() as http_client:
        client = OpenMeteoClient(http_client=http_client)
        conditions = await client.get_current_conditions(RESORT_ID, LAT, LON)

    assert conditions.snow_depth_cm == 47.0
    assert conditions.temperature_c == -2.1


# ---------------------------------------------------------------------------
# Test 2: get_current_conditions handles all-null snow depth
# ---------------------------------------------------------------------------


async def test_get_current_conditions_all_null_returns_zeros(
    httpx_mock: HTTPXMock,
) -> None:
    """
    When all snow_depth values are null the client returns 0.0 for both
    depth and temperature.

    Validates: Requirements 7.2, 29.2
    """
    httpx_mock.add_response(
        url=FORECAST_URL_RE,
        json={
            "hourly": {
                "time": ["2025-01-10T00:00", "2025-01-10T01:00"],
                "snow_depth": [None, None],
                "temperature_2m": [-1.0, -1.5],
            }
        },
    )

    async with httpx.AsyncClient() as http_client:
        client = OpenMeteoClient(http_client=http_client)
        conditions = await client.get_current_conditions(RESORT_ID, LAT, LON)

    assert conditions.snow_depth_cm == 0.0
    assert conditions.temperature_c == 0.0


# ---------------------------------------------------------------------------
# Test 3: get_5day_forecast returns 5 DayForecast entries with correct values
# ---------------------------------------------------------------------------


async def test_get_5day_forecast_returns_correct_entries(
    httpx_mock: HTTPXMock,
) -> None:
    """
    The client returns exactly 5 DayForecast objects with values taken
    directly from the daily payload.

    Validates: Requirements 7.2, 29.2
    """
    httpx_mock.add_response(
        url=FORECAST_URL_RE,
        json={
            "daily": {
                "time": [
                    "2025-01-10",
                    "2025-01-11",
                    "2025-01-12",
                    "2025-01-13",
                    "2025-01-14",
                ],
                "temperature_2m_max": [1.5, 3.2, -1.0, 0.5, 2.0],
                "rain_sum": [0.0, 0.2, 0.0, 0.0, 0.0],
                "snowfall_sum": [5.0, 0.0, 10.0, 3.0, 0.0],
            }
        },
    )

    async with httpx.AsyncClient() as http_client:
        client = OpenMeteoClient(http_client=http_client)
        forecast = await client.get_5day_forecast(RESORT_ID, LAT, LON)

    assert len(forecast.days) == 5
    assert forecast.days[0].temp_max_c == 1.5
    assert forecast.days[1].rain_sum_mm == 0.2
    assert forecast.days[2].snowfall_sum_cm == 10.0


# ---------------------------------------------------------------------------
# Test 4: get_5day_forecast treats null values as 0.0
# ---------------------------------------------------------------------------


async def test_get_5day_forecast_nulls_become_zero(
    httpx_mock: HTTPXMock,
) -> None:
    """
    Null values in any daily field are coerced to 0.0.

    Validates: Requirements 7.2, 29.2
    """
    httpx_mock.add_response(
        url=FORECAST_URL_RE,
        json={
            "daily": {
                "time": [
                    "2025-01-10",
                    "2025-01-11",
                    "2025-01-12",
                    "2025-01-13",
                    "2025-01-14",
                ],
                "temperature_2m_max": [None, 1.0, None, 0.5, 2.0],
                "rain_sum": [0.0, None, 0.0, 0.0, 0.0],
                "snowfall_sum": [5.0, 0.0, None, 3.0, 0.0],
            }
        },
    )

    async with httpx.AsyncClient() as http_client:
        client = OpenMeteoClient(http_client=http_client)
        forecast = await client.get_5day_forecast(RESORT_ID, LAT, LON)

    assert len(forecast.days) == 5
    # index 0: temperature null → 0.0
    assert forecast.days[0].temp_max_c == 0.0
    # index 1: rain null → 0.0
    assert forecast.days[1].rain_sum_mm == 0.0
    # index 2: snowfall null → 0.0
    assert forecast.days[2].snowfall_sum_cm == 0.0
    # non-null values are preserved
    assert forecast.days[1].temp_max_c == 1.0
    assert forecast.days[3].snowfall_sum_cm == 3.0


# ---------------------------------------------------------------------------
# Test 5: get_historical_reliability returns correct percentage (sports mode)
# ---------------------------------------------------------------------------


async def test_get_historical_reliability_sports_mode(
    httpx_mock: HTTPXMock,
) -> None:
    """
    Sports mode uses a 0.40 m threshold.  Of 5 weekend days in the mock data,
    4 have snow_depth >= 0.40 m → reliability = 4/5 × 100 = 80.0 %.

    Weekend days and depths:
        2025-01-11 (Sat) → 0.45 ✓
        2025-01-12 (Sun) → 0.42 ✓
        2025-01-18 (Sat) → 0.50 ✓
        2025-01-19 (Sun) → 0.38 ✗  (below 0.40)
        2025-01-25 (Sat) → 0.55 ✓

    Validates: Requirements 7.2, 29.2
    """
    httpx_mock.add_response(
        url=ARCHIVE_URL_RE,
        json={
            "daily": {
                "time": [
                    "2025-01-10",  # Fri
                    "2025-01-11",  # Sat ✓
                    "2025-01-12",  # Sun ✓
                    "2025-01-13",  # Mon
                    "2025-01-17",  # Fri
                    "2025-01-18",  # Sat ✓
                    "2025-01-19",  # Sun ✗
                    "2025-01-20",  # Mon
                    "2025-01-24",  # Fri
                    "2025-01-25",  # Sat ✓
                ],
                "snow_depth": [
                    0.20,
                    0.45,
                    0.42,
                    0.10,
                    0.05,
                    0.50,
                    0.38,
                    0.10,
                    0.15,
                    0.55,
                ],
            }
        },
    )

    async with httpx.AsyncClient() as http_client:
        client = OpenMeteoClient(http_client=http_client)
        reliability = await client.get_historical_reliability(
            RESORT_ID, LAT, LON, mode="sports"
        )

    assert reliability == 80.0


# ---------------------------------------------------------------------------
# Test 6: get_historical_reliability handles tourism mode threshold
# ---------------------------------------------------------------------------


async def test_get_historical_reliability_tourism_mode(
    httpx_mock: HTTPXMock,
) -> None:
    """
    Tourism mode uses a 0.15 m threshold.  All 5 weekend days have
    snow_depth >= 0.15 m → reliability = 5/5 × 100 = 100.0 %.

    Validates: Requirements 7.2, 29.2
    """
    httpx_mock.add_response(
        url=ARCHIVE_URL_RE,
        json={
            "daily": {
                "time": [
                    "2025-01-10",  # Fri
                    "2025-01-11",  # Sat ✓
                    "2025-01-12",  # Sun ✓
                    "2025-01-13",  # Mon
                    "2025-01-17",  # Fri
                    "2025-01-18",  # Sat ✓
                    "2025-01-19",  # Sun ✓
                    "2025-01-20",  # Mon
                    "2025-01-24",  # Fri
                    "2025-01-25",  # Sat ✓
                ],
                "snow_depth": [
                    0.20,
                    0.45,
                    0.42,
                    0.10,
                    0.05,
                    0.50,
                    0.38,
                    0.10,
                    0.15,
                    0.55,
                ],
            }
        },
    )

    async with httpx.AsyncClient() as http_client:
        client = OpenMeteoClient(http_client=http_client)
        reliability = await client.get_historical_reliability(
            RESORT_ID, LAT, LON, mode="tourism"
        )

    assert reliability == 100.0


# ---------------------------------------------------------------------------
# Test 7: Retry on 5xx — succeeds on second attempt
# ---------------------------------------------------------------------------


async def test_retry_on_5xx_succeeds_on_second_attempt(
    httpx_mock: HTTPXMock,
) -> None:
    """
    The client retries on HTTP 500 and returns the correct result on the
    second attempt.  ``asyncio.sleep`` is patched to avoid real delays.

    Validates: Requirements 7.2, 29.2
    """
    # First call → 500, second call → 200 with valid data.
    httpx_mock.add_response(url=FORECAST_URL_RE, status_code=500)
    httpx_mock.add_response(
        url=FORECAST_URL_RE,
        json={
            "hourly": {
                "time": ["2025-01-10T00:00"],
                "snow_depth": [0.30],
                "temperature_2m": [-5.0],
            }
        },
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        async with httpx.AsyncClient() as http_client:
            client = OpenMeteoClient(http_client=http_client)
            conditions = await client.get_current_conditions(RESORT_ID, LAT, LON)

    assert conditions.snow_depth_cm == 30.0
    assert conditions.temperature_c == -5.0


# ---------------------------------------------------------------------------
# Test 8: Raises OpenMeteoFetchError after all retries exhausted
# ---------------------------------------------------------------------------


async def test_raises_fetch_error_after_all_retries_exhausted(
    httpx_mock: HTTPXMock,
) -> None:
    """
    When all 3 attempts return HTTP 500, the client raises OpenMeteoFetchError.
    ``asyncio.sleep`` is patched to avoid real delays.

    Validates: Requirements 7.2, 29.2
    """
    # Three 500 responses — one per attempt (max_retries=3 from settings).
    httpx_mock.add_response(url=FORECAST_URL_RE, status_code=500)
    httpx_mock.add_response(url=FORECAST_URL_RE, status_code=500)
    httpx_mock.add_response(url=FORECAST_URL_RE, status_code=500)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        async with httpx.AsyncClient() as http_client:
            client = OpenMeteoClient(http_client=http_client)
            with pytest.raises(OpenMeteoFetchError):
                await client.get_current_conditions(RESORT_ID, LAT, LON)


# ---------------------------------------------------------------------------
# Test 9: Raises OpenMeteoFetchError immediately on 4xx (no retry)
# ---------------------------------------------------------------------------


async def test_raises_fetch_error_immediately_on_4xx(
    httpx_mock: HTTPXMock,
) -> None:
    """
    A 4xx response is not retried — the client raises OpenMeteoFetchError
    after exactly one HTTP request.

    Validates: Requirements 7.2, 29.2
    """
    httpx_mock.add_response(url=FORECAST_URL_RE, status_code=404)

    async with httpx.AsyncClient() as http_client:
        client = OpenMeteoClient(http_client=http_client)
        with pytest.raises(OpenMeteoFetchError):
            await client.get_current_conditions(RESORT_ID, LAT, LON)

    # Only one request was made — 4xx is not retried.
    assert len(httpx_mock.get_requests()) == 1
