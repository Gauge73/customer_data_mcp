# Customer Data MCP Server — PRD

## Overview

An MCP (Model Context Protocol) server exposing CRUD and search operations over a 46-million-row synthetic customer dataset. Packaged as a single Docker container with an embedded SQLite database; built via GitHub Actions and deployed to a local Kubernetes cluster.

---

## Goals

- Provide MCP-compatible tools for creating, reading, updating, deleting, and searching customer records.
- Ship as a single self-contained container — no external database, no sidecar, no volume dependencies at runtime (data is baked in).
- Support fast full-text and field-level search across 46M rows without an external search engine.
- Be buildable in CI (GitHub Actions) and runnable in a local k8s cluster (e.g. k3s, minikube, Rancher Desktop).

---

## Non-Goals

- Production security hardening (PII encryption, mTLS, RBAC) — this is a demo.
- Multi-replica writes / distributed consistency.
- External database (Postgres, MySQL, etc.).
- A REST or GraphQL API — MCP stdio or HTTP/SSE transport only.

---

## Data

**Source:** `data/customer_data.csv` (~3.5 GB, ~46 million rows)

**Schema:**

| Column       | Type    | Notes                        |
|--------------|---------|------------------------------|
| last_name    | TEXT    |                              |
| first_name   | TEXT    |                              |
| ssn          | TEXT    | formatted `NNN-NN-NNNN`      |
| dob          | TEXT    | formatted `MM/DD/YYYY`       |
| city         | TEXT    |                              |
| state        | TEXT    | 2-letter abbreviation        |
| zip          | TEXT    |                              |
| ccn          | TEXT    | credit card number           |
| cc_exp       | TEXT    | formatted `MM/YY`            |

---

## Architecture

### Storage: SQLite (baked into the image)

At 46M rows / 3.5 GB CSV, a plain file read is too slow for search. SQLite is the right fit:

- Single file, zero infrastructure.
- Supports B-tree indexes for O(log n) field lookups.
- Supports FTS5 (full-text search) for free-text queries.
- Python's `sqlite3` is in the standard library — no extra deps.
- The `.db` file is generated at image build time from the CSV and baked into the layer.

**Estimated SQLite file size:** ~6–8 GB (uncompressed rows + indexes). The Docker image will be large but self-contained.

**Indexes to create at build time:**
- `last_name`, `first_name` (for name lookups)
- `ssn` UNIQUE (for exact SSN lookup)
- `state`, `zip` (for geographic filters)
- FTS5 virtual table over `first_name`, `last_name`, `city` (for free-text search)

### MCP Server: Python + `mcp` SDK

Use the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). Expose tools over **stdio** transport (standard for MCP) and optionally over **HTTP/SSE** for k8s health-check compatibility.

### Transport

- Primary: **stdio** (used when Claude Desktop or another MCP client spawns the container via `docker run`).
- Secondary: **HTTP/SSE** on port `8080` — useful for k8s liveness probes and future web clients.

---

## MCP Tools

| Tool Name           | Description                                              | Key Parameters                                      |
|---------------------|----------------------------------------------------------|-----------------------------------------------------|
| `get_customer`      | Fetch a single customer by internal row ID               | `id: int`                                           |
| `find_by_ssn`       | Exact lookup by SSN                                      | `ssn: str`                                          |
| `search_customers`  | Search by any combination of fields; supports partial match on name/city via FTS | `query?: str`, `first_name?: str`, `last_name?: str`, `state?: str`, `zip?: str`, `dob?: str`, `limit?: int` (default 20, max 100) |
| `create_customer`   | Insert a new customer record                             | all schema fields (ssn must be unique)              |
| `update_customer`   | Update fields on an existing record                      | `id: int`, any subset of schema fields              |
| `delete_customer`   | Remove a record by ID                                    | `id: int`                                           |
| `count_customers`   | Return total record count (or count matching a filter)   | same optional filters as `search_customers`         |

