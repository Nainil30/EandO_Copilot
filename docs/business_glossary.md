# Business Glossary: E&O Copilot

## Excess inventory
Excess is inventory that is not expected to be consumed by remaining demand.

Default horizon:
- Remaining forecast horizon = next 12 weeks unless user specifies a different horizon.

## Excess quantity
For a part at a supplier/location:
excess_qty = on_hand_qty - SUM(forecasted_units over horizon)

If excess_qty <= 0: no excess.

## Excess value
excess_value = excess_qty * unit_cost

## Scrap recommendation
Default recommendation:
- scrap_recommended = floor(excess_qty * 0.70)
- hold_recommended = excess_qty - scrap_recommended

## Lifecycle
- Active: currently shipping/servicing
- EOL: end-of-life initiated (final buys may exist)
- EOSS: end of support services (may still have service demand until EOSS)

## LTB (Last Time Buy)
LTB orders are final buys during EOL/EOSS transition.


1.3 Install Python packages

From repo root:

pip install -r requirements.txt
pip freeze > requirements.lock.txt


Note:

If install errors happen, check Python version and pinned versions.

If Chroma fails with NumPy 2.x issues, pin NumPy < 2.

Phase 2 — PostgreSQL in Docker (Local)
2.1 Start Docker Desktop

Open Docker Desktop

Wait until it shows “Docker is running”

2.2 Start Postgres container using docker compose

From repo root (IMPORTANT):

docker compose up -d
docker ps


Expected:

Container eando_postgres shows “healthy”

Port mapping: 5432:5432

2.3 Confirm schema exists
docker exec -it eando_postgres psql -U copilot_user -d eando_copilot -c "\dt"


Expected:

9 tables listed:

dim_part, dim_platform, dim_supplier

fact_bom, fact_inventory, fact_forecast, fact_ltb_orders

fact_excess_calculation, fact_scrap_approval

2.4 Stop Postgres cleanly
docker compose down


To wipe data (ONLY if you want a fresh DB):

docker compose down -v

Phase 3 — Fake Data + Load into Postgres
3.1 Generate fake CSVs

From repo root:

python scripts\generate_data.py


Expected:

CSV files appear in data/

Example: data\dim_part.csv, data\fact_inventory.csv, etc.

3.2 Load CSVs into Postgres

Before loading:

Ensure docker is up + healthy

Ensure venv active

Run:

python scripts\load_to_postgres.py


Expected:

“Truncating tables…”

Inserts happen for each table

No duplicate key errors (part_number uniqueness handled)

No NaT datetime errors (NaT converted to None/NULL in loader)

3.3 Verify row counts quickly
docker exec -it eando_postgres psql -U copilot_user -d eando_copilot -c "select count(*) from dim_part;"
docker exec -it eando_postgres psql -U copilot_user -d eando_copilot -c "select count(*) from fact_excess_calculation;"

Phase 4 — FastAPI + Swagger
4.1 Run API

From repo root with venv active:

python -m uvicorn app.api.main:app --reload --port 8000


Expected:

Server running at: http://127.0.0.1:8000

Swagger at: http://127.0.0.1:8000/docs

4.2 Stop API

Press Ctrl + C in the terminal running uvicorn.

Note:

You may see KeyboardInterrupt / CancelledError logs. This is normal shutdown.

Phase 5 — Text-to-SQL Core
Implemented endpoints

GET /health

GET /schema

POST /query (direct SQL)

POST /nlq (natural language → SQL → validate → execute)

POST /rag/build (build KB into Chroma)

POST /rag/debug (inspect retrieval + rerank selection)

Behavior

/nlq:

Retrieve context (schema + business + examples)

(Optional) rerank

Generate SQL via Gemini

Validate SQL is safe (SELECT-only)

Execute SQL and return rows

Phase 6 — RAG (Hybrid) + Rerank
What exists

Vector store using Chroma

Hybrid retrieval:

Vector similarity search

BM25 fallback/boost

Merge + heuristic sort

RAG build collects:

schema docs (tables, columns, joins)

business glossary / definitions

curated SQL examples

Reranker:

Retrieve top ~12 chunks

Gemini reranks to best ~6 to reduce noise and improve join correctness

Known fixes already applied

Postgres column mismatch fixed:

Correct column is dim_part.lifecycle_state (not lifecycle)

Chroma include list fixed:

Do not request ids in include; Chroma returns ids by default

Phase 7 — Company-grade (Planned next)

Audit logs for each NLQ call:

question, retrieval ids, rerank ids, sql, rowcount, latency, model ids, kb version

Eval harness:

30–50 NLQ questions + expected patterns

pass rate, unsafe SQL blocked rate, latency

Later: Streamlit UI + “trust panel” showing SQL + citations + execution metadata

Standard run sequence (daily)

Activate venv:

cd C:\Users\naini\eando-copilot
.\.venv\Scripts\Activate.ps1


Start DB:

docker compose up -d
docker ps


Start API:

python -m uvicorn app.api.main:app --reload --port 8000


Build KB (once per KB change) in Swagger:

POST /rag/build

Test NLQ:

POST /nlq

Standard shutdown sequence (daily)

Stop API: Ctrl+C in uvicorn terminal

Stop DB:

docker compose down
