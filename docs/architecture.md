# Architecture: E&O Copilot v2

## 1. Goals and non-goals

**Goals**
- Answer analytics questions about E&O inventory correctly, with SQL a human can audit.
- Never modify data, whatever the input.
- Be measurable: every answer is traceable to its context, prompt version, model and cost.
- Stay model-agnostic and runnable on a laptop (free-tier Gemini, or fully offline).

**Non-goals**
- General chat. Out-of-scope questions are politely refused.
- Writing to the warehouse. The system is read-only by construction.

## 2. Layered view

```
┌──────────────────────────── Clients ─────────────────────────────┐
│ Streamlit chat (SSE) │ REST clients │ MCP clients (Claude, Cursor)│
└──────────────┬───────────────┬──────────────────┬────────────────┘
               ▼               ▼                  ▼
┌──────────────────── Interface layer ─────────────────────────────┐
│ FastAPI /v1 (auth, rate limit, request-id, typed schemas)        │
│ MCP server (ask_eando, run_readonly_sql, describe_schema, ...)   │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────── Orchestration layer ─────────────────────────┐
│ pipeline/graph.py: PipelineState + nodes + streaming orchestrator │
│ memory.py, prompts.py, outputs.py                                │
└───────┬───────────────┬────────────────┬──────────────┬──────────┘
        ▼               ▼                ▼              ▼
┌─ Retrieval ──┐ ┌─ Generation ──┐ ┌─ SQL safety ──┐ ┌─ Observability ─┐
│ retriever    │ │ llm/base      │ │ validator     │ │ audit (JSONL)   │
│ bm25         │ │ gemini        │ │ executor      │ │ metrics         │
│ vector_store │ │ openai_compat │ │ catalog       │ │ json logging    │
│ reranker     │ │ fake          │ │               │ │ usage tracker   │
│ context      │ └───────────────┘ └───────────────┘ └─────────────────┘
│ sem. cache   │
└──────────────┘
        ▼                                  ▼
   ChromaDB + BM25                 PostgreSQL (read-only role)
```

## 3. Knowledge base design

| Chunk kind | Count | Source | Rationale |
|---|---|---|---|
| `table` | 9 | `sql/catalog.py` | One chunk per table: DDL-like columns, descriptions, valid categorical values, its join conditions. Splitting a table across chunks causes missing-column errors. |
| `join` | 12 | FK edges | Small, keyword-dense chunks. BM25 hits them for "join X with Y" questions. |
| `glossary` | 12 | `docs/business_glossary.md` (`##` sections) | Metric definitions (excess value, scrap split, lifecycle). |
| `sql_example` | 21 | `docs/sql_examples.md` | Question→SQL pairs used as dynamic few-shot examples. |

- **Ids** are `kind:sha256(kind|title|text)[:12]`, so the content is addressed by its hash and rebuilds are idempotent.
- **Collections** are versioned (`eando_kb_<KB_VERSION>`). You can build `v3` while `v2` serves, then flip the env var (blue/green).
- **Live value enrichment:** when the DB is reachable, distinct `platform_name` values are appended to the table chunk (value linking for "R750").

## 4. Retrieval funnel

```
question ─► embed(RETRIEVAL_QUERY) ─► dense top-12 ─┐
         └► BM25(question + rewrite keywords) top-12 ┴► RRF (k=60) ─► top-12
             ─► FK-graph expansion (tables + join edges for referenced tables)
             ─► LLM rerank (fast model) keep 6  + always keep graph-expanded schema
             ─► order: table → join → glossary → sql_example, 9K char budget
```

**Why RRF:** cosine similarity (0..1) and BM25 (unbounded) are not comparable, so a weighted sum needs
per-corpus tuning. RRF uses ranks only: `Σ 1/(k + rank)`. It is robust and has one parameter.

**Why graph expansion:** with retrieval alone, the model can get `fact_excess_calculation` and `dim_supplier`
but miss the join condition, or get the example SQL but not the table definition. The FK graph is known, so
we add those chunks deterministically.

**Reranker fail-open:** if the reranker throws or returns unknown ids, fall back to the fused order. Optional
components must not break the main path.

## 5. Generation contract

`SQLGeneration = {plan, sql, tables_used, assumptions, confidence, needs_clarification, clarification_question}`

- **plan** gives cheap chain-of-thought that is also shown to the user.
- **assumptions** make ambiguity visible (e.g. "interpreted 'recent' as last 90 days").
- **confidence** can drive UI warnings or human-in-the-loop routing.
- **needs_clarification** gives the model a sanctioned alternative to hallucinating a column.

Gemini receives the Pydantic model as `response_schema` (native constrained decoding). If the SDK/model rejects
the schema, the provider falls back to JSON mode with the schema embedded in the prompt, and `parse_json_model`
tolerates code fences and surrounding text.

## 6. Self-healing execution

```
for attempt in 0..MAX_REPAIR_ATTEMPTS:
    validate (AST)            ── policy violation → BLOCK (never repaired)
                              ── parse/unknown table → repair
    EXPLAIN (pre-flight)      ── catches unknown columns/type errors without running the query
    execute (READ ONLY, timeout, row cap)
    on DB error → repair prompt = context + compact schema + failed SQL + DB error
```

The compact schema (every table with its columns) is always included in repair prompts, so the model can
find the right column even if retrieval missed that table.

## 7. State graph and streaming

`run_pipeline_iter()` is a generator. After each node it yields `{"type": "step", "name", "ms", "data"}` and
ends with `{"type": "final", "response"}`. The SSE endpoint just serializes these events. The UI renders
them live, and the same events form the response `trace`.

The usage tracker is bound to a `ContextVar` and re-bound at every step, because a streaming generator can
resume on a different worker thread.

## 8. Caching

| Cache | Key | Invalidation |
|---|---|---|
| Semantic cache (checked *before* routing → repeats cost 0 LLM calls) | question embedding, cosine ≥ 0.95 | TTL (1 h) + scope `kb_version|prompt_version|provider`; cleared on KB build |
| Embedding cache | sha256(model, task, text) on disk | 30-day TTL; content-addressed, never stale |
| BM25 index | process memory | rebuilt lazily after `invalidate_bm25()` (KB build) |
| Chroma client, LLM client, SQLAlchemy engines | singletons | process lifetime |

Only successful executed answers are cached, never errors or refusals.

## 9. Scaling notes

| Concern | Current | Production path |
|---|---|---|
| Memory / cache / rate limit | in-process | Redis |
| Vector store | embedded Chroma | pgvector, or managed Chroma/Qdrant |
| Sync pipeline in threadpool | fine for tens of RPS | async providers + async SQLAlchemy |
| Audit JSONL | local file | ship to OpenTelemetry / Langfuse / a warehouse table |
| Auth | static API keys | OIDC + per-user row-level security in Postgres |

## 10. Cost and reliability controls

- **Daily budget** (`app/llm/budget.py`): calls and tokens per UTC day are checked before every request.
- **Model fallback** (`GeminiProvider._generate_text`): primary → `GEMINI_FALLBACK_MODELS` on 503/429.
- **Retries** with exponential backoff and jitter (`with_retries`).
- **Fail-open optional steps:** reranker and summarizer errors degrade gracefully. They never fail the request.