All tools return structured JSON. Errors surface as MCP error responses with a human-readable message.

---

## Implementation Plan

### Phase 1 — Local Python MCP Server

1. **Scaffold the project**
   - `pyproject.toml` with deps: `mcp`, `uvicorn` (for HTTP/SSE mode).
   - `src/server.py` — MCP server entry point.
   - `src/db.py` — SQLite connection pool and query helpers.

2. **Database loader script** (`scripts/load_csv.py`)
   - Reads `data/customer_data.csv` in chunks (e.g. 100k rows at a time) using `csv` stdlib.
   - Creates the `customers` table with an auto-increment `id` primary key.
   - Bulk-inserts via `executemany`.
   - Creates all indexes after bulk load (faster than incremental indexing).
   - Creates FTS5 virtual table and populates it.
   - Expected runtime: 5–15 minutes on typical hardware.

3. **Implement MCP tools** in `src/server.py` backed by `src/db.py`.

4. **Verify locally** by running the server in stdio mode and exercising all tools.

### Phase 2 — Docker Image

**Multi-stage `Dockerfile`:**

```
Stage 1 (builder):
  - python:3.12-slim
  - COPY data/customer_data.csv
  - RUN pip install deps
  - RUN python scripts/load_csv.py  → produces /app/data/customers.db

Stage 2 (runtime):
  - python:3.12-slim
  - COPY --from=builder /app/data/customers.db
  - COPY src/
  - ENTRYPOINT ["python", "-m", "src.server"]
```

The CSV is not copied into the runtime stage — only the compiled `.db` file.

**Image size note:** The SQLite file will be ~6–8 GB. Consider using Docker layer caching carefully in CI — the builder stage should be cached as long as the CSV does not change.

### Phase 3 — AWS S3 Setup (one-time, manual)

This is done once before CI is wired up. All steps are in the AWS Console or AWS CLI.

#### 3a. Create an S3 bucket

1. Go to **S3 → Create bucket**.
2. Name it something like `customer-data-mcp-assets`. Choose your preferred region (e.g. `us-east-1`).
3. Leave **Block all public access** enabled (default). The bucket must be private.
4. Leave versioning off unless you want to track CSV revisions.
5. Click **Create bucket**.

#### 3b. Upload the CSV

```bash
aws s3 cp data/customer_data.csv s3://customer-data-mcp-assets/customer_data.csv
```

Or use the S3 Console drag-and-drop. The object key should be exactly `customer_data.csv`.

#### 3c. Create an IAM user for GitHub Actions

GitHub Actions needs credentials that can read this one object. Least-privilege approach:

1. Go to **IAM → Users → Create user**. Name it `github-actions-customer-data-mcp`. No console access needed.
2. On the **Permissions** step, choose **Attach policies directly → Create inline policy**.
3. Use this policy (replace the bucket name if different):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::customer-data-mcp-assets/customer_data.csv"
    }
  ]
}
```

4. Finish creating the user.
5. Go to the user → **Security credentials → Create access key**. Choose **Other** as the use case.
6. Download or copy the **Access key ID** and **Secret access key** — you will not be able to see the secret again.

#### 3d. Store credentials in GitHub

1. Go to your GitHub repo → **Settings → Secrets and variables → Actions**.
2. Add three **Repository secrets**:
   - `AWS_ACCESS_KEY_ID` — the key ID from the previous step.
   - `AWS_SECRET_ACCESS_KEY` — the secret key.
   - `AWS_S3_BUCKET` — the bucket name (e.g. `customer-data-mcp-assets`).
3. Add one **Repository variable** (not a secret — it's not sensitive):
   - `AWS_REGION` — your bucket region (e.g. `us-east-1`).

---

### Phase 4 — GitHub Actions CI

**Workflow: `.github/workflows/build.yml`**

Triggers: push to `main`, manual `workflow_dispatch`.

Steps:
1. Checkout repo (CSV is **not** in git — it lives in S3).
2. Configure AWS credentials using the `aws-actions/configure-aws-credentials` action and the secrets added above.
3. Download `customer_data.csv` from S3 into the build context: `aws s3 cp s3://${{ secrets.AWS_S3_BUCKET }}/customer_data.csv data/customer_data.csv`.
4. Log in to GHCR using `docker/login-action` with `GITHUB_TOKEN`.
5. Set up Docker Buildx with registry cache (`--cache-from` / `--cache-to` pointing at a GHCR cache image) to avoid rebuilding the SQLite DB when only application code changes.
6. Build and push: `docker buildx build --platform linux/amd64 --push -t ghcr.io/<owner>/customer-data-mcp:${{ github.sha }} .`
7. Tag `:latest` on main-branch builds.

