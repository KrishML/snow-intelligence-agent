"""
Property-based tests for the Snow Score Engine scoring functions.

All four scoring properties are tested using Hypothesis @given with
@settings(max_examples=200, deadline=None) to avoid flaky timeouts.

Properties covered
------------------
Property 5 — Score Total Invariant        Validates: Requirements 9.1, 9.7, 9.8
Property 6 — Depth Score Lookup           Validates: Requirements 9.2, 9.3
Property 7 — Forecast Score Bounds        Validates: Requirement 9.4
Property 8 — Historical Reliability       Validates: Requirement 9.5

Spec test cases
---------------
test_score_components_sum_correctly       (known inputs → correct total)
test_historical_reliability_calculation   (pct=80 → 20, pct=100 → 25, pct=0 → 0)
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import assume, given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from app.scoring.engine import compute_score, score_depth, score_forecast, score_historical
from app.scoring.schemas import DayForecast, ScoreInput


# ---------------------------------------------------------------------------
# Shared Hypothesis strategies
# ---------------------------------------------------------------------------

def _day_strategy() -> st.SearchStrategy[DayForecast]:
    """Build an arbitrary DayForecast with any valid field values."""
    return st.builds(
        DayForecast,
        date=st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31)),
        temp_max_c=st.floats(min_value=-40.0, max_value=40.0, allow_nan=False),
        rain_sum_mm=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
        snowfall_sum_cm=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
    )


def _stable_day_strategy() -> st.SearchStrategy[DayForecast]:
    """Build a DayForecast with no rain and no warm temperatures (stable conditions)."""
    return st.builds(
        DayForecast,
        date=st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31)),
        temp_max_c=st.floats(min_value=-40.0, max_value=3.0, allow_nan=False),
        rain_sum_mm=st.floats(min_value=0.0, max_value=0.1, allow_nan=False),
        snowfall_sum_cm=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
    )


# ---------------------------------------------------------------------------
# Property 5 — Score Total Invariant
# Validates: Requirements 9.1, 9.7, 9.8
# ---------------------------------------------------------------------------

@given(
    mode=st.sampled_from(["sports", "tourism"]),
    depth_cm=st.floats(min_value=0.0, max_value=500.0, allow_nan=False),
    forecast=st.lists(_day_strategy(), min_size=5, max_size=5),
    historical_pct=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    qualitative=st.integers(min_value=0, max_value=20),
)
@h_settings(max_examples=200, deadline=None)
def test_score_total_invariant(
    mode: str,
    depth_cm: float,
    forecast: list[DayForecast],
    historical_pct: float,
    qualitative: int,
) -> None:
    """
    Property 5: For any valid inputs, score_total SHALL equal
    min(100, score_depth + score_forecast + score_historical + score_qualitative)
    exactly, with no rounding error, and the result SHALL always be in [0, 100].

    Validates: Requirements 9.1, 9.7, 9.8
    """
    result = compute_score(
        ScoreInput(
            mode=mode,
            snow_depth_cm=depth_cm,
            forecast_days=forecast,
            historical_reliability_pct=historical_pct,
            qualitative_score=qualitative,
        )
    )

    expected_total = min(
        100,
        result.score_depth
        + result.score_forecast
        + result.score_historical
        + result.score_qualitative,
    )

    assert result.score_total == expected_total
    assert 0 <= result.score_total <= 100


# ---------------------------------------------------------------------------
# Property 6 — Depth Score Lookup Correctness (Mode-Parametric)
# Validates: Requirements 9.2, 9.3
# ---------------------------------------------------------------------------

@given(
    mode=st.sampled_from(["sports", "tourism"]),
    depth=st.floats(min_value=0.0, max_value=500.0, allow_nan=False),
)
@h_settings(max_examples=200, deadline=None)
def test_depth_score_lookup(mode: str, depth: float) -> None:
    """
    Property 6: For any mode and any snow depth value, score_depth SHALL return
    the score corresponding to the bracket in which that depth falls, with
    correct boundary handling (e.g., exactly 40cm in sports mode → 20 pts).

    sports:  <20 → 0, 20–39 → 10, 40–59 → 20, ≥60 → 30
    tourism: <10 → 0, 10–19 → 10, 20–29 → 20, ≥30 → 30

    Validates: Requirements 9.2, 9.3
    """
    result = score_depth(depth, mode)

    if mode == "sports":
        if depth < 20:
            assert result == 0
        elif depth < 40:
            assert result == 10
        elif depth < 60:
            assert result == 20
        else:
            assert result == 30
    else:  # tourism
        if depth < 10:
            assert result == 0
        elif depth < 20:
            assert result == 10
        elif depth < 30:
            assert result == 20
        else:
            assert result == 30


# ---------------------------------------------------------------------------
# Property 7 — Forecast Score Bounds and Priority
# Validates: Requirement 9.4
# ---------------------------------------------------------------------------

@given(forecast=st.lists(_day_strategy(), min_size=5, max_size=5))
@h_settings(max_examples=200, deadline=None)
def test_forecast_score_bounds_and_priority(forecast: list[DayForecast]) -> None:
    """
    Property 7: For any 5-day forecast, score_forecast SHALL:
    - return a value in [0, 30]
    - when negative conditions (rain > 0.1mm OR temp > 3.0°C) are present,
      score SHALL never exceed 15

    Validates: Requirement 9.4
    """
    result = score_forecast(forecast)

    # Always within [0, 30]
    assert 0 <= result <= 30

    has_rain = any(d.rain_sum_mm > 0.1 for d in forecast)
    has_warm = any(d.temp_max_c > 3.0 for d in forecast)

    if has_rain or has_warm:
        assert result <= 15


# ---------------------------------------------------------------------------
# Property 8 — Historical Reliability Formula
# Validates: Requirement 9.5
# ---------------------------------------------------------------------------

@given(pct=st.floats(min_value=0.0, max_value=100.0, allow_nan=False))
@h_settings(max_examples=200, deadline=None)
def test_historical_reliability_formula(pct: float) -> None:
    """
    Property 8: For any reliability percentage in [0, 100], score_historical
    SHALL equal round(pct * 0.25), clamped to [0, 25].

    Validates: Requirement 9.5
    """
    result = score_historical(pct)

    expected = max(0, min(25, round(pct * 0.25)))
    assert result == expected
    assert 0 <= result <= 25


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
    """Build a uniform N-day forecast for use in spec test cases."""
    return [
        DayForecast(
            date=date(2025, 1, i + 1),
            temp_max_c=temp_c,
            rain_sum_mm=rain_mm,
            snowfall_sum_cm=snowfall_cm,
        )
        for i in range(count)
    ]


def test_score_components_sum_correctly() -> None:
    """
    Spec test case: Known inputs produce components that sum correctly to total.

    Uses sports mode, 60cm depth (→30), stable+snow forecast (→30),
    80% historical (→20), qualitative placeholder (→10).
    Expected total = min(100, 30+30+20+10) = 90.
    """
    result = compute_score(
        ScoreInput(
            mode="sports",
            snow_depth_cm=60.0,
            forecast_days=_make_forecast(snowfall_cm=5.0),  # stable + snow → 30
            historical_reliability_pct=80.0,
            qualitative_score=10,
        )
    )

    assert result.score_depth == 30
    assert result.score_forecast == 30
    assert result.score_historical == 20
    assert result.score_qualitative == 10
    assert result.score_total == 90
    assert result.score_total == min(100, result.score_depth + result.score_forecast + result.score_historical + result.score_qualitative)


def test_score_components_cap_at_100() -> None:
    """
    Spec test case: Scores that would exceed 100 are capped at exactly 100.

    Uses sports mode, 60cm (→30), stable+snow (→30), 100% historical (→25),
    qualitative 20 (→20): raw sum = 105, capped to 100.
    """
    result = compute_score(
        ScoreInput(
            mode="sports",
            snow_depth_cm=60.0,
            forecast_days=_make_forecast(snowfall_cm=5.0),
            historical_reliability_pct=100.0,
            qualitative_score=20,
        )
    )

    raw_sum = result.score_depth + result.score_forecast + result.score_historical + result.score_qualitative
    assert raw_sum > 100  # confirm it would exceed without cap
    assert result.score_total == 100


def test_historical_reliability_calculation() -> None:
    """
    Spec test case: Verify known historical reliability percentage mappings.

    pct=0   → score=0
    pct=80  → score=20   (round(80 * 0.25) = round(20.0) = 20)
    pct=100 → score=25   (round(100 * 0.25) = round(25.0) = 25)
    pct=50  → score=13   (round(50 * 0.25) = round(12.5) = 12 or 13 depending on banker's rounding)
    """
    assert score_historical(0.0) == 0
    assert score_historical(80.0) == 20
    assert score_historical(100.0) == 25
    # round(12.5) uses banker's rounding in Python 3 → rounds to 12 (nearest even)
    assert score_historical(50.0) == round(50.0 * 0.25)
