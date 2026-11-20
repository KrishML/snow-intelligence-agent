from typing import Literal
import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://snow:snow@localhost:5432/snow_agent"

    # ── Application ───────────────────────────────────────────────────────────
    default_mode: Literal["sports", "tourism"] = "sports"
    api_bearer_token: str = ""
    default_user_id: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")
    admin_user_ids: list[uuid.UUID] = [uuid.UUID("00000000-0000-0000-0000-000000000001")]

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
    weekly_run_estimated_minutes: int = 30

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model_chat: str = "gpt-4o"
    openai_model_qualitative: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_vision_timeout_seconds: float = 30.0

    # ── Reddit (praw) ─────────────────────────────────────────────────────────
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "snow-agent/2.0"
    reddit_post_limit: int = 10
    reddit_lookback_days: int = 7

    # ── Webcam ────────────────────────────────────────────────────────────────
    webcam_timeout_seconds: float = 5.0
    webcam_snapshot_dir: str = "snapshots"
    webcam_concurrency: int = 5
    webcam_storage_backend: Literal["local", "s3"] = "local"
    webcam_s3_bucket: str = ""
    webcam_s3_prefix: str = "webcams"
    webcam_retention_days: int = 90

    # ── Evidence sources ────────────────────────────────────────────────
    official_report_sources_file: str = "data/report_sources.json"
    tourism_rss_feeds: list[str] = []

    # ── Qualitative scoring ───────────────────────────────────────────────────
    qualitative_concurrency: int = 5
    qualitative_timeout_minutes: float = 8.0

    # ── Delivery ──────────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    sendgrid_api_key: str = ""
    sendgrid_from_email: str = "reports@snow-agent.io"
    delivery_email_address: str = ""

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    redis_session_ttl_seconds: int = 86400

    # ── Amadeus (chat agent only) ─────────────────────────────────────────────
    amadeus_client_id: str = ""
    amadeus_client_secret: str = ""


settings = Settings()
