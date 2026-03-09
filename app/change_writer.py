"""
Change writer for the DCR Queue Polling Service.

Writes pre-structured Engineering and BOM change details (received from the
Automated DCR Backend JSON endpoints) into the client's SQL Server tables:

  - EngineeringChangeDetail  (text / annotation changes)
  - BomChangeDetail          (BOM + dimensional changes)

The Backend handles all AI-output parsing and categorisation.
This writer simply maps the JSON fields to SQL columns and inserts.

Both tables use BIGINT IDENTITY primary keys — the DB auto-generates the IDs.
Review* columns are left NULL (populated later by the client's review workflow).
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from app.db_client import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def insert_engineering_changes(
    queue_id: int,
    dcr_record_number: int,
    changes: List[Dict[str, Any]],
) -> int:
    """
    Insert engineering change rows into EngineeringChangeDetail.

    Args:
        queue_id:            QueueId from DrawingChangeCompareQueue
        dcr_record_number:   DcrRecordNumber (FK to DCR)
        changes:             Pre-structured list from GET /changes/engineering

    Each item in ``changes`` has keys:
        Category, DiffType, LocationJson, OldValue, NewValue, OriginalAiOutput

    Returns:
        Number of rows inserted.
    """
    if not changes:
        logger.info(f"[QueueId={queue_id}] No engineering changes to insert")
        return 0

    sql = """
        INSERT INTO EngineeringChangeDetail
            (DcrRecordNumber, QueueId, Category, DiffType,
             LocationJson, OldValue, NewValue, OriginalAiOutput, EntryDate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    rows_inserted = 0
    entry_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            for row in changes:
                cursor.execute(sql, (
                    dcr_record_number,
                    queue_id,
                    row.get("Category", "Other"),
                    row.get("DiffType", "Modify"),
                    row.get("LocationJson", ""),
                    row.get("OldValue", ""),
                    row.get("NewValue", ""),
                    row.get("OriginalAiOutput", ""),
                    entry_date,
                ))
                rows_inserted += 1

        logger.info(
            f"[QueueId={queue_id}] Inserted {rows_inserted} row(s) "
            f"into EngineeringChangeDetail"
        )
    except Exception as exc:
        logger.error(
            f"[QueueId={queue_id}] Failed to insert engineering changes: {exc}",
            exc_info=True,
        )
        raise

    return rows_inserted


def insert_bom_changes(
    queue_id: int,
    dcr_record_number: int,
    changes: List[Dict[str, Any]],
) -> int:
    """
    Insert BOM change rows into BomChangeDetail.

    Args:
        queue_id:              QueueId from DrawingChangeCompareQueue
        dcr_record_number:     DcrRecordNumber (FK to DCR)
        changes:               Pre-structured list from GET /changes/bom

    Each item in ``changes`` has keys:
        PartNumber, DiffType, OldQuantity, NewQuantity,
        OldUom, NewUom, OldReference, NewReference, OriginalAiOutput

    Returns:
        Number of rows inserted.
    """
    if not changes:
        logger.info(f"[QueueId={queue_id}] No BOM/dimensional changes to insert")
        return 0

    sql = """
        INSERT INTO BomChangeDetail
            (DcrRecordNumber, QueueId, PartNumber, DiffType,
             OldQuantity, NewQuantity, OldUom, NewUom,
             OldReference, NewReference, OriginalAiOutput, EntryDate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    rows_inserted = 0
    entry_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            for row in changes:
                cursor.execute(sql, (
                    dcr_record_number,
                    queue_id,
                    row.get("PartNumber", ""),
                    row.get("DiffType", "Modify"),
                    row.get("OldQuantity"),
                    row.get("NewQuantity"),
                    row.get("OldUom", ""),
                    row.get("NewUom", ""),
                    row.get("OldReference", ""),
                    row.get("NewReference", ""),
                    row.get("OriginalAiOutput", ""),
                    entry_date,
                ))
                rows_inserted += 1

        logger.info(
            f"[QueueId={queue_id}] Inserted {rows_inserted} row(s) "
            f"into BomChangeDetail"
        )
    except Exception as exc:
        logger.error(
            f"[QueueId={queue_id}] Failed to insert BOM changes: {exc}",
            exc_info=True,
        )
        raise

    return rows_inserted
