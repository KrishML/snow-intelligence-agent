from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class DayForecast(BaseModel):
    date: date
    temp_max_c: float
    rain_sum_mm: float
    snowfall_sum_cm: float


class GateInput(BaseModel):
    mode: Literal["sports", "tourism"]
    snow_depth_cm: float
    has_open_run: bool | None = None  # sports mode only; None means unknown
    forecast_days: list[DayForecast]  # 5 entries


class GateResult(BaseModel):
    passed: bool
    reason: str | None = None  # 'depth_below_threshold', 'no_open_run',
    # 'rain_in_forecast', 'warm_temperature_forecast'


class ScoreInput(BaseModel):
    mode: Literal["sports", "tourism"]
    snow_depth_cm: float
    forecast_days: list[DayForecast]
    historical_reliability_pct: float = Field(ge=0.0, le=100.0)
    qualitative_score: int = 10  # Phase 1 placeholder — always 10


class ScoreResult(BaseModel):
    score_depth: int = Field(ge=0, le=30)
    score_forecast: int = Field(ge=0, le=30)
    score_historical: int = Field(ge=0, le=25)
    score_qualitative: int = Field(ge=0, le=20)
    score_total: int = Field(ge=0, le=100)
