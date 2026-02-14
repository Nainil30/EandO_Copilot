# E&O Copilot (Text → SQL + RAG + Guardrails)

E&O Copilot is a **company-grade analytics assistant** that converts **natural language questions** into **safe PostgreSQL SELECT queries**, executes them against a Postgres database, and returns results with **citations + retrieval transparency**.

This is not a toy “basic RAG” demo. It includes the core patterns real teams need:
- **Hybrid retrieval (vector + BM25)** over schema + business glossary + SQL examples
- **LLM reranking** to reduce context to only the most relevant chunks
- **SQL guardrails** (validation + allowlist-style behavior through context)
- **Execution visibility** (retrieval preview + citations)
- **Audit logging** (governance / traceability)
- **Evaluation harness** (repeatable quality measurement)
- Minimal, functional **Streamlit UI** for analysts/managers

---

## What problem it solves

In many companies, analysts know the data but business stakeholders don’t. Stakeholders ask questions like:
- “Top 10 parts by excess value for EOL parts”
- “Which suppliers contribute most to excess?”
- “Show forecast vs inventory for platform X”

Instead of making stakeholders wait for an analyst, this system:
1. Retrieves schema + definitions + patterns (examples)
2. Generates one safe SQL query
3. Validates it
4. Executes it (optional)
5. Returns results + “why it wrote that SQL” (citations + retrieval preview)


## Key Features

### 1) FastAPI backend (production-friendly API)
**What:** Provides clean endpoints for health, schema, retrieval build/debug, NLQ-to-SQL, and raw SQL execution.  
**Why:** Real systems are API-driven so UI / other services can consume them.

Endpoints:
- `GET /health` — service check
- `GET /schema` — schema snapshot (introspection)
- `POST /rag/build` — build vector KB from docs/schema/examples
- `POST /rag/debug` — inspect retrieval + reranker selection
- `POST /nlq` — NLQ → SQL → validate → execute → return results
- `POST /query` — run SQL directly (still validated)

---

### 2) Postgres via Docker (repeatable local setup)
**What:** Local Postgres runs in Docker and is loaded with sample E&O tables.  
**Why:** Zero dependency on external DB accounts. Anyone can replicate.

---

### 3) Hybrid Retrieval (Vector + BM25)
**What:** Combines semantic retrieval (embeddings) + lexical retrieval (BM25).  
**Why:** Pure vector retrieval misses exact tokens like part numbers, column names, or lifecycle states.  
Hybrid retrieval improves accuracy in real enterprise data.

Collections used:
- Schema chunks (tables, columns, foreign keys)
- Business glossary chunks (definitions/metrics)
- SQL example chunks (known-good patterns)

---

### 4) Reranker (LLM reorders context)
**What:** Retrieve top N chunks (e.g., 12) then rerank down to best K (e.g., 6).  
**Why:** Big accuracy jump and lower hallucination risk.  
This is a very common production pattern because LLMs perform best with **tight context**.

---

### 5) Guardrails (SQL safety)
**What:** Generated SQL must be:
- single SELECT only
- no semicolons
- no DDL/DML (DROP/DELETE/UPDATE/INSERT)
- only known tables/columns (enforced through “context-only” behavior + validation)
**Why:** This is non-negotiable for company environments.

---

### 6) Audit logging (governance)
**What:** Each NLQ request writes a JSON line log (question → retrieval → sql → execution stats).  
**Why:** Real teams need traceability for debugging, governance, incident review, and continuous improvement.

---

### 7) Eval harness (quality measurement)
**What:** Batch-run a list of NLQ questions and score:
- pass rate (did SQL run?)
- unsafe SQL blocked rate
- latency
- common failure reasons
**Why:** This is the difference between a hobby demo and something you can trust and improve.

## Architecture Overview

High-level flow for `/nlq`:

1) **User question** (natural language)
2) **Retrieve context**
   - Vector search per collection (schema, business, examples)
   - BM25 ranking (keyword matching) on schema collection
   - Merge + prioritize results
