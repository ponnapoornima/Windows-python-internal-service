"""
Configuration for the DCR Queue Polling Service.

All settings are loaded from environment variables or a .env file.
Uses pydantic-settings for validation and type coercion.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # -------------------------------------------------------------------------
    # SQL Server — Drawing Hub Queue Database (Client: SQL Server 2019)
    # -------------------------------------------------------------------------
    MSSQL_HOST:     str
    MSSQL_PORT:     int    = 1433
    MSSQL_DATABASE: str    = "DrawingHub"
    MSSQL_USERNAME: str
    MSSQL_PASSWORD: str
    MSSQL_DRIVER:   str    = "ODBC Driver 18 for SQL Server"

    # -------------------------------------------------------------------------
    # External DCR Comparison Service
    # Base URL for the service that performs PDF comparison.
    # e.g. https://cp-apim-dev.azure-api.net/polling-api
    # -------------------------------------------------------------------------
    EXTERNAL_API_BASE_URL: str

    # -------------------------------------------------------------------------
    # Polling behaviour
    # -------------------------------------------------------------------------
    POLL_INTERVAL_SECONDS: int = 5          # How often to poll the queue
    MAX_CONCURRENT_JOBS: int = 3            # Max parallel worker threads
    REQUEST_TIMEOUT_SECONDS: int = 120      # HTTP timeout per request
    MAX_RETRIES: int = 3                    # Retries for transient (5xx) errors
    RETRY_DELAY_SECONDS: float = 5.0        # Base delay between retries

    # -------------------------------------------------------------------------
    # APIM Authentication — Subscription Key
    # When EXTERNAL_API_BASE_URL points to APIM, provide the subscription key.
    # The key is sent as the Ocp-Apim-Subscription-Key header.
    # Leave empty/None for local development (no auth).
    # -------------------------------------------------------------------------
    APIM_SUBSCRIPTION_KEY: Optional[str] = None

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/service.log"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
