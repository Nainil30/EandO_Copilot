# BUILD LOG — E&O Copilot (Text-to-SQL + RAG)

This file is the “single source of truth” for how this project was built and how to reproduce it from scratch on Windows 11.

---

## System Assumptions
- OS: Windows 11
- Python: 3.11 installed system-wide
- IDE: VS Code and/or Cursor
- Docker Desktop installed and working (hello-world test passes)

---

## Repo Layout (current)
Project root: `C:\Users\naini\eando-copilot`

Key folders:
- `app/` → FastAPI app + core logic
- `db/` → schema.sql for Postgres
- `scripts/` → data generator + loader scripts
- `data/` → generated CSVs (local)
- `docs/` → living project documentation (this folder)
- `.venv/` → Python virtual environment (local)
- `.env` → secrets (local; NOT COMMITTED)

---

## Day 0 / Day 1 — Project bootstrap

### Goal
Create a clean local dev environment that can run the API and database reliably.

### Steps (PowerShell in VS Code)
1) Open folder in VS Code:
   - File → Open Folder → `C:\Users\naini\eando-copilot`

2) Confirm Python is installed:
   - `python --version`

3) Create a virtual environment (only once):
   - `python -m venv .venv`

4) Activate venv (every new terminal session):
   - `.\.venv\Scripts\Activate.ps1`

5) Confirm venv is active:
   - `python -c "import sys; print(sys.prefix)"`

6) Upgrade pip:
   - `python -m pip install --upgrade pip`

7) Install dependencies:
   - `pip install -r requirements.txt`

### Why venv?
Keeps your project dependencies isolated and reproducible.

---

## Day 2 — Docker + PostgreSQL

### Goal
Run Postgres locally via Docker Compose so anyone can reproduce the DB without manual installs.

### Start Docker Desktop
- Open Docker Desktop → wait until it says “Running”

### Validate Docker works
- `docker run hello-world`

### Start Postgres (from project root)
1) `cd C:\Users\naini\eando-copilot`
2) `docker compose up -d`

### Verify container running
- `docker ps`

Expected: container named `eando_postgres` with port 5432 mapped.

### Verify schema exists
- `docker exec -it eando_postgres psql -U copilot_user -d eando_copilot -c "\dt"`

Expected: 9 tables (dim_part, dim_supplier, etc.)

### Stop Postgres safely
- `docker compose down`

Why stop?
- Frees memory and ports when you’re not working.

---

## Day 3 — Generate fake data + load to Postgres

### Goal
Create realistic supply-chain + E&O tables locally (no company data) and load them into Postgres.

### Required: Postgres must be running
- `docker compose up -d`

### Generate CSVs
- `python scripts\generate_data.py`

Output:
- CSV files created in `data/`

### Load CSVs into Postgres
- `python scripts\load_to_postgres.py`

Notes on earlier issues solved:
- Fixed NaT/None date handling for platform EOL dates.
- Fixed duplicate part_number collisions by generating unique part numbers.

### Verify row counts
- `docker exec -it eando_postgres psql -U copilot_user -d eando_copilot -c "select count(*) from dim_part;"`
- `docker exec -it eando_postgres psql -U copilot_user -d eando_copilot -c "select count(*) from fact_excess_calculation;"`

---

## Day 4 — FastAPI + Swagger baseline

### Goal
Expose the service through an API that managers/tools can use.

### Start API (venv active)
- `python -m uvicorn app.api.main:app --reload --port 8000`

### Open Swagger docs
- http://127.0.0.1:8000/docs

### Stop API
- Press `CTRL + C` in terminal

Note:
- “KeyboardInterrupt / CancelledError” logs during shutdown are normal.

---

## Day 5 — RAG KB Build + NLQ working end-to-end

### Goal
Build a knowledge base (schema docs + business glossary + SQL examples), then let users ask NL questions and get SQL + results.

### Start sequence (always use this order)
1) Start Postgres:
   - `docker compose up -d`

2) Start API:
   - `python -m uvicorn app.api.main:app --reload --port 8000`

3) Swagger:
   - POST `/rag/build`
   - POST `/nlq` (execute=true)

### Stop sequence (always use this order)
1) Stop API:
   - CTRL + C
2) Stop Postgres:
   - `docker compose down`

---

## Day 6 — Hybrid retrieval fixes (Chroma include issues)
### Issue
Chroma `include=["ids"]` is invalid; `ids` is returned automatically and should not be requested.

### Fix
Updated hybrid retriever to only request:
- documents
- metadatas
- distances (for query)

Now NLQ works without Chroma include errors.

---

## Day 7 (today) — Planned work (company-grade upgrades)

### Part A: Reranker
Goal: retrieve 12 → rerank to best 6 → generate SQL using only the best 6.

Implementation:
- add `app/llm/gemini_client.py`
- add `app/rag/reranker.py`
- update `app/core/text2sql.py` to use reranker
- add `/rag/debug` endpoint to inspect retrieval vs reranked chunks

### Part B: Audit logs
Goal: write one JSON line per NLQ request so we can debug, measure, and govern usage:
- store question, retrieved IDs, reranked IDs, SQL, rowcount, latency, prompt_version, kb_version

Implementation:
- add `app/core/audit.py`
- write `.logs/audit.jsonl` per request
- add `.logs/` to `.gitignore`

---

## Daily Runbook (copy/paste)

### Start day
1) Open VS Code → open folder `C:\Users\naini\eando-copilot`
2) Terminal:
   - `.\.venv\Scripts\Activate.ps1`
3) Start Postgres:
   - `docker compose up -d`
4) Start API:
   - `python -m uvicorn app.api.main:app --reload --port 8000`
5) Swagger:
   - http://127.0.0.1:8000/docs

### End day
1) Stop API:
   - CTRL + C
2) Stop Postgres:
   - `docker compose down`
