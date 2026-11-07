# Architecture Decision Records (ADR-lite)

Each record: **context → decision → consequences / trade-offs**. These are the "why" answers for interviews.

---

## ADR-001 Postgres in Docker + synthetic data
- **Context:** Needs to be reproducible on any laptop, realistic, and free of company data.
- **Decision:** Postgres 15 via Docker Compose. Faker-based generator with fixed seeds producing a star schema (3 dims, 6 facts).
- **Consequences:** One command recreates the environment. The data is realistic enough for joins, time series and lifecycle logic, and safe to show publicly.

## ADR-002 Hybrid retrieval with Reciprocal Rank Fusion
- **Context:** Dense embeddings capture meaning ("over-stock" ≈ excess) but are weak on identifiers (`consignment_eligible`). BM25 is the opposite. v1 merged them with a hand-written priority sort that ignored scores.
- **Decision:** Run both and fuse with RRF (k=60).
- **Trade-offs:** Two retrievers to maintain. RRF ignores score magnitude, which is acceptable at this corpus size and avoids score calibration.

## ADR-003 Schema-graph expansion after retrieval
- **Context:** The most common text-to-SQL failure is a missing or incorrect join path.
- **Decision:** After fusion, add the table chunks and FK join-edge chunks connecting every table referenced by the top chunks. These are always kept through reranking.
- **Trade-offs:** Slightly larger prompts. Join hallucinations largely disappear.

## ADR-004 LLM reranker, fail-open
- **Context:** Recall-oriented retrieval returns near-misses that distract the generator.
- **Decision:** Fast-tier LLM selects the best 6 of 12 with structured output. On any error, keep the fused order.
- **Trade-offs:** One extra cheap call (~200-400 ms). It can be disabled (`USE_RERANK=false`) for ablations. A local cross-encoder is on the roadmap.

## ADR-005 Structured outputs everywhere
- **Context:** v1 parsed free-text SQL and hand-parsed reranker JSON (`json.loads(raw)` with a silent fallback).
- **Decision:** Every LLM step has a Pydantic contract passed as the response schema, with a prompt-schema fallback and tolerant parsing.
- **Consequences:** Typed code paths, confidence/assumptions/clarification come for free, and parse failures are explicit errors.

## ADR-006 Defence-in-depth SQL safety
- **Context:** The v1 validator used a regex blocklist and `";" in sql`. That is easy to bypass (comments, data-modifying CTEs, `pg_sleep`).
- **Decision:** Six layers: input guard → router → prompt rules → **sqlglot AST policy** → READ ONLY txn + timeout + row cap → **read-only DB role**.
- **Consequences:** Even a fully compromised prompt cannot write. CI asserts the role rejects `DELETE`. Policy violations are never auto-repaired.

## ADR-007 Self-healing with EXPLAIN pre-flight
- **Context:** Most remaining failures are wrong column names or type errors that Postgres reports precisely.
- **Decision:** `EXPLAIN` before executing. Feed any DB error plus a compact full schema back to the model, max 2 repairs.
- **Trade-offs:** Worst case is 3 generation calls. The bounded budget prevents loops, and repair rate is tracked as a metric.

## ADR-008 Explicit state graph instead of a framework
- **Context:** LangGraph/LangChain add abstraction and dependency churn. Interviewers want to see that you understand the control flow.
- **Decision:** A ~400-line explicit graph: nodes are functions over a dataclass state, and the orchestrator is a generator that yields events.
- **Consequences:** Every transition is unit-testable with a fake LLM. Streaming and tracing come from the same events. Porting to LangGraph would be mechanical if needed.

## ADR-009 Provider abstraction + deterministic fake LLM
- **Context:** Vendor lock-in, free-tier rate limits, and the need for offline tests/CI.
- **Decision:** `LLMProvider` ABC (`generate_structured`, `embed`) with Gemini, OpenAI-compatible (covers OpenAI, Azure, Ollama, vLLM) and a heuristic fake provider.
- **Consequences:** 67 tests run in ~5 s without network. Switching to a local model is a config change.

## ADR-010 Model tiering
- **Decision:** Route, rewrite, rerank and summarize use the fast tier (`flash-lite`). SQL generation and repair use the smart tier (`flash`).
- **Consequences:** Most calls are cheap, and quality is spent where it matters.

## ADR-011 Semantic cache scoped by versions
- **Decision:** Embedding-similarity cache (≥ 0.95), scoped to `kb_version|prompt_version|provider`, TTL 1 h, cleared on KB rebuild. Only successful answers are cached.
- **Trade-offs:** Data freshness is bounded by the TTL. A paraphrase with a different filter could collide, which is why the threshold is high.

## ADR-012 Versioned KB collections + content-hashed ids
- **Decision:** `eando_kb_<KB_VERSION>` collections and `kind:hash` ids.
- **Consequences:** Blue/green re-indexing, reproducible builds, and audit records link answers to an exact KB.

## ADR-013 Prompt versioning in code
- **Decision:** Templates live in `app/pipeline/prompts.py` (reviewed like code). `PROMPT_VERSION` is written to every response and audit record.
- **Consequences:** Regressions can be attributed with "eval EX dropped after sql-v3 → sql-v4".

## ADR-014 Evaluation as a first-class artifact
- **Decision:** A golden set with gold SQL; metrics EX / soft-EX / table recall / guardrail block rate / judge / latency / cost; CI runs the harness and supports a `--min-ex` gate.
- **Consequences:** Changes are judged by numbers. Separating retrieval recall from EX shows *where* to fix things.

## ADR-015 MCP server
- **Context:** Agents (Claude, Cursor, internal agent platforms) are becoming the main consumers of internal tools.
- **Decision:** Expose the copilot as MCP tools in addition to REST.
- **Consequences:** Zero-glue integration with any MCP client. Guardrails are shared because the tools call the same pipeline/validator.

## ADR-016 JSONL audit log + feedback log
- **Decision:** Append-only JSONL for requests and feedback. `/v1/metrics` aggregates them.
- **Trade-offs:** Not queryable at scale. In production, ship it to OTel/Langfuse/warehouse. Thumbs-down + corrected SQL feed new eval cases.

## ADR-017 Hard daily usage budget + embedding cache
- **Context:** this is a portfolio project on a personal API key. Spend must stay near zero even if a bug, retry storm or long eval run goes wrong.
- **Decision:** a small on-disk ledger counts calls and tokens per UTC day. Requests over `LLM_DAILY_CALL_LIMIT` / `LLM_DAILY_TOKEN_LIMIT` raise `BudgetExceededError` *before* any network call. Embeddings are cached by (model, task, text), and the semantic cache runs before routing.
- **Consequences:** worst-case daily spend is bounded by configuration. KB rebuilds and repeat questions are free. Today's usage is visible at `/ready`.

## ADR-018 Model fallback chain
- **Context:** hosted models get retired (`gemini-2.5-flash-lite` returned 404 "no longer available to new users") and overloaded (`gemini-3.5-flash` returned 503 "high demand") while this was being built.
- **Decision:** model names are configuration. On 503/429 the provider tries `GEMINI_FALLBACK_MODELS` in order (stable `*-latest` aliases) before giving up, inside the usual retry/backoff.
- **Trade-offs:** an answer may come from a backup model. The actual model of every call is recorded in `usage.calls[].model`, so this stays visible.
