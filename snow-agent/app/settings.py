from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://snow:snow@localhost:5432/snow_agent"

    # ── Application ───────────────────────────────────────────────────────────
    default_mode: Literal["sports", "tourism"] = "sports"

    # ── Open-Meteo ────────────────────────────────────────────────────────────
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_concurrency: int = 10          # semaphore limit for parallel fetches
    open_meteo_timeout_seconds: float = 30.0
    open_meteo_max_retries: int = 3

    # ── Scoring ───────────────────────────────────────────────────────────────
    sports_depth_gate_cm: int = 40
    tourism_depth_gate_cm: int = 15
    forecast_rain_exclude_threshold_mm: float = 0.1
    forecast_temp_exclude_threshold_c: float = 3.0
    historical_years: int = 5

    # ── Scheduler ─────────────────────────────────────────────────────────────
    weekly_run_day: str = "fri"
    weekly_run_hour: int = 17
    weekly_run_minute: int = 0
    weekly_run_timezone: str = "UTC"


settings = Settings()
