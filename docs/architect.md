# ARCHITECTURE — E&O Copilot

## What this system does
This project is a production-style Text-to-SQL assistant for supply chain E&O workflows:
- A manager asks a question in natural language.
- The system retrieves schema + business rules + SQL examples (RAG).
- It generates safe Postgres SQL.
- It optionally executes the query and returns results.
- It records traces so we can debug and govern usage.

This is designed to look and behave like an internal “company-grade” analytics copilot.

---

## Runtime components

### 1) PostgreSQL (Docker)
Runs locally using Docker Compose.
Purpose:
- Realistic relational schema for supply chain + E&O
- Supports joins, aggregations, and time series logic

### 2) FastAPI Service
Runs locally via Uvicorn.
Exposes endpoints:
- `/rag/build` — builds knowledge base (Chroma collections)
- `/nlq` — main entrypoint: question → SQL → (optional) execute → response
- `/rag/debug` — exposes retrieval + rerank trace for debugging relevance and accuracy
- `/docs` — Swagger UI

### 3) RAG Knowledge Base (ChromaDB)
Persistent local vector store.
Collections:
- `schema` — generated schema documentation (tables/columns/keys)
- `business` — glossary + business rules (excess, scrap logic)
- `sql_examples` — curated example queries showing join patterns

Why these three?
Text-to-SQL is significantly more accurate when the model sees:
- “what tables exist”
- “how the business defines metrics”
- “how joins should be written”

### 4) Hybrid Retriever
For each question:
- Vector search across collections to get semantic matches
- BM25 keyword ranking for schema docs to catch exact terms and column names
- Merge + prioritize

Outcome:
- A candidate set of context chunks (typically top 12)

### 5) Reranker (Phase 7)
Second-stage selector using Gemini:
- Input: question + 12 candidate chunks
- Output: best 6 chunk IDs to use for SQL generation
Why:
- Retrieval is approximate; reranking improves precision and reduces irrelevant context.

### 6) SQL Prompt Builder
Given selected context chunks:
- Formats them into an authoritative context block
- Adds strict system rules (SELECT-only, no semicolons, use only provided tables)

### 7) SQL Safety Layer (Guardrails)
Before execution:
- Blocks any destructive statements or multiple statements
- Enforces SELECT-only
- Applies a default LIMIT if not present

### 8) Execution Layer
Executes SQL against Postgres only if allowed and if `execute=true`.

### 9) Audit / Trace (Phase 7)
Writes `.logs/audit.jsonl`:
- question
- retrieved IDs
- reranked IDs
- generated SQL
- rowcount
- latency
- prompt version and KB version

Purpose:
- governance
- debugging
- evaluation metrics

---

## Data flow (high level)
1) User → `/nlq`
2) Retriever → candidates (12)
3) Reranker → selected (6)
4) Prompt builder → SQL prompt
5) Gemini SQL model → SQL text
6) Guardrails validate SQL
7) Execute against Postgres
8) Return results
9) Write audit log


## Runtime flow (NLQ)

1. User calls POST `/nlq` with a question
2. Retriever gets top N documents
3. Reranker reduces doc set to the best K documents
4. Prompt builder composes:
   - system rules
   - context docs
   - user question
5. Gemini generates SQL text
6. SQL validator checks safety
7. If safe: run against Postgres and return results
8. Return also:
   - generated SQL
   - citations (doc ids)
   - retrieval preview

---

## What “production-grade” means here
- Traceability: store what context caused what SQL
- Evaluations: measurable accuracy and failure patterns
- Safety: strict SQL validation before execution
- Reproducibility: deterministic KB build + versioning