**Secrets/variables summary for the workflow:**

| Name | Type | Where set |
|------|------|-----------|
| `GITHUB_TOKEN` | Secret (automatic) | Built into every Actions run |
| `AWS_ACCESS_KEY_ID` | Secret | GitHub repo → Settings → Secrets |
| `AWS_SECRET_ACCESS_KEY` | Secret | GitHub repo → Settings → Secrets |
| `AWS_S3_BUCKET` | Secret | GitHub repo → Settings → Secrets |
| `AWS_REGION` | Variable | GitHub repo → Settings → Variables |

**Cache invalidation:** The builder stage that runs `load_csv.py` is cached by Docker layer content hash. If the CSV changes in S3, the `COPY data/customer_data.csv` layer gets a new hash → cache miss → SQLite DB is rebuilt automatically. If only `src/` changes, the builder layer is a cache hit and the image builds in seconds.

### Phase 5 — Kubernetes Deployment

**Manifests: `k8s/`**

```
k8s/
  deployment.yaml
  service.yaml
  (optional) configmap.yaml
```

**`deployment.yaml` highlights:**
- `image: ghcr.io/<owner>/customer-data-mcp:latest`
- `imagePullPolicy: Always`
- Single replica (reads are idempotent; writes mutate an in-container DB, which is acceptable for a demo).
- Resource requests: `memory: 512Mi, cpu: 250m`; limits: `memory: 2Gi, cpu: 1`.
- Liveness probe: HTTP GET `/health` on port 8080 (served by the HTTP/SSE transport).
- No PersistentVolumeClaim — the DB is read-mostly and resets on pod restart (fine for demo).

**`service.yaml`:**
- `ClusterIP` service on port 8080 (for in-cluster MCP clients over HTTP/SSE).
- For Claude Desktop access from the host: expose via `NodePort` or `kubectl port-forward`.

---

## File Layout

```
customer_data_mcp/
├── data/
│   └── customer_data.csv          # NOT in git — download from S3 locally or in CI
├── scripts/
│   └── load_csv.py                # CSV → SQLite builder
├── src/
│   ├── __init__.py
│   ├── server.py                  # MCP server, tool definitions
│   └── db.py                      # SQLite helpers
├── k8s/
│   ├── deployment.yaml
│   └── service.yaml
├── .github/
│   └── workflows/
│       └── build.yml
├── Dockerfile
├── pyproject.toml
└── prd/
    └── customer-data-mcp.md       # this document
```

---

## Open Questions / Decisions

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Where is the CSV stored? | **S3** — download in CI with AWS CLI before `docker buildx build`. Never commit the CSV or `.db` to git. |
| 2 | Does the k8s cluster have GHCR pull access? | **Yes** (confirmed). Create an `imagePullSecret` from a GitHub PAT with `read:packages` scope and reference it in the Deployment. |
| 3 | Should writes (create/update/delete) persist across pod restarts? | For a demo, no — in-memory SQLite mutations reset on restart. If persistence is later needed, add a PVC and mount the DB file. |
| 4 | MCP transport for Claude Desktop? | Use `stdio` with `docker run -i ghcr.io/.../customer-data-mcp` as the command in Claude Desktop's MCP config. |
