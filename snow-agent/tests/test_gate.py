"""
Property-based tests for the Snow Score Engine gate logic.

All four gate properties are tested using Hypothesis @given with
@settings(max_examples=200, deadline=None) to avoid flaky timeouts.

Properties covered
------------------
Property 1 — Depth Gate Exclusion    Validates: Requirements 8.1, 8.3
Property 2 — Open Run Gate           Validates: Requirement 8.2
Property 3 — Rain Forecast Gate      Validates: Requirement 8.4
Property 4 — Warm Forecast Gate      Validates: Requirement 8.5

Spec test cases
---------------
test_gate_excludes_shallow_sports_depth   (depth=35cm, mode=sports)
test_gate_excludes_rain_forecast          (depth=60cm, 3-day rain)
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import assume, given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from app.scoring.engine import apply_gate
from app.scoring.schemas import DayForecast, GateInput

# ---------------------------------------------------------------------------
# Thresholds (mirrors app/settings.py defaults — kept explicit so tests are
# self-contained and don't depend on a .env file being present)
# ---------------------------------------------------------------------------
SPORTS_DEPTH_GATE_CM: float = 40.0
TOURISM_DEPTH_GATE_CM: float = 15.0
RAIN_THRESHOLD_MM: float = 0.1
WARM_THRESHOLD_C: float = 3.0


# ---------------------------------------------------------------------------
# Shared Hypothesis strategies
# ---------------------------------------------------------------------------

def _safe_day_strategy() -> st.SearchStrategy[DayForecast]:
    """
    Build a DayForecast that satisfies all negative-condition thresholds
    (no rain, not warm), so it cannot trigger rain or warm gates.
    """
    return st.builds(
        DayForecast,
        date=st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31)),
        temp_max_c=st.floats(min_value=-30.0, max_value=WARM_THRESHOLD_C, allow_nan=False),
        rain_sum_mm=st.floats(min_value=0.0, max_value=RAIN_THRESHOLD_MM, allow_nan=False),
        snowfall_sum_cm=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    )


def _five_safe_days() -> st.SearchStrategy[list[DayForecast]]:
    """Generate exactly 5 safe (non-negative-condition) DayForecast entries."""
    return st.lists(_safe_day_strategy(), min_size=5, max_size=5)


def _rainy_day_strategy() -> st.SearchStrategy[DayForecast]:
    """Build a DayForecast with rain_sum_mm strictly above the threshold."""
    return st.builds(
        DayForecast,
        date=st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31)),
        temp_max_c=st.floats(min_value=-30.0, max_value=WARM_THRESHOLD_C, allow_nan=False),
        rain_sum_mm=st.floats(
            min_value=RAIN_THRESHOLD_MM + 0.01, max_value=200.0, allow_nan=False
        ),
        snowfall_sum_cm=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    )


def _warm_day_strategy() -> st.SearchStrategy[DayForecast]:
    """
    Build a DayForecast with temp_max_c strictly above the threshold and
    rain_sum_mm at or below the threshold (so the rain gate cannot fire first).
    """
    return st.builds(
        DayForecast,
        date=st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31)),
        temp_max_c=st.floats(
            min_value=WARM_THRESHOLD_C + 0.01, max_value=50.0, allow_nan=False
        ),
        rain_sum_mm=st.floats(min_value=0.0, max_value=RAIN_THRESHOLD_MM, allow_nan=False),
        snowfall_sum_cm=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    )


def _forecast_with_at_least_one_rainy_day() -> st.SearchStrategy[list[DayForecast]]:
    """
    Generate a 5-day forecast that contains at least 1 rainy day (remaining
    days are safe so only the rain gate can fire).
    """
    return st.integers(min_value=0, max_value=4).flatmap(
        lambda rainy_index: st.lists(
            _safe_day_strategy(), min_size=5, max_size=5
        ).flatmap(
            lambda safe_days: _rainy_day_strategy().map(
                lambda rainy: [
                    rainy if i == rainy_index else safe_days[i] for i in range(5)
                ]
            )
        )
    )


def _forecast_with_at_least_one_warm_day() -> st.SearchStrategy[list[DayForecast]]:
    """
    Generate a 5-day forecast that contains at least 1 warm day and NO rainy
    days, so only the warm gate can fire (rain gate is already satisfied).
    """
    return st.integers(min_value=0, max_value=4).flatmap(
        lambda warm_index: st.lists(
            _safe_day_strategy(), min_size=5, max_size=5
        ).flatmap(
            lambda safe_days: _warm_day_strategy().map(
                lambda warm: [
                    warm if i == warm_index else safe_days[i] for i in range(5)
                ]
            )
        )
    )


# ---------------------------------------------------------------------------
# Property 1 — Depth Gate Exclusion (Mode-Parametric)
# Validates: Requirements 8.1, 8.3
# ---------------------------------------------------------------------------

@given(
    mode=st.sampled_from(["sports", "tourism"]),
    depth=st.floats(min_value=0.0, max_value=500.0, allow_nan=False),
    forecast=_five_safe_days(),
)
@h_settings(max_examples=200, deadline=None)
def test_depth_gate_exclusion(mode: str, depth: float, forecast: list[DayForecast]) -> None:
    """
    Property 1: For any mode and any depth strictly below the mode-appropriate
    gate threshold, apply_gate SHALL return passed=False with
    reason='depth_below_threshold'.

    Validates: Requirements 8.1, 8.3
    """
    threshold = SPORTS_DEPTH_GATE_CM if mode == "sports" else TOURISM_DEPTH_GATE_CM
    assume(depth < threshold)

    result = apply_gate(
        GateInput(
            mode=mode,
            snow_depth_cm=depth,
            has_open_run=None,  # unknown — must not cause no_open_run to fire
            forecast_days=forecast,
        )
    )

    assert result.passed is False
    assert result.reason == "depth_below_threshold"


# ---------------------------------------------------------------------------
# Property 2 — Open Run Gate (Sports Mode)
# Validates: Requirement 8.2
# ---------------------------------------------------------------------------

@given(
    depth=st.floats(
        min_value=SPORTS_DEPTH_GATE_CM, max_value=1000.0, allow_nan=False
    ),
    forecast=_five_safe_days(),
)
@h_settings(max_examples=200, deadline=None)
def test_open_run_gate(depth: float, forecast: list[DayForecast]) -> None:
    """
    Property 2: For sports mode with depth ≥ 40cm and has_open_run=False,
    apply_gate SHALL return passed=False with reason='no_open_run', regardless
    of forecast.

    Validates: Requirement 8.2
    """
    result = apply_gate(
        GateInput(
            mode="sports",
            snow_depth_cm=depth,
            has_open_run=False,
            forecast_days=forecast,
        )
    )

    assert result.passed is False
    assert result.reason == "no_open_run"


# ---------------------------------------------------------------------------
# Property 3 — Rain Forecast Gate
# Validates: Requirement 8.4
# ---------------------------------------------------------------------------

@given(
    mode=st.sampled_from(["sports", "tourism"]),
    forecast=_forecast_with_at_least_one_rainy_day(),
)
@h_settings(max_examples=200, deadline=None)
def test_rain_forecast_gate(mode: str, forecast: list[DayForecast]) -> None:
    """
    Property 3: For any 5-day forecast containing ≥1 day with rain_sum_mm > 0.1,
    apply_gate SHALL return passed=False with reason='rain_in_forecast',
    regardless of mode (depth and open-run conditions are satisfied).

    Validates: Requirement 8.4
    """
    # Use a depth that comfortably clears both mode thresholds so only the
    # rain condition determines the outcome.
    safe_depth = max(SPORTS_DEPTH_GATE_CM, TOURISM_DEPTH_GATE_CM) + 10.0

    result = apply_gate(
        GateInput(
            mode=mode,
            snow_depth_cm=safe_depth,
            has_open_run=None,  # unknown — depth gate and open-run gate both pass
            forecast_days=forecast,
        )
    )

    assert result.passed is False
    assert result.reason == "rain_in_forecast"


# ---------------------------------------------------------------------------
# Property 4 — Warm Forecast Gate
# Validates: Requirement 8.5
# ---------------------------------------------------------------------------

@given(
    mode=st.sampled_from(["sports", "tourism"]),
    forecast=_forecast_with_at_least_one_warm_day(),
)
@h_settings(max_examples=200, deadline=None)
def test_warm_forecast_gate(mode: str, forecast: list[DayForecast]) -> None:
    """
    Property 4: For any 5-day forecast containing ≥1 warm day (temp_max_c > 3.0)
    and no rainy days, apply_gate SHALL return passed=False with
    reason='warm_temperature_forecast' (depth and rain conditions are satisfied).

    Validates: Requirement 8.5
    """
    safe_depth = max(SPORTS_DEPTH_GATE_CM, TOURISM_DEPTH_GATE_CM) + 10.0

    result = apply_gate(
        GateInput(
            mode=mode,
            snow_depth_cm=safe_depth,
            has_open_run=None,
            forecast_days=forecast,
        )
    )

    assert result.passed is False
    assert result.reason == "warm_temperature_forecast"


# ---------------------------------------------------------------------------
# Spec test cases (concrete examples)
# ---------------------------------------------------------------------------

def _make_forecast(
    *,
    rain_mm: float = 0.0,
    temp_c: float = 0.0,
    snowfall_cm: float = 0.0,
    count: int = 5,
) -> list[DayForecast]:
    """Build a uniform 5-day forecast for use in spec test cases."""
    return [
        DayForecast(
            date=date(2025, 1, i + 1),
            temp_max_c=temp_c,
            rain_sum_mm=rain_mm,
            snowfall_sum_cm=snowfall_cm,
        )
        for i in range(count)
    ]


def test_gate_excludes_shallow_sports_depth() -> None:
    """
    Spec test case: depth=35cm, mode=sports → excluded (threshold is 40cm).
    """
    result = apply_gate(
        GateInput(
            mode="sports",
            snow_depth_cm=35.0,
            has_open_run=None,
            forecast_days=_make_forecast(),
        )
    )

    assert result.passed is False
    assert result.reason == "depth_below_threshold"


def test_gate_excludes_rain_forecast() -> None:
    """
    Spec test case: depth=60cm, 3-day rain (5mm each) → excluded with
    reason='rain_in_forecast'.
    """
    # Build 2 clean days followed by 3 rainy days
    clean_days = _make_forecast(rain_mm=0.0, temp_c=0.0, count=2)
    rainy_days = _make_forecast(rain_mm=5.0, temp_c=0.0, count=3)
    forecast = clean_days + rainy_days

    result = apply_gate(
        GateInput(
            mode="sports",
            snow_depth_cm=60.0,
            has_open_run=None,
            forecast_days=forecast,
        )
    )

    assert result.passed is False
    assert result.reason == "rain_in_forecast"
