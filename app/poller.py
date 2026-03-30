"""
Core polling engine for the DCR Queue Polling Service.

The Poller runs a continuous loop that on each tick executes two phases:

  Phase 1 — Upload (Queued → Processing)
  ----------------------------------------
  Fetches records with Status = 'Queued'.
  For each record:
    1. Atomically claims it (Status → 'Processing') with an optimistic lock.
    2. Reads the two source PDFs from PDF_SOURCE_DIR.
    3. POSTs them to the external service via POST /api/v1/dcrs/upload.
    4. On HTTP 200  → record stays 'Processing' (handled by Phase 2 next tick).
    5. On any error → CompareResult = 'Failed', ErrorMessage = <error>.

  Phase 2 — Status Check (Processing → Pending Review | Failed)
  ---------------------------------------------------------------
  Fetches records with Status = 'Processing'.
  For each record:
    1. Calls GET /api/v1/dcrs/{queue_id}/status on the external service.
    2. If status == "In Progress"  → do nothing (check again next tick).
    3. If status == "Completed"    → download ResultPDFLink to network drive
                                     → mark 'Pending Review' / 'Successful'.
    4. On any error                → CompareResult = 'Failed', ErrorMessage.

Concurrency is managed with a ThreadPoolExecutor. Active futures are tracked
in two dicts to prevent double submission of the same record.
"""

import json
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Dict

from app import change_writer, db_client, external_api_client, pdf_reader
from app.config import settings
from app.models import QueueRecord

logger = logging.getLogger(__name__)


