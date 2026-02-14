# E&O Copilot — Text → SQL with RAG, Guardrails, Reranking, Evals, and a Minimal UI

E&O Copilot is a company-style analytics assistant that converts natural language questions into **safe PostgreSQL `SELECT` queries**, executes them on a Postgres database, and returns results with **citations + retrieval transparency**.

This is not a basic “toy RAG” demo. It uses patterns you actually see in production:
- **Hybrid retrieval (Vector + BM25)** across schema + business glossary + SQL examples
- **LLM reranking** to reduce context to only the most useful chunks
- **SQL guardrails** (strict validation + safe execution)
- **Retrieval transparency** (citations + retrieval preview)
- **Audit logging** (governance / traceability)
- **Evaluation harness** (repeatable quality measurement)
- **Minimal Streamlit UI** for analysts/managers

---

## What problem it solves

In many teams, business stakeholders need answers but don’t write SQL. They ask questions like:
- “Top 10 parts by excess value for EOL parts”
- “Which suppliers contribute most to excess?”
- “Show forecast vs inventory for platform X”

Instead of waiting on an analyst, this system:
1. Retrieves authoritative context (schema, joins, glossary definitions, SQL patterns)
2. Generates one safe SQL query
3. Validates it
4. Executes it
5. Returns results **plus evidence of why it wrote that SQL** (citations, retrieval preview)

---

## High-level workflow (for a non-technical manager)

1. Start Postgres (Docker)
2. Start the API (FastAPI)
3. Open the Streamlit UI
4. Ask a question in English
5. The system generates safe SQL, runs it, and shows results
6. If you want to understand “why”, use the RAG Debug view to see what context was used

Example question:
> Top 10 parts by excess value (calculated_excess * unit_cost) for EOL parts

Typical output:
- SQL query it generated
- Results table
- Citations (which schema/glossary/examples influenced the SQL)
- Retrieval preview (top chunks retrieved)

---

## Core features

### 1) FastAPI backend (API-first, production friendly)
**What it does:** Exposes clean endpoints that any UI/service can call (Swagger included).  
**Why:** Real systems are API-driven.

Endpoints:
- `GET /health` — service check
- `GET /schema` — schema snapshot (introspection)
- `POST /rag/build` — build vector KB from docs/schema/examples
- `POST /rag/debug` — inspect retrieval + reranker selection
- `POST /nlq` — NLQ → SQL → validate → execute → return results
- `POST /query` — run SQL directly (still validated)

---

### 2) Postgres via Docker (reproducible environment)
**What it does:** Runs Postgres locally and loads sample E&O tables from CSV.  
**Why:** No external DB accounts required; anyone can replicate.

---

### 3) Text-to-SQL (NLQ → SQL) with strict guardrails
**What it does:** Converts a natural language question into **exactly one** PostgreSQL `SELECT` query.  
**Why:** That’s the main user value.

**Guardrails used:**
- Output must be a single `SELECT` (no multi-statement)
- No DDL/DML (`DROP/DELETE/UPDATE/INSERT`)
- Enforced `LIMIT` unless user explicitly requests otherwise
- Prompt forces “use only tables/columns shown in Context”
- Validator rejects unsafe SQL before execution

---

### 4) RAG done correctly (schema + glossary + SQL examples)
The model should not guess your schema. RAG gives it authoritative context:
- **Schema chunks**: tables, columns, and join relationships
- **Business glossary chunks**: definitions of metrics and terms
- **SQL examples**: known-good patterns (joins, aggregations, filters)

This reduces hallucination and improves correctness.

---

### 5) Hybrid retrieval (Vector + BM25)
**Vector search (embeddings)** retrieves semantically related text (“excess value” ↔ “overstock cost impact”).  
**BM25** retrieves exact keyword matches (column names, lifecycle states, part numbers).

**Why hybrid:** In enterprise analytics, users mix semantics and exact tokens. Hybrid retrieval handles both.

---

### 6) Reranking (production pattern)
Retrieval returns top N candidate chunks (example: 12).  
Reranker chooses the best K to actually feed into SQL generation (example: 6).

**Why rerank:**
- Higher accuracy (less irrelevant context)
- Lower hallucination risk
- Smaller context budget improves generation quality

---

### 7) Retrieval transparency
NLQ responses include:
- `citations`: chunk IDs used
- `retrieval_preview`: what was retrieved and from which source

**Why:** Debugging and trust. You can see *why* it produced that SQL.

