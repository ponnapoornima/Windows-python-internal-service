"""
External DCR Comparison Service API client.

Provides the two API calls required by the polling workflow:

  1. upload_pdfs()   — POST /api/v1/dcrs/upload
                       Sends two PDFs + metadata. Returns True on HTTP 200.

  2. check_status()  — GET  /api/v1/dcrs/{queue_id}/status
                       Returns a dict with at minimum:
                         {"status": "In Progress" | "Completed",
                          "ResultPDFLink": "<azure-sas-url>"}   # when Completed
"""

import logging
import time
from io import BytesIO
from typing import Any, Dict, Optional

import requests

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _api_url(path: str) -> str:
    """Build a full URL from a relative path against EXTERNAL_API_BASE_URL."""
    base = settings.EXTERNAL_API_BASE_URL.rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _get_auth_headers() -> Dict[str, str]:
    """
    Return HTTP auth headers for the external service.

    When APIM_SUBSCRIPTION_KEY is configured, sends it as the
    Ocp-Apim-Subscription-Key header. Returns empty dict for
    local development (no auth needed).
    """
    if settings.APIM_SUBSCRIPTION_KEY:
        return {"Ocp-Apim-Subscription-Key": settings.APIM_SUBSCRIPTION_KEY}
    return {}


def _request_with_retry(
    method: str,
    url: str,
    *,
    max_retries: int = None,
    retry_delay: float = None,
    **kwargs,
) -> requests.Response:
    """
    Execute an HTTP request with retry on transient (5xx) errors.

    4xx errors are raised immediately (non-retryable).
    Uses linear back-off: delay × attempt number.

    Args:
        method:      HTTP verb ("get", "post", …)
        url:         Full URL
        max_retries: Override settings.MAX_RETRIES
        retry_delay: Override settings.RETRY_DELAY_SECONDS
        **kwargs:    Forwarded to requests.request()

    Returns:
        requests.Response (guaranteed 2xx on return)

    Raises:
        requests.HTTPError: Final non-2xx response after all retries
        RuntimeError:       On connection / timeout failures
    """
    retries = max_retries if max_retries is not None else settings.MAX_RETRIES
    delay = retry_delay if retry_delay is not None else settings.RETRY_DELAY_SECONDS
    last_exc: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            auth_headers = _get_auth_headers()
            merged_headers = {**auth_headers, **kwargs.pop("headers", {})}

            response = requests.request(
                method,
                url,
                headers=merged_headers,
                timeout=settings.REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )

            if response.status_code < 500:
                # 2xx → success; 4xx → raise immediately (non-retryable)
                response.raise_for_status()
                return response

            # 5xx → log and retry
            logger.warning(
                f"Attempt {attempt}/{retries} — {method.upper()} {url} "
                f"returned HTTP {response.status_code}. Retrying..."
            )
            last_exc = requests.HTTPError(response=response)

        except (requests.ConnectionError, requests.Timeout) as exc:
            logger.warning(
                f"Attempt {attempt}/{retries} — connection error for {url}: {exc}"
            )
            last_exc = exc

        if attempt < retries:
            sleep_time = delay * attempt  # linear back-off
            logger.debug(f"Sleeping {sleep_time}s before next retry")
            time.sleep(sleep_time)

    raise last_exc or RuntimeError(f"All retries exhausted for {method.upper()} {url}")


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

def upload_pdfs(
    queue_id: int,
    dcr_number: str,
    current_pdf_bytes: bytes,
    new_pdf_bytes: bytes,
) -> bool:
    """
    Upload two PDF documents and DCR metadata to the external service.

    Calls:
        POST /api/v1/dcrs/upload
        Content-Type: multipart/form-data

    Form fields:
        file1        — current (old) revision PDF
        file2        — new revision PDF
        dcr_number   — DcrNumber from the queue record
        queue_id     — QueueId from the queue record

    Returns:
        str: The backend's internal dcr_id (may differ from dcr_number)

    Raises:
        requests.HTTPError: On 4xx/5xx after retries
        RuntimeError:       On connection failures
    """
    url = _api_url("/api/v1/dcrs/upload")
    # Build URL with dcr_id as query param — backend requires it (Drawing Hub ID)
    url_with_params = f"{url}?dcr_id={dcr_number}"
    logger.info(
        f"[QueueId={queue_id}] Uploading PDFs to external service → {url_with_params} "
        f"(DcrNumber={dcr_number}) [Drawing Hub DCR ID]"
    )

    files = {
        "old_file": (f"{dcr_number}_current.pdf", BytesIO(current_pdf_bytes), "application/pdf"),
        "new_file": (f"{dcr_number}_new.pdf",     BytesIO(new_pdf_bytes),     "application/pdf"),
    }
    data = {
        "dcr_number": dcr_number,
        "queue_id":   str(queue_id),
    }

    response = _request_with_retry("post", url_with_params, files=files, data=data)
    logger.info(f"[QueueId={queue_id}] Upload successful (HTTP {response.status_code})")

    # Extract the dcr_id from the upload response
    # The backend now accepts our dcr_id and uses it directly,
    # so the response dcr_id should match dcr_number.
    # We still parse the response as a safety check.
    try:
        upload_result = response.json()
        backend_dcr_id = upload_result.get("dcr_id", dcr_number)
        logger.info(
            f"[QueueId={queue_id}] Backend confirmed dcr_id='{backend_dcr_id}' "
            f"(Drawing Hub DcrNumber='{dcr_number}')"
        )
    except (ValueError, AttributeError):
        logger.warning(
            f"[QueueId={queue_id}] Could not parse upload response, "
            f"using DcrNumber='{dcr_number}' as dcr_id"
        )
        backend_dcr_id = dcr_number

    # Step 2: Trigger text comparison (Backend does NOT auto-run this after upload)
    compare_url = _api_url(f"/api/v1/dcrs/{backend_dcr_id}/compare/text")
    logger.info(f"[QueueId={queue_id}] Triggering text comparison → {compare_url}")
    _request_with_retry("post", compare_url)
    logger.info(f"[QueueId={queue_id}] Text comparison triggered successfully")

    # Step 3: Generate report (makes status → Completed)
    report_url = _api_url(f"/api/v1/dcrs/{backend_dcr_id}/report")
    logger.info(f"[QueueId={queue_id}] Triggering report generation → {report_url}")
    _request_with_retry("post", report_url)
    logger.info(f"[QueueId={queue_id}] Report generated — DCR is now Completed")

    return backend_dcr_id


