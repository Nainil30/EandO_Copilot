# Rebuild Guide: build E&O Copilot from zero

This guide walks through rebuilding the project in the same order a real team would: **baseline first, then
measure, then add complexity only where the numbers say it helps.** Each phase lists what to build, the key
idea, the files to compare against, and how to verify it.

> Tip: do each phase on its own git branch and record your eval numbers before/after. That table of numbers
> is the best interview artifact you can have.

---

## Phase 0: Environment (30 min)
- Python 3.11 venv, Docker Desktop, a Gemini API key.
- `requirements-dev.txt`, `.env.example` → `.env`, `app/config.py` with pydantic-settings.
- **Key idea:** all configuration in one typed place, so features can be flagged on and off.
- **Verify:** `python -c "from app.config import get_settings; print(get_settings())"`

## Phase 1: Warehouse (1-2 h)
- `db/schema.sql`: 3 dimensions (`dim_part`, `dim_supplier`, `dim_platform`) and 6 facts.
- `scripts/generate_data.py` (seeded Faker), `scripts/load_to_postgres.py`.
- `docker compose up -d postgres` mounts `schema.sql` into `docker-entrypoint-initdb.d`.
- **Verify:** `docker exec -it eando_postgres psql -U copilot_user -d eando_copilot -c "\dt"` shows 9 tables.

## Phase 2: Naive baseline (1 h)
- Put the full schema in a prompt, ask Gemini for SQL, execute it.
- Write ~10 questions and note failures: wrong joins, invented columns, no LIMIT, and the model will happily
  write `DELETE` if asked.
- **Key idea:** you need a baseline before you can show improvement.

## Phase 3: Guardrails (2 h) → `app/sql/validator.py`, `executor.py`, `app/guardrails/`
- Parse SQL with **sqlglot**. Require a single `exp.Query`, walk the tree for DML/DDL nodes, apply the table
  allow-list, function deny-list and LIMIT injection.
- Execute inside `SET TRANSACTION READ ONLY` + `SET LOCAL statement_timeout`, with `fetchmany` as a row cap.
- Create the `copilot_readonly` role (bottom of `schema.sql`).
- **Verify:** `pytest tests/test_sql_validator.py tests/test_guardrails.py`

## Phase 4: Semantic layer + RAG v1 (2-3 h) → `app/sql/catalog.py`, `app/rag/kb_builder.py`, `vector_store.py`
- Write a data dictionary: column meanings, units, valid values, joins.
- Chunk: one per table, one per join edge, glossary by `##`, SQL examples as question→SQL.
- Embed with `task_type=RETRIEVAL_DOCUMENT` and store in a Chroma collection with cosine distance.
- Retrieve top-k with `RETRIEVAL_QUERY` embeddings and put them in the prompt.
- **Verify:** `python -m scripts.bootstrap --kb-only` then `POST /v1/retrieve/debug`.

## Phase 5: Hybrid retrieval (2 h) → `app/rag/bm25.py`, `retriever.py`
- BM25 over the same chunks (tokenizer splits snake_case).
- **RRF:** `score = Σ 1/(60 + rank)` across both lists.
- **FK-graph expansion:** for tables referenced by top chunks, add their table chunks and the join edges between them.
- **Verify:** `pytest tests/test_retrieval.py`, then compare table recall@context in evals before and after.

## Phase 6: Reranking + structured outputs (2 h) → `app/rag/reranker.py`, `app/pipeline/outputs.py`
- Pydantic models for every LLM step. Pass them as `response_schema`.
- Reranker: fast model picks the best N ids. Fall back to fused order on any failure.
- **Verify:** `pytest tests/test_pipeline.py::test_structured_output_parser_tolerates_fences`

## Phase 7: Agentic pipeline (3-4 h) → `app/pipeline/graph.py`, `prompts.py`
- `PipelineState` dataclass and node functions: guard → contextualize → route → cache → retrieve → rerank →
  generate → validate/execute (repair loop) → summarize.
- Orchestrator as a **generator** that yields events (this gives you streaming and tracing).
- Self-repair: `EXPLAIN` pre-flight, then DB error + compact schema → repair prompt, bounded attempts.
- **Verify:** `pytest tests/test_pipeline.py`. Read `test_self_healing_repair_loop` to see how a fake
  warehouse injects a DB error.

## Phase 8: Memory, cache, streaming (2 h) → `memory.py`, `semantic_cache.py`, `/v1/ask/stream`
- Session memory (window 4, TTL), with follow-ups rewritten to standalone questions.
- Semantic cache keyed by embedding similarity and scoped by versions.
- SSE: `event: step\ndata: {...}\n\n`.
- **Verify:** ask "Top 10 suppliers by excess value", then "only Taiwan" with the same `session_id`.

## Phase 9: Evaluation (2-3 h) → `evals/`
- Golden set with `gold_sql`, expected tables, adversarial cases.
- Metrics: EX, soft EX, table recall@context, block rate, repair rate, judge, p50/p95, cost.
- **Iterate:** for each failure, ask whether it is a retrieval problem (recall < 1) or a generation problem.
  Fix it by adding a glossary entry, a SQL example, a catalog description or a prompt rule, then re-run.

## Phase 10: Productionize (3 h)
- FastAPI v1 routes, API keys, rate limiting, request IDs, `/health` and `/ready`.
- Audit JSONL, `/v1/metrics`, Streamlit observability page.
- Dockerfile (non-root, healthcheck), Compose stack, GitHub Actions (lint → tests → Postgres integration → eval → docker build).
- MCP server (`app/mcp_server.py`), then connect it to Claude Desktop.

---

## Experiments worth running (and quoting in interviews)

| Experiment | How | Expected learning |
|---|---|---|
| Dense only vs hybrid | temporarily return only `dense` from `retrieve()` | BM25 lifts recall on identifier-heavy questions |
| Rerank on/off | `USE_RERANK=false` | precision vs +1 call latency |
| No graph expansion | skip `expand_with_schema_graph` | join errors rise on multi-table questions |
| `CONTEXT_TOP_N` 3 / 6 / 10 | env var | the context-size vs distraction curve |
| Repair attempts 0 / 2 | `MAX_REPAIR_ATTEMPTS` | how much EX self-healing buys |
| flash vs flash-lite for SQL | `GEMINI_SQL_MODEL` | cost/quality trade-off |
| Local model | `LLM_PROVIDER=openai`, `OPENAI_BASE_URL=http://localhost:11434/v1` | open-weights viability |