---

### 8) Audit logging (governance)
Each NLQ request can write a JSON line record:
- question
- retrieved doc ids
- rerank selected ids
- generated sql
- execution rowcount
- latency
- prompt_version / kb_version

**Why:** Traceability, incident review, and systematic improvement.

---

### 9) Evaluation harness
Run a fixed set of questions and compute:
- pass rate (SQL executed successfully?)
- unsafe SQL blocked rate
- avg latency
- common failure modes

**Why:** This makes quality measurable and improvable (not vibes-based).

---

## Architecture (how `/nlq` works)

When you call `POST /nlq`, the backend does:

1. **Retrieve context (RAG)**
   - Vector search per collection: schema, business, SQL examples
   - BM25 ranking (keyword precision boost)
   - Merge into a candidate list

2. **Rerank**
   - Reranker model chooses best context chunks

3. **Build prompt**
   - Hard rules + authoritative context + user question

4. **Generate SQL**
   - Gemini generates a single SQL statement

5. **Validate SQL**
   - Blocks unsafe output

6. **Execute**
   - Runs on Postgres

7. **Return**
   - SQL + results + citations + retrieval preview

Key design rule:
> The model is only allowed to use tables/columns that exist in the retrieved Context.

---

## Repository structure

```text
app/
  api/
    main.py                  # FastAPI endpoints
  core/
    db.py                    # DB execution
    schema_introspect.py     # schema snapshot
    sql_validate.py          # SQL safety validator
    text2sql.py              # retrieval + rerank + prompt builder
    audit.py                 # audit logging (JSONL)
    sql_repair.py            # optional: repair attempt after execution errors
  llm/
    gemini.py                # SQL generation wrapper
    gemini_client.py         # shared Gemini client wrapper
  rag/
    kb_builder.py            # builds KB into vector store
    vector_store.py          # Chroma helpers + embeddings
    hybrid_retriever.py      # hybrid retrieval, caching
    reranker.py              # rerank top retrieval items using Gemini
    prompt_context.py        # formats context + citations for prompt

data/
  *.csv                      # sample data (dim/fact tables)

db/
  docker-compose.yml         # optional / legacy (may exist)

docker-compose.yml           # Postgres container + volume (repo root)

scripts/
  load_to_postgres.py        # loads CSVs into Postgres
  run_evals.py               # eval harness runner
  test_gemini.py             # quick LLM sanity test

tests/
  evals.jsonl                # NLQ eval set

ui/
  api_client.py              # thin HTTP client used by Streamlit
  streamlit_app.py           # minimal UI

docs/
  BUILD_LOG.md               # daily build notes
  ARCHITECTURE.md            # deeper architecture explanation
  DECISIONS.md               # ADR-lite decisions
  PROMPTS.md                 # prompt versions

```
## Setup (Windows 11) — step-by-step

### 0) Prerequisites
Make sure you have these installed:

- **Python 3.11+**
- **Docker Desktop** (installed *and running*)
- **Git** (optional if you downloaded the repo as a ZIP)

---

### 1) Clone and enter the repo
```bash
git clone https://github.com/Nainil30/SupplyChain_RAG.git
cd SupplyChain_RAG

## 2) Create and activate a virtual environment (venv)

### PowerShell
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt


````md
## 2) Create and activate a virtual environment (venv)

### PowerShell
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
````

### Verify venv is active

```powershell
python -c "import sys; print(sys.prefix)"
```

You should see a path pointing inside your project folder (e.g., `...\SupplyChain_RAG\.venv\...`).

---

## 3) Create `.env` (DO NOT COMMIT)

Create a file named `.env` in the **repo root**.

```env
GEMINI_API_KEY=your_key_here

# Models
GEMINI_SQL_MODEL=gemini-1.5-flash
GEMINI_RERANK_MODEL=gemini-1.5-flash
GEMINI_EMBED_MODEL=text-embedding-004

# Switches (avoid accidental spend)
RERANK_ENABLED=true
EVALS_ENABLED=false
AUDIT_ENABLED=true

PROMPT_VERSION=v1
KB_VERSION=v1
```

---

## Start services (recommended sequence)

### Step A — Start Postgres using Docker

From the repo root:

```powershell
docker compose up -d
docker ps
```

Confirm tables exist:

```powershell
docker exec -it eando_postgres psql -U copilot_user -d eando_copilot -c "\dt"
```

---

### Step B — Load sample data into Postgres

```powershell
python scripts\load_to_postgres.py
```

---

### Step C — Start FastAPI

```powershell
python -m uvicorn app.api.main:app --reload --port 8000
```

---

### Step D — Open Swagger (API UI)

Open this in your browser:

* `http://127.0.0.1:8000/docs`

