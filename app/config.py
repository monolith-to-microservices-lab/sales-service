"""Runtime configuration, loaded exclusively from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Connection string for the Sales Service's OWN PostgreSQL database.
    database_url: str = "postgresql+psycopg2://sales:sales@localhost:5432/sales"

    # development | staging | production
    app_env: str = "development"

    # DEBUG | INFO | WARNING | ERROR | CRITICAL
    log_level: str = "INFO"

    service_name: str = "sales-service"

    # --- CDC consumer (app/cdc) ----------------------------------------
    # Runs as a separate process from the FastAPI server; see app/cdc/__main__.py.
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_sales_topic: str = "legacy.public.sales"
    kafka_consumer_group: str = "sales-service-cdc"
    kafka_auto_offset_reset: str = "earliest"
    cdc_metrics_port: int = 9201


@lru_cache
def get_settings() -> Settings:
    return Settings()
