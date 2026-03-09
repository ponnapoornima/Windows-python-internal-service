# DCR Queue Polling Service

A Python background service that polls the **DrawingChangeCompareQueue** table in SQL Server and orchestrates DCR (Drawing Change Request) comparison jobs via an external API.

## Architecture

```
SQL Server — DrawingChangeCompareQueue
  Status = 'Queued'
         │
         ▼
  This Service (poller)
   Phase 1: Upload
    1. Claims record (Status → 'Processing')
    2. Reads old + new PDFs from PDF_SOURCE_DIR
    3. Uploads to external service  → POST /api/v1/dcrs/upload

   Phase 2: Status Check
    4. Polls external service       → GET /api/v1/dcrs/{queueId}/status
    5. If 'Completed' → downloads ResultPDFLink to network drive
    6. Marks record (Status → 'Pending Review', CompareResult → 'Successful')
         │
         ▼
  Result PDF saved to RESULT_PDF_SAVE_PATH
```

## Pre-requisites

| Requirement | Details |
|---|---|
| Python | 3.11+ |
| ODBC Driver | Microsoft ODBC Driver 17 for SQL Server |
| Network | Must reach SQL Server AND external API over HTTPS |

## Setup

```bash
# 1. Clone / copy this folder
cd DCR-Queue-Polling-Service

# 2. Create virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env
# Edit .env with your actual values

# 5. Run
python main.py
```

## Configuration (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `SQL_SERVER_HOST` | ✅ | — | SQL Server hostname or IP |
| `SQL_SERVER_DATABASE` | ✅ | `DrawingHub` | Database name |
| `SQL_SERVER_USERNAME` | ✅ | — | DB login username |
| `SQL_SERVER_PASSWORD` | ✅ | — | DB login password |
| `EXTERNAL_API_BASE_URL` | ✅ | — | External comparison service URL |
| `PDF_SOURCE_DIR` | ✅ | — | Path to source PDFs |
| `RESULT_PDF_SAVE_PATH` | ✅ | — | Path to save result PDFs |
| `POLL_INTERVAL_SECONDS` | — | `5` | Seconds between polls |
| `MAX_CONCURRENT_JOBS` | — | `3` | Max parallel worker threads |
| `APIM_SUBSCRIPTION_KEY` | — | — | APIM subscription key (required for APIM) |

## Running Tests

```bash
python -m pytest tests/ -v
```

## Docker

```bash
docker build -t dcr-queue-polling-service .
docker run --env-file .env dcr-queue-polling-service
```

## Project Structure

```
DCR-Queue-Polling-Service/
├── app/
│   ├── __init__.py
│   ├── (auth removed)       # Auth is now via APIM subscription key header
│   ├── config.py            # Pydantic settings from .env
│   ├── db_client.py         # SQL Server queue operations (pyodbc)
│   ├── external_api_client.py  # Upload + status check HTTP client
│   ├── file_downloader.py   # Stream-download result PDFs
│   ├── logging_config.py    # Rotating file + console logging
│   ├── models.py            # QueueRecord dataclass
│   ├── pdf_reader.py        # Read source PDF pairs from disk
│   └── poller.py            # Two-phase polling engine
├── tests/
│   ├── conftest.py
│   ├── test_db_client.py
│   ├── test_external_api_client.py
│   ├── test_file_downloader.py
│   └── test_poller.py
├── main.py
├── requirements.txt
├── .env.example
└── Dockerfile
```