Recommended Swagger test order:

1. `GET /health`
2. `GET /schema`
3. `POST /rag/build`
4. `POST /rag/debug` with:

   ```json
   { "question": "Top 10 parts by excess value for EOL parts" }
   ```
5. `POST /nlq` with:

   ```json
   { "question": "Top 10 parts by excess value for EOL parts" }
   ```

---

## Streamlit UI

### 1) Start backend first

```powershell
python -m uvicorn app.api.main:app --reload --port 8000
```

### 2) Start Streamlit (new terminal, venv active)

```powershell
streamlit run ui/streamlit_app.py
```

### What the UI does (simple)

* **NLQ**: calls `/nlq` and displays SQL + results + citations
* **RAG Build**: calls `/rag/build`
* **RAG Debug**: calls `/rag/debug` for retrieval + rerank inspection
* **Run SQL**: calls `/query` (still validated)

---

## Stopping everything safely

### Stop FastAPI

In the terminal running uvicorn:

* Press `CTRL + C`

### Stop Docker Postgres

From the repo root:

```powershell
docker compose down
```

If you want to delete the DB data volume too:

```powershell
docker compose down -v
```

---

## Eval harness (optional, measurable quality)

### What it is

Batch-run a fixed set of NLQ questions to measure:

* pass rate
* unsafe SQL blocked rate
* latency
* common failure modes

### Files

* `tests/evals.jsonl`
* `scripts/run_evals.py`

### Run

```powershell
python scripts/run_evals.py
```

### To avoid accidental spend

Keep this in `.env` unless you are explicitly running evals:

```env
EVALS_ENABLED=false
```

---

## Audit logging (optional, governance)

### Where logs are written

Audit logs are written to:

* `.logs/audit.jsonl`

### Confirm logs exist

```powershell
dir .logs
type .logs\audit.jsonl
```

### To disable audit logs

Set in `.env`:

```env
AUDIT_ENABLED=false
```

```
::contentReference[oaicite:0]{index=0}
```


## Concepts Used (and Why)

* **Natural Language Query (NLQ)**
    * Users ask questions in plain English.
    * This is the manager-friendly interface.
* **Text-to-SQL**
    * The system generates SQL from NLQ.
    * Useful only if it stays safe and correct.
* **Embeddings (Vector Search)**
    * Transforms text into vectors to search by meaning.
    * Used for semantic retrieval across schema, glossary, and examples.
* **RAG (Retrieval-Augmented Generation)**
    * Injects authoritative context before generation.
    * Stops the model from guessing schema or inventing columns.
* **Hybrid Retrieval (Vector + BM25)**
    * **Vector** = semantic matching; **BM25** = exact keyword matching.
    * Hybrid improves enterprise reliability for specific column names, IDs, and lifecycle states.
* **Reranking (LLM)**
    * Selects only the best chunks for SQL generation.
    * Results in higher accuracy and lower hallucination risk.
* **SQL Guardrails**
    * Validator blocks dangerous SQL and enforces safe patterns.
    * Mandatory for production usage.
* **Audit Logging**
    * Captures full trace for governance and debugging.
    * Helps explain why a query happened and how to improve it.
* **Evaluation Harness**
    * Measures quality over time with repeatable tests.
    * Turns iteration into an engineering process.

---

## Interview-Ready Narrative (What You Built)

* **Reproducible Environment:** Built a reproducible data environment using Postgres in Docker with a custom CSV loader.
* **API-First Design:** Exposed all functionality through a FastAPI backend with full Swagger documentation.
* **Advanced RAG:** Implemented RAG over schema, business definitions, and specific SQL examples to ensure accuracy.
* **Reliability:** Added hybrid retrieval and caching to increase both reliability and speed.
* **Production Patterns:** Integrated LLM reranking to tighten context, a common pattern in production-grade AI.
* **Security & Governance:** Added strict SQL validation guardrails and audit logging for traceability.
* **Measurable Progress:** Developed an eval harness to quantify improvements over time.
* **User Interface:** Built a minimal Streamlit UI to make the tool immediately usable by analysts and managers.

---


