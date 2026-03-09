"""
Data models for the DCR Queue Polling Service.

Maps the DrawingChangeCompareQueue table schema to Python dataclasses.

Client's Drawing Hub uses SQL Server 2019 with this table structure:
    QueueId              BIGINT IDENTITY
    DcrRecordNumber      BIGINT
    DcrNumber            NVARCHAR(50)         e.g. 'DCR-2026-0012'
    CurrentRevisionPdf   VARBINARY(MAX)       Old/current revision PDF
    NewRevisionPdf       VARBINARY(MAX)       New revision PDF
    Status               VARCHAR(20)          Queued | Processing | Completed | Failed
    CompareResult        VARCHAR(20)          Successful | Failed | NULL
    UserId               NVARCHAR(100)
    EntryDate            DATETIME
    CompareDate          DATETIME             NULL until comparison completes
    AcceptDate           DATETIME             NULL until accepted
    ErrorMessage         NVARCHAR(MAX)        NULL unless Failed
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class QueueRecord:
    """
    Represents one row from the DrawingChangeCompareQueue table.

    The PDF bytes are loaded directly from the VARBINARY(MAX) columns
    in the SQL Server queue table.
    """

    queue_id: int
    dcr_record_number: int
    dcr_number: str
    status: str
    current_pdf_bytes: Optional[bytes] = field(default=None, repr=False)
    new_pdf_bytes: Optional[bytes] = field(default=None, repr=False)

    @classmethod
    def from_row(cls, row) -> "QueueRecord":
        """Construct a QueueRecord from a pyodbc row.

        Column order must match the SELECT in db_client.py:
            QueueId, DcrRecordNumber, DcrNumber, Status,
            CurrentRevisionPdf, NewRevisionPdf
        """
        return cls(
            queue_id=row[0],
            dcr_record_number=row[1],
            dcr_number=row[2],
            status=row[3],
            current_pdf_bytes=row[4] if len(row) > 4 else None,
            new_pdf_bytes=row[5] if len(row) > 5 else None,
        )

    def __repr__(self) -> str:
        current_size = len(self.current_pdf_bytes) if self.current_pdf_bytes else 0
        new_size = len(self.new_pdf_bytes) if self.new_pdf_bytes else 0
        return (
            f"QueueRecord(queue_id={self.queue_id}, "
            f"dcr_number={self.dcr_number!r}, status={self.status!r}, "
            f"current_pdf={current_size}B, new_pdf={new_size}B)"
        )
