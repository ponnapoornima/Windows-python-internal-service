# ---------------------------------------------------------------------------
# DCR Queue Polling Service — Dockerfile
#
# Build:  docker build -t dcr-queue-polling-service .
# Run:    docker run --env-file .env dcr-queue-polling-service
# ---------------------------------------------------------------------------

FROM python:3.11-slim

WORKDIR /service

# Install OS-level dependencies (ODBC Driver 17 for SQL Server)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    unixodbc \
    unixodbc-dev \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/11/prod.list \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create log directory
RUN mkdir -p logs

CMD ["python", "main.py"]