3) **Rerank context (LLM)**
   - Use Gemini Flash to select the best chunks
4) **Build prompt**
   - System rules + authoritative context + question
5) **Generate SQL (LLM)**
6) **Validate SQL**
   - must be safe SELECT only
7) **Execute SQL** in Postgres
8) Return:
   - generated SQL
   - results
   - citations (doc ids)
   - retrieval preview (what it used)

Key design rule:
> The model is only allowed to use tables/columns that exist in the retrieved context.

## Repository Structure
app/
api/
main.py # FastAPI endpoints
core/
db.py # DB execution
schema_introspect.py # schema snapshot
sql_validate.py # SQL safety validator
text2sql.py # retrieval + rerank + prompt builder
audit.py # audit logging (JSONL)
sql_repair.py # optional: deterministic repair attempt
llm/
gemini.py # SQL generation wrapper
gemini_client.py # small shared Gemini client wrapper
rag/
kb_builder.py # builds knowledge base docs into vector store
vector_store.py # Chroma collection helpers + embed calls
hybrid_retriever.py # vector + bm25 retrieval, caching
reranker.py # rerank top retrieval items using Gemini
prompt_context.py # formats context + citations for prompt

db/
docker-compose.yml # (optional if separated)
docker-compose.yml # Postgres container + volume

data/
*.csv # sample data (dim/fact tables)

scripts/
load_to_postgres.py # loads CSVs into Postgres
run_evals.py # eval harness runner
test_gemini.py # quick LLM sanity test

tests/
evals.jsonl # NLQ eval set

ui/
api_client.py # thin HTTP client used by Streamlit
streamlit_app.py # minimal UI

docs/
BUILD_LOG.md # daily build notes
ARCHITECTURE.md # architecture explanation
DECISIONS.md # ADR-lite decisions
PROMPTS.md # prompt versions

## Setup (Windows 11)

### 0) Prerequisites
- Python 3.11+
- Docker Desktop
- Git (optional if already cloned/downloaded)

---

### 1) Clone repo
```bash
git clone https://github.com/<your-username>/eando-copilot.git
cd eando-copilot

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

python -c "import sys; print(sys.prefix)"
 
### 2) Create .env (DO NOT COMMIT THIS)
GEMINI_API_KEY=your_key_here

# Models (you can change later)
GEMINI_SQL_MODEL=gemini-1.5-flash
GEMINI_RERANK_MODEL=gemini-1.5-flash
GEMINI_EMBED_MODEL=text-embedding-004

# App switches (avoid accidental token spend)
RERANK_ENABLED=true
EVALS_ENABLED=false
AUDIT_ENABLED=true

PROMPT_VERSION=v1
KB_VERSION=v1

### 3) Create .env (DO NOT COMMIT THIS)
docker compose up -d
docker ps


4) Start Postgres using Docker

From the repo root:

docker compose up -d
docker ps


Verify tables exist:

docker exec -it eando_postgres psql -U copilot_user -d eando_copilot -c "\dt"

5) Load CSV data into Postgres
python scripts\load_to_postgres.py

6) Build the RAG knowledge base (required once)

Start API first (next step), then call /rag/build OR run through Swagger.


---

## PART 6 — Run backend + Swagger workflow

```md
## Running the Backend (FastAPI)

### 1) Start the API
From repo root with venv active:
```powershell
python -m uvicorn app.api.main:app --reload --port 8000

2) Open Swagger Docs

Open in browser:

http://127.0.0.1:8000/docs

3) Recommended test sequence in Swagger

GET /health → should return { "status": "ok" }

GET /schema → confirms DB visibility

POST /rag/build → builds knowledge base (vector store)

POST /rag/debug with:

{ "question": "Top 10 parts by excess value for EOL parts" }


Verify rerank selected chunk IDs make sense.

POST /nlq with:

{ "question": "Top 10 parts by excess value for EOL parts" }


You should get:

generated SQL

results

citations

retrieval preview

Stopping services safely
Stop FastAPI

In the terminal running uvicorn:

Press CTRL + C

Stop Docker (Postgres)

From repo root:

docker compose down


If you want to delete DB volume data:

docker compose down -v


---

## PART 7 — Streamlit UI (workflow)

```md
## Streamlit UI

