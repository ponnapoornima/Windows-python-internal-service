"""
SQL Server database client for the DCR Queue Polling Service.

Provides:
 - Connection management via pyodbc (ODBC Driver 17 for SQL Server)
 - Queue polling (SELECT WHERE Status = 'Queued' / 'Processing')
 - Status transitions:
     Queued     → Processing       (mark_processing)
     Processing → Pending Review   (mark_pending_review)
     Any        → Failed           (mark_failed)

Table schema (DrawingChangeCompareQueue — SQL Server 2019):
  QueueId              BIGINT IDENTITY
  DcrRecordNumber      BIGINT
  DcrNumber            NVARCHAR(50)
  CurrentRevisionPdf   VARBINARY(MAX)
  NewRevisionPdf       VARBINARY(MAX)
  Status               VARCHAR(20)
  CompareResult        VARCHAR(20)
  UserId               NVARCHAR(100)
  EntryDate            DATETIME
  CompareDate          DATETIME
  AcceptDate           DATETIME
  ErrorMessage         NVARCHAR(MAX)
"""

import logging
from contextlib import contextmanager
from typing import Generator, List

import pyodbc

from app.config import settings
from app.models import QueueRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _build_connection_string() -> str:
    """Build an ODBC connection string from settings."""
    return (
        f"DRIVER={{{settings.MSSQL_DRIVER}}};"
        f"SERVER={settings.MSSQL_HOST},{settings.MSSQL_PORT};"
        f"DATABASE={settings.MSSQL_DATABASE};"
        f"UID={settings.MSSQL_USERNAME};"
        f"PWD={settings.MSSQL_PASSWORD};"
        f"TrustServerCertificate=yes;"
    )


@contextmanager
def get_connection() -> Generator[pyodbc.Connection, None, None]:
    """
    Context manager that yields an open pyodbc connection.

    Commits on clean exit, rolls back on exception.
    """
    conn = pyodbc.connect(_build_connection_string(), timeout=30)
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def test_connection() -> bool:
    """Test the database connection. Returns True if successful."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
        logger.info("SQL Server connection test successful")
        return True
    except Exception as exc:
        logger.error(f"SQL Server connection test failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Queue read operations
# ---------------------------------------------------------------------------

# Include PDF VARBINARY columns so PDFs are read directly from DB
_SELECT_COLS = "QueueId, DcrRecordNumber, DcrNumber, Status, CurrentRevisionPdf, NewRevisionPdf"


def fetch_queued_records(limit: int = 10) -> List[QueueRecord]:
    """
    Fetch up to *limit* records with Status = 'Queued', ordered FIFO.

    Returns an empty list on error or when no rows match.
    """
    sql = f"""
        SELECT TOP (?)
            {_SELECT_COLS}
        FROM DrawingChangeCompareQueue
        WHERE Status = 'Queued'
        ORDER BY QueueId ASC
    """
    return _fetch_records(sql, limit, label="queued")


def fetch_processing_records(limit: int = 50) -> List[QueueRecord]:
    """
    Fetch up to *limit* records with Status = 'Processing', ordered FIFO.

    Returns an empty list on error or when no rows match.
    """
    sql = f"""
        SELECT TOP (?)
            {_SELECT_COLS}
        FROM DrawingChangeCompareQueue
        WHERE Status = 'Processing'
        ORDER BY QueueId ASC
    """
    return _fetch_records(sql, limit, label="processing")


def _fetch_records(sql: str, limit: int, label: str) -> List[QueueRecord]:
    """Internal helper — runs a parameterised SELECT and maps rows to QueueRecord."""
    records: List[QueueRecord] = []
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (limit,))
            rows = cursor.fetchall()
            records = [QueueRecord.from_row(row) for row in rows]
        logger.debug(f"Fetched {len(records)} {label} record(s)")
    except Exception as exc:
        logger.error(f"Error fetching {label} records: {exc}")
    return records


# ---------------------------------------------------------------------------
# Queue write operations
# ---------------------------------------------------------------------------

def mark_processing(queue_id: int) -> bool:
    """
    Atomically transition Status from 'Queued' → 'Processing'.

    Uses an optimistic lock (WHERE Status = 'Queued') to prevent
    double-processing in concurrent deployments.

    Returns True if the row was successfully claimed, False otherwise.
    """
    sql = """
        UPDATE DrawingChangeCompareQueue
        SET Status = 'Processing'
        WHERE QueueId = ? AND Status = 'Queued'
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (queue_id,))
            claimed = cursor.rowcount > 0
        if claimed:
            logger.info(f"QueueId={queue_id} → Status='Processing'")
        else:
            logger.warning(f"QueueId={queue_id} already claimed or no longer Queued")
        return claimed
    except Exception as exc:
        logger.error(f"Error claiming QueueId={queue_id}: {exc}")
        return False


def mark_pending_review(queue_id: int) -> None:
    """
    Transition Status to 'Pending Review' and CompareResult to 'Successful'
    after the result PDF has been downloaded and saved successfully.
    """
    sql = """
        UPDATE DrawingChangeCompareQueue
        SET Status        = 'Pending Review',
            CompareResult = 'Successful',
            CompareDate   = GETDATE()
        WHERE QueueId = ?
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (queue_id,))
        logger.info(f"QueueId={queue_id} → Status='Pending Review', CompareResult='Successful'")
    except Exception as exc:
        logger.error(f"Error marking QueueId={queue_id} as Pending Review: {exc}")


def mark_failed(queue_id: int, error_message: str) -> None:
    """
    Set CompareResult to 'Failed' and record the error message.
    """
    truncated = (error_message or "Unknown error")[:4000]
    sql = """
        UPDATE DrawingChangeCompareQueue
        SET Status        = 'Failed',
            CompareResult = 'Failed',
            CompareDate   = GETDATE(),
            ErrorMessage  = ?
        WHERE QueueId = ?
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (truncated, queue_id))
        logger.warning(f"QueueId={queue_id} → CompareResult='Failed': {truncated[:200]}")
    except Exception as exc:
        logger.error(f"Error marking QueueId={queue_id} as failed: {exc}")
