"""
Structured logging configuration for the DCR Queue Polling Service.

Provides both console output and rotating file logging.
"""

import logging
import logging.handlers
import os
import sys

from app.config import settings


def setup_logging() -> None:
    """
    Configure root logger with:
     - Console handler  (stdout)
     - Rotating file handler (10 MB per file, 5 backups)

    Call this once at service startup before any other imports log messages.
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # Ensure log directory exists
    log_dir = os.path.dirname(settings.LOG_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)-40s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Rotating file handler (10 MB per file, 5 backups)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=settings.LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("msal").setLevel(logging.WARNING)
    logging.getLogger("pyodbc").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        f"Logging initialized — level={settings.LOG_LEVEL}, file={settings.LOG_FILE}"
    )