class Poller:
    """
    Polling engine that continuously monitors DrawingChangeCompareQueue.

    Usage::

        poller = Poller()
        poller.run()   # blocks until Ctrl+C
    """

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=settings.MAX_CONCURRENT_JOBS,
            thread_name_prefix="dcr-worker",
        )
        # Track in-flight upload jobs   {queue_id: Future}
        self._upload_jobs: Dict[int, Future] = {}
        # Track in-flight status-check jobs {queue_id: Future}
        self._status_jobs: Dict[int, Future] = {}
        self._running = False

    # -------------------------------------------------------------------------
    # Public entry points
    # -------------------------------------------------------------------------

    def run(self) -> None:
        """
        Start the polling loop (blocking).

        Polls every POLL_INTERVAL_SECONDS seconds.
        Handles Ctrl+C gracefully.
        """
        self._running = True
        logger.info(
            f"Poller started — "
            f"poll_interval={settings.POLL_INTERVAL_SECONDS}s, "
            f"max_concurrent={settings.MAX_CONCURRENT_JOBS}"
        )

        try:
            while self._running:
                logger.info(
                    f"Polling tick — "
                    f"active_jobs={len(self._upload_jobs)}"
                )
                self._poll_queued()
                # Phase 2 (_poll_processing) is disabled — the full workflow
                # (upload → compare → report → download → mark complete)
                # is now handled inline in _run_upload.
                self._cleanup_finished_jobs()
                time.sleep(settings.POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("Shutdown requested via KeyboardInterrupt")
        finally:
            self._shutdown()

    def stop(self) -> None:
        """Signal the polling loop to stop (used in tests / daemon mode)."""
        self._running = False

    # -------------------------------------------------------------------------
    # Phase 1: Upload (Queued → Processing)
    # -------------------------------------------------------------------------

    def _poll_queued(self) -> None:
        """
        Atomically claim 'Queued' records and submit upload jobs.

        Uses a single UPDATE ... OUTPUT statement so claim + read happen in
        one round-trip.  No race window exists between SELECT and UPDATE,
        meaning no worker (local or VM) can ever see the same row twice.
        """
        available_slots = settings.MAX_CONCURRENT_JOBS - len(self._upload_jobs)
        if available_slots <= 0:
            logger.debug("All upload slots occupied — skipping queued poll")
            return

        # Single atomic round-trip: claim ownership AND read PDF bytes together.
        # Only rows that were still 'Queued' at lock-acquisition time are returned.
        records = db_client.claim_queued_records(limit=available_slots)
        if not records:
            logger.debug("No queued records found")
            return

        for record in records:
            if record.queue_id in self._upload_jobs:
                # Should never happen with atomic claim, but guard defensively.
                logger.warning(
                    f"[QueueId={record.queue_id}] Already in upload_jobs after atomic claim — skipping"
                )
                continue

            future = self._executor.submit(self._run_upload, record)
            self._upload_jobs[record.queue_id] = future
            logger.info(
                f"[QueueId={record.queue_id}] Upload job submitted "
                f"(DcrNumber={record.dcr_number!r})"
            )

    def _run_upload(self, record: QueueRecord) -> None:
        """
        Worker function: full workflow from PDF upload to completion.

        upload_pdfs() already triggers compare + report generation on the
        backend, so the DCR is 'Completed' by the time it returns.  We
        handle everything inline here to avoid a separate Phase 2 status
        check (the backend has no dedicated /status endpoint).

        Runs inside the thread pool — never raises (errors are recorded in DB).
        """
        queue_id = record.queue_id
        dcr_number = record.dcr_number

        try:
            # Step 1: Read source PDFs from the QueueRecord (loaded from DB VARBINARY)
            current_pdf, new_pdf = pdf_reader.read_pdf_pair(record)

            # Step 2-4: Upload PDFs → trigger compare → generate report
            # (all handled inside upload_pdfs)
            # Returns the backend's internal dcr_id (may differ from dcr_number)
            backend_dcr_id = external_api_client.upload_pdfs(
                queue_id=queue_id,
                dcr_number=dcr_number,
                current_pdf_bytes=current_pdf,
                new_pdf_bytes=new_pdf,
            )
            logger.info(
                f"[QueueId={queue_id}] Upload + compare + report complete"
            )

            # Step 5: Fetch structured change details & write to tables
            # Use backend_dcr_id (not dcr_number) for all backend API lookups
            try:
                eng_data = external_api_client.fetch_engineering_changes(backend_dcr_id)
                bom_data = external_api_client.fetch_bom_changes(backend_dcr_id)

                change_writer.insert_engineering_changes(
                    queue_id=queue_id,
                    dcr_record_number=record.dcr_record_number,
                    changes=eng_data.get("changes", []),
                )
                change_writer.insert_bom_changes(
                    queue_id=queue_id,
                    dcr_record_number=record.dcr_record_number,
                    changes=bom_data.get("changes", []),
                )
                logger.info(
                    f"[QueueId={queue_id}] "
                    f"{eng_data.get('count', 0)} engineering + "
                    f"{bom_data.get('count', 0)} BOM change(s) written"
                )
            except Exception as change_exc:
                logger.warning(
                    f"[QueueId={queue_id}] Could not write change details: "
                    f"{change_exc}  — continuing to download result PDF"
                )

            # Step 6: Mark the record as successfully completed
            db_client.mark_pending_review(queue_id)

        except Exception as exc:
            error_msg = str(exc)
            logger.error(
                f"[QueueId={queue_id}] Upload failed for DcrNumber={dcr_number!r}: "
                f"{error_msg}",
                exc_info=True,
            )
            db_client.mark_failed(queue_id, error_msg)

    # -------------------------------------------------------------------------
    # Phase 2: Status Check (Processing → Pending Review | Failed)
    # -------------------------------------------------------------------------

    def _poll_processing(self) -> None:
        """Fetch 'Processing' records and submit status-check jobs for untracked ones."""
        records = db_client.fetch_processing_records(limit=50)
        if not records:
            logger.debug("No processing records found")
            return

        logger.debug(f"Found {len(records)} processing record(s) to check")

        for record in records:
            # Skip records whose upload (Phase 1) is still in-flight —
            # the backend won't have the DCR yet, so status would 404.
            if record.queue_id in self._upload_jobs:
                upload_future = self._upload_jobs[record.queue_id]
                if not upload_future.done():
                    logger.debug(
                        f"[QueueId={record.queue_id}] Skipping status check — "
                        f"upload still in progress"
                    )
                    continue

            # Skip records already submitted for an in-flight status check
            if record.queue_id in self._status_jobs:
                future = self._status_jobs[record.queue_id]
                if not future.done():
                    continue  # Still running

            future = self._executor.submit(self._run_status_check, record)
            self._status_jobs[record.queue_id] = future
            logger.debug(
                f"[QueueId={record.queue_id}] Status-check job submitted"
            )

    def _run_status_check(self, record: QueueRecord) -> None:
        """
        Worker function: call the status API and act on the result.

        Runs inside the thread pool — never raises (errors are recorded in DB).
        """
        queue_id = record.queue_id
        dcr_number = record.dcr_number

        try:
            payload = external_api_client.check_status(queue_id, dcr_number)
            remote_status = payload.get("status", "")

            if remote_status == "In Progress":
                # Nothing to do — check again next poll cycle
                logger.debug(
                    f"[QueueId={queue_id}] External status is 'In Progress' — waiting"
                )
                return

            if remote_status == "Completed":
                result_pdf_url = payload.get("ResultPDFLink")
                if not result_pdf_url:
                    raise ValueError(
                        "'Completed' response missing 'ResultPDFLink' field"
                    )

                # ---- Step 7: Fetch structured change details & write to tables ----
                dcr_id = payload.get("dcr_id")
                if dcr_id:
                    # Fetch pre-structured JSON from Backend endpoints
                    eng_data = external_api_client.fetch_engineering_changes(dcr_id)
                    bom_data = external_api_client.fetch_bom_changes(dcr_id)

                    change_writer.insert_engineering_changes(
                        queue_id=queue_id,
                        dcr_record_number=record.dcr_record_number,
                        changes=eng_data.get("changes", []),
                    )
                    change_writer.insert_bom_changes(
                        queue_id=queue_id,
                        dcr_record_number=record.dcr_record_number,
                        changes=bom_data.get("changes", []),
                    )

                    logger.info(
                        f"[QueueId={queue_id}] Step 7 complete — "
                        f"{eng_data.get('count', 0)} engineering + "
                        f"{bom_data.get('count', 0)} BOM change(s) written"
                    )
                else:
                    logger.warning(
                        f"[QueueId={queue_id}] 'Completed' response missing 'dcr_id' — "
                        f"skipping Step 7 (change detail tables)"
                    )



                # Mark the record as successfully completed
                db_client.mark_pending_review(queue_id)
                logger.info(
                    f"[QueueId={queue_id}] Comparison complete — "
                    f"result saved to {saved_path}"
                )
                return

            # Unexpected status value
            raise ValueError(
                f"Unexpected status value from external service: {remote_status!r}"
            )

        except Exception as exc:
            error_msg = str(exc)
            logger.error(
                f"[QueueId={queue_id}] Status check failed for "
                f"DcrNumber={dcr_number!r}: {error_msg}",
                exc_info=True,
            )
            db_client.mark_failed(queue_id, error_msg)

    # -------------------------------------------------------------------------
    # Housekeeping
    # -------------------------------------------------------------------------

    def _cleanup_finished_jobs(self) -> None:
        """Remove completed futures from both tracking dicts."""
        for jobs_dict, label in [
            (self._upload_jobs, "upload"),
            (self._status_jobs, "status"),
        ]:
            done_ids = [qid for qid, f in jobs_dict.items() if f.done()]
            for qid in done_ids:
                future = jobs_dict.pop(qid)
                exc = future.exception()
                if exc:
                    # Safety net — exceptions should already be handled inside
                    # the worker functions, but log any unhandled ones here.
                    logger.error(
                        f"Unhandled exception in {label} worker "
                        f"for QueueId={qid}: {exc}"
                    )

    def _shutdown(self) -> None:
        """Gracefully wait for active jobs before exiting."""
        logger.info("Waiting for active jobs to finish before shutdown…")
        self._executor.shutdown(wait=True, cancel_futures=False)
        logger.info("Poller shut down cleanly.")
