# ─── Stage 1: build the SQLite database from the CSV ────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install Python deps needed only for the loader
COPY pyproject.toml .
RUN pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir "mcp[cli]>=1.0.0"

# Copy loader script
COPY scripts/ scripts/

# Copy the CSV (downloaded from S3 into the build context before `docker build`)
COPY data/customer_data.csv data/customer_data.csv

# Build the SQLite DB. This layer is cached by Docker as long as the CSV and
# loader script are unchanged — no rebuild cost on code-only changes.
RUN python scripts/load_csv.py \
        --csv data/customer_data.csv \
        --db  data/customers.db && \
    # Remove the CSV to keep the layer smaller
    rm data/customer_data.csv


# ─── Stage 2: lean runtime image ────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install runtime deps
COPY pyproject.toml .
RUN pip install --no-cache-dir "mcp[cli]>=1.0.0" "uvicorn>=0.30.0"

# Copy application source
COPY src/ src/

# Copy the compiled database from the builder stage (CSV is NOT included)
COPY --from=builder /app/data/customers.db data/customers.db

# DB_PATH is read by src/db.py; MCP_TRANSPORT and MCP_PORT control the server mode.
ENV DB_PATH=data/customers.db \
    MCP_TRANSPORT=stdio \
    MCP_PORT=8080

EXPOSE 8080

ENTRYPOINT ["python", "-m", "src.server"]
