"""
DCR Queue Polling Service — entry point.

Run:
    python main.py

Or as a Docker container:
    docker run --env-file .env dcr-queue-polling-service
"""

import logging
import sys

from app.logging_config import setup_logging

# Setup logging first so all subsequent imports log correctly
setup_logging()

logger = logging.getLogger(__name__)


def main() -> None:
    """Bootstrap and start the polling service."""
    logger.info("=" * 70)
    logger.info("DCR Queue Polling Service — starting up")
    logger.info("=" * 70)

    # -------------------------------------------------------------------------
    # Step 1: Validate configuration
    # -------------------------------------------------------------------------
    try:
        from app.config import settings
        logger.info(f"External API URL   : {settings.EXTERNAL_API_BASE_URL}")
        logger.info(f"SQL Server DB      : {settings.MSSQL_HOST}:{settings.MSSQL_PORT}/{settings.MSSQL_DATABASE}")
        logger.info(f"Poll interval      : {settings.POLL_INTERVAL_SECONDS}s")
        logger.info(f"Max concurrent     : {settings.MAX_CONCURRENT_JOBS} jobs")
    except Exception as exc:
        logger.critical(f"Configuration error: {exc}")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # Step 2: Verify SQL Server connectivity
    # -------------------------------------------------------------------------
    logger.info("Verifying SQL Server connectivity…")
    try:
        from app.db_client import fetch_queued_records
        records = fetch_queued_records(limit=1)
        logger.info(f"SQL Server connected — {len(records)} queued record(s) currently pending")
    except Exception as exc:
        logger.critical(f"SQL Server connection failed: {exc}")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # Step 3: Start the polling loop
    # -------------------------------------------------------------------------
    from app.poller import Poller
    poller = Poller()
    poller.run()


if __name__ == "__main__":
    main()
