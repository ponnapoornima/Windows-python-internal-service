"""
PDF source reader for the DCR Queue Polling Service.

Reads the current (old) and new revision PDFs from the QueueRecord,
which are loaded directly from the SQL Server VARBINARY(MAX) columns.
"""

import logging
from typing import Tuple

from app.models import QueueRecord

logger = logging.getLogger(__name__)


def read_pdf_pair(record: QueueRecord) -> Tuple[bytes, bytes]:
    """
    Read the current-revision and new-revision PDFs from a QueueRecord.

    The PDFs are stored as VARBINARY(MAX) in the DrawingChangeCompareQueue
    table and are loaded into the QueueRecord when fetched from the database.

    Args:
        record: QueueRecord with populated current_pdf_bytes and new_pdf_bytes.

    Returns:
        Tuple of (current_pdf_bytes, new_pdf_bytes)

    Raises:
        ValueError: If either PDF column is NULL/empty.
    """
    if not record.current_pdf_bytes:
        raise ValueError(
            f"CurrentRevisionPdf is empty for QueueId={record.queue_id} "
            f"(DcrNumber={record.dcr_number!r})"
        )

    if not record.new_pdf_bytes:
        raise ValueError(
            f"NewRevisionPdf is empty for QueueId={record.queue_id} "
            f"(DcrNumber={record.dcr_number!r})"
        )

    logger.info(
        f"Read PDFs for DcrNumber={record.dcr_number!r}: "
        f"current={len(record.current_pdf_bytes)} bytes, "
        f"new={len(record.new_pdf_bytes)} bytes"
    )
    return record.current_pdf_bytes, record.new_pdf_bytes