def check_status(queue_id: int, dcr_number: str) -> Dict[str, Any]:
    """
    Check the processing status of a previously uploaded comparison job.

    Calls:
        GET /api/v1/dcrs/{dcr_number}/status

    Returns:
        Parsed JSON response, e.g.:
            {"status": "In Progress"}
            {"status": "Completed", "ResultPDFLink": "https://..."}

    Raises:
        requests.HTTPError: On 4xx/5xx after retries
        RuntimeError:       On connection failures or invalid JSON response
    """
    url = _api_url(f"/api/v1/dcrs/{dcr_number}/status")
    logger.info(f"[QueueId={queue_id}] Checking status → {url}")

    response = _request_with_retry("get", url)

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"[QueueId={queue_id}] Unexpected non-JSON response from status API: "
            f"{response.text[:200]}"
        ) from exc

    status = payload.get("status", "<missing>")
    logger.info(f"[QueueId={queue_id}] External service status: {status!r}")
    return payload


def fetch_comparison_results(dcr_id: str) -> Dict[str, Any]:
    """
    Fetch the text comparison results for a completed DCR.

    Calls:
        POST /api/v1/dcrs/{dcr_id}/compare/text

    Returns:
        Parsed JSON response containing:
            {
                "dcr_id": "...",
                "drawing_number": "...",
                "text_changes": [...],           # Engineering changes
                "changes_implemented": {
                    "bom_changes": [...],         # BOM changes
                    "dimensional_changes": [...],
                    "note_changes": [...]
                }
            }

    Raises:
        requests.HTTPError: On 4xx/5xx after retries
        RuntimeError:       On connection failures or invalid JSON response
    """
    url = _api_url(f"/api/v1/dcrs/{dcr_id}/compare/text")
    logger.info(f"[DCR={dcr_id}] Fetching comparison results → {url}")

    response = _request_with_retry("post", url)

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"[DCR={dcr_id}] Unexpected non-JSON response from compare/text API: "
            f"{response.text[:200]}"
        ) from exc

    text_changes = payload.get("text_changes", [])
    changes_impl = payload.get("changes_implemented", {})
    bom_count = len(changes_impl.get("bom_changes", []))
    dim_count = len(changes_impl.get("dimensional_changes", []))

    logger.info(
        f"[DCR={dcr_id}] Comparison results: "
        f"{len(text_changes)} text change(s), "
        f"{bom_count} BOM change(s), "
        f"{dim_count} dimensional change(s)"
    )
    return payload


# ---------------------------------------------------------------------------
# Step 7b: Fetch pre-structured change details (JSON endpoints)
# ---------------------------------------------------------------------------

def fetch_engineering_changes(dcr_id: str) -> Dict[str, Any]:
    """
    Fetch engineering changes as structured JSON from:
        GET /api/v1/dcrs/{dcr_id}/changes/engineering

    Returns dict with keys: dcr_id, count, changes (list of dicts matching
    the client's EngineeringChangeDetail table schema).
    """
    url = _api_url(f"/api/v1/dcrs/{dcr_id}/changes/engineering")
    logger.info(f"[DCR={dcr_id}] Fetching engineering changes → {url}")

    response = _request_with_retry("get", url)

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"[DCR={dcr_id}] Non-JSON response from engineering changes API: "
            f"{response.text[:200]}"
        ) from exc

    count = payload.get("count", 0)
    logger.info(f"[DCR={dcr_id}] Engineering changes: {count} row(s)")
    return payload


def fetch_bom_changes(dcr_id: str) -> Dict[str, Any]:
    """
    Fetch BOM/dimensional changes as structured JSON from:
        GET /api/v1/dcrs/{dcr_id}/changes/bom

    Returns dict with keys: dcr_id, count, changes (list of dicts matching
    the client's BomChangeDetail table schema).
    """
    url = _api_url(f"/api/v1/dcrs/{dcr_id}/changes/bom")
    logger.info(f"[DCR={dcr_id}] Fetching BOM changes → {url}")

    response = _request_with_retry("get", url)

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"[DCR={dcr_id}] Non-JSON response from BOM changes API: "
            f"{response.text[:200]}"
        ) from exc

    count = payload.get("count", 0)
    logger.info(f"[DCR={dcr_id}] BOM changes: {count} row(s)")
    return payload

