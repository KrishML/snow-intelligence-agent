from __future__ import annotations

from typing import TYPE_CHECKING

from app.scoring.schemas import (
    DayForecast,
    GateInput,
    GateResult,
    ScoreInput,
    ScoreResult,
)
from app.settings import settings


# ---------------------------------------------------------------------------
# Gate logic
# ---------------------------------------------------------------------------

def apply_gate(input: GateInput) -> GateResult:
    """Apply mode-specific exclusion gates in priority order.

    Priority:
    1. Snow depth below mode threshold   → depth_below_threshold
    2. Sports mode with no open run      → no_open_run  (only when explicitly False)
    3. Rain in 5-day forecast            → rain_in_forecast
    4. Warm temperature in 5-day forecast → warm_temperature_forecast

    Returns immediately on the first failing condition.
    """
    # 1. Depth gate
    threshold = (
        settings.sports_depth_gate_cm
        if input.mode == "sports"
        else settings.tourism_depth_gate_cm
    )
    if input.snow_depth_cm < threshold:
        return GateResult(passed=False, reason="depth_below_threshold")

    # 2. Open run gate (sports only; None = unknown → skip)
    if input.mode == "sports" and input.has_open_run is False:
        return GateResult(passed=False, reason="no_open_run")

    # 3. Rain forecast gate
    if any(
        day.rain_sum_mm > settings.forecast_rain_exclude_threshold_mm
        for day in input.forecast_days
    ):
        return GateResult(passed=False, reason="rain_in_forecast")

    # 4. Warm temperature gate
    if any(
        day.temp_max_c > settings.forecast_temp_exclude_threshold_c
        for day in input.forecast_days
    ):
        return GateResult(passed=False, reason="warm_temperature_forecast")

    return GateResult(passed=True, reason=None)


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def score_depth(depth_cm: float, mode: str) -> int:
    """Return depth score [0, 30] based on mode-specific bracket lookup.

    sports:  <20cm → 0, 20–39cm → 10, 40–59cm → 20, ≥60cm → 30
    tourism: <10cm → 0, 10–19cm → 10, 20–29cm → 20, ≥30cm  → 30
    """
    if mode == "sports":
        if depth_cm < 20:
            return 0
        elif depth_cm < 40:
            return 10
        elif depth_cm < 60:
            return 20
        else:
            return 30
    else:  # tourism
        if depth_cm < 10:
            return 0
        elif depth_cm < 20:
            return 10
        elif depth_cm < 30:
            return 20
        else:
            return 30


def score_forecast(days: list[DayForecast]) -> int:
    """Return forecast stability score [0, 30].

    Step 1 — detect conditions:
      has_rain  = any day with rain_sum_mm > 0.1
      has_warm  = any day with temp_max_c > 3.0
      has_snow  = any day with snowfall_sum_cm > 0
      is_stable = not has_rain and not has_warm

    Step 2 — priority-ordered base score:
      has_rain AND has_warm → 0
      has_rain only         → 0   (10 - 15, clamped)
      has_warm only         → 5   (15 - 10)
      is_stable             → 25
      else                  → 0

    Step 3 — snowfall bonus (only when is_stable and has_snow):
      base = min(base + 5, 30)

    Step 4 — clamp to [0, 30]
    """
    has_rain = any(day.rain_sum_mm > 0.1 for day in days)
    has_warm = any(day.temp_max_c > 3.0 for day in days)
    has_snow = any(day.snowfall_sum_cm > 0 for day in days)
    is_stable = not has_rain and not has_warm

    if has_rain and has_warm:
        base = 0
    elif has_rain:
        base = max(0, 10 - 15)  # 0
    elif has_warm:
        base = max(0, 15 - 10)  # 5
    elif is_stable:
        base = 25
    else:
        base = 0

    if is_stable and has_snow:
        base = min(base + 5, 30)

    return max(0, min(30, base))


def score_historical(reliability_pct: float) -> int:
    """Return historical reliability score [0, 25] as round(pct * 0.25)."""
    return max(0, min(25, round(reliability_pct * 0.25)))


def compute_score(input: ScoreInput) -> ScoreResult:
    """Compose all four score components into a ScoreResult."""
    depth_score = score_depth(input.snow_depth_cm, input.mode)
    forecast_score = score_forecast(input.forecast_days)
    historical_score = score_historical(input.historical_reliability_pct)
    qualitative_score = input.qualitative_score  # Always 10 in Phase 1
    total = min(100, depth_score + forecast_score + historical_score + qualitative_score)
    return ScoreResult(
        score_depth=depth_score,
        score_forecast=forecast_score,
        score_historical=historical_score,
        score_qualitative=qualitative_score,
        score_total=total,
    )