### 1) Start backend first
```powershell
python -m uvicorn app.api.main:app --reload --port 8000

2) Start Streamlit

In a second terminal (venv active):

streamlit run ui/streamlit_app.py

3) What UI does

NLQ: sends question to /nlq and shows results + SQL + citations

RAG Build: triggers /rag/build (explicit action)

RAG Debug: triggers /rag/debug to show retrieval + rerank selection

Run SQL: sends SQL to /query (still validated)


---

## PART 8 — Eval harness (how to run + not accidentally burn tokens)

```md
## Eval Harness

### What it is
A repeatable script to measure system quality using a fixed question set.

### Files
- `tests/evals.jsonl` — list of NLQ questions
- `scripts/run_evals.py` — runs NLQ in batch and outputs metrics

### How to run
Make sure API is running, then:
```powershell
python scripts\run_evals.py

Important: avoiding accidental LLM spend

This project uses explicit switches so you don’t accidentally burn tokens.

Recommended defaults in .env:

EVALS_ENABLED=false
RERANK_ENABLED=true
AUDIT_ENABLED=true


Only set EVALS_ENABLED=true when you explicitly want to run evals.

If EVALS_ENABLED=false, run_evals.py should exit early (recommended behavior).


---

## PART 9 — Audit logging (governance)

```md
## Audit Logging

### What gets logged
Each NLQ request writes one JSON line to:
- `.logs/audit.jsonl`

Typical fields:
- timestamp
- question
- retrieved doc ids
- rerank selected ids
- generated sql
- execution row_count
- latency
- prompt_version / kb_version

### Why it matters
This is essential for:
- debugging incorrect queries
- measuring drift
- investigating incidents
- improving prompts + retrieval with evidence

### How to confirm logs are written
1) Ensure `.env` contains:
```env
AUDIT_ENABLED=true


Run an NLQ call.

Check:

dir .logs
type .logs\audit.jsonl


---

## PART 10 — Concepts used (and why)

```md
## Concepts Used (and why)

### RAG (Retrieval-Augmented Generation)
Used so the model can reference **real schema + business definitions + examples** instead of guessing.

### Hybrid Retrieval (Vector + BM25)
Vector retrieval = semantic similarity  
BM25 = keyword precision  
Hybrid = better accuracy on real enterprise data (IDs, column names, abbreviations).

### Reranking
Reduces context from “maybe relevant” to “most relevant”.  
Improves accuracy and reduces hallucinations.

### Prompt Versioning + KB Versioning
Makes experiments trackable.  
If SQL quality changes, you can attribute it to prompt or knowledge base changes.

### SQL Guardrails
Prevents unsafe queries and ensures the system is safe to run in production-like settings.

### Audit Logging
Adds traceability and governance.  
Real teams need this for compliance and debugging.

### Evaluation Harness
Lets you measure progress and avoid “it feels better” development.

PART 11 — Interview-ready “How I built this” narrative
## How I built this (interview narrative)

1) I created a reproducible data environment (Postgres in Docker + CSV loader).
2) I exposed the system through a FastAPI backend with clean endpoints and Swagger.
3) I implemented RAG over three knowledge sources:
   - schema / relationships
   - business definitions
   - SQL examples
4) I used hybrid retrieval and caching to improve reliability and speed.
5) I added an LLM reranker to narrow context before generation (production pattern).
6) I built strict SQL validation guardrails and fail-safe error handling.
7) I added governance: audit logs capturing full trace from question → SQL → execution.
8) I built an eval harness to quantify quality and track improvements.
9) I wrapped everything in a minimal Streamlit UI for real usage.

