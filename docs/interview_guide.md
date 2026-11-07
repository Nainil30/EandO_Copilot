# Interview Guide: presenting E&O Copilot

## 1. Pitches

**30 seconds:** "A natural-language analytics copilot for excess and obsolete inventory. It's agentic RAG
text-to-SQL: hybrid retrieval with RRF, LLM reranking, structured generation, AST-level SQL guardrails,
self-healing execution, and an evaluation harness measuring execution accuracy. It's exposed via REST, a chat
UI and an MCP server."

**2 minutes:** walk the diagram in the README top to bottom. Spend the time on three things:
1. **Retrieval for text-to-SQL is different from document QA.** Chunk by table, add a semantic layer, expand along the FK graph.
2. **Safety is layered.** AST validation plus a read-only role, not regex.
3. **It's measured.** EX, retrieval recall and judge scores, gated in CI.

## 2. Demo script (5 minutes)
1. UI: *"Top 10 suppliers by total excess value"*. Show the streamed steps, answer, chart, then the **Sources** tab (dense rank vs BM25 rank vs rerank).
2. Follow-up: *"only the ones in Taiwan"*. Show `standalone_question` in the trace (memory + rewrite).
3. *"What does EOSS mean?"* routes to the glossary with no SQL.
4. *"Delete all inventory rows"*: blocked, 0 LLM calls.
5. Ask the first question again: ⚡ semantic cache hit, fewer calls, lower latency.
6. Observability page: p95, cost, cache hit rate. Knowledge Base page: retrieval debugger.
7. (Optional) Claude Desktop calling `ask_eando` over MCP.

## 3. Likely questions and strong answers

**Why not just put the whole schema in the prompt?**
With 9 tables you could. With 300 tables and thousands of columns you can't, and even below the context limit
irrelevant tables distract the model ("lost in the middle"). Retrieval keeps context small and relevant, and
it's how the system scales. The schema-graph expansion and the compact schema in repair prompts are the
safety nets for when retrieval misses.

**Why hybrid retrieval, and why RRF?**
Embeddings match meaning, BM25 matches exact identifiers like `consignment_eligible`, and text-to-SQL needs
both. Their scores aren't on the same scale, so instead of tuning weights I fuse ranks with RRF: `1/(k+rank)`
summed across retrievers, k=60. It's the standard choice in Elastic/OpenSearch hybrid search.

**How do you stop the model from running destructive SQL?**
Six layers. The important ones are the sqlglot AST policy (which also catches data-modifying CTEs and
`pg_sleep`, things regex misses) and the database role that only has SELECT with
`default_transaction_read_only`. CI proves the role can't `DELETE`. Policy violations are never sent to the
repair loop.

**What is the self-healing loop, and how do you stop it from looping forever?**
EXPLAIN pre-flight, then execute. On a Postgres error I send the failed SQL, the exact error and a compact
schema back to the model. It's bounded by `MAX_REPAIR_ATTEMPTS=2`, and I track repair rate as a metric. If
it's high, that means retrieval or the catalog needs work.

**How do you evaluate it?**
A golden set with gold SQL. Execution accuracy compares result sets, not SQL strings, because many SQL
queries are equivalent. I also measure table recall@context separately, which tells me whether a failure is
retrieval or generation. Plus guardrail block rate, an LLM-as-judge for answer faithfulness, latency
percentiles and cost. CI runs it, and `--min-ex` can gate merges.

**Why build your own graph instead of LangGraph/LangChain?**
I wanted every transition visible and unit-testable. The orchestrator is a generator over a state dataclass,
which gives streaming and tracing for free. It maps one-to-one onto a LangGraph `StateGraph` if a team
standardizes on that.

**How do you handle hallucination in the final answer?**
The summarizer only sees the returned rows and is told to use only those numbers. The chart is validated
against actual columns. The judge eval measures faithfulness. Citations show which chunks grounded the SQL.

**What about cost and latency?**
Model tiering (only SQL generation and repair use the bigger model), the semantic cache, the BM25 index kept
in memory, and per-request token and cost accounting. Per-step latency is in the trace, so I know what to
optimize.

**How would you take this to production at a company?**
pgvector or a managed vector DB, Redis for memory/cache/rate limits, OIDC with row-level security per
business unit, traces to OpenTelemetry/Langfuse, a feedback-to-eval promotion workflow, and canary-testing
prompt versions against the golden set.

**What failed / what did you learn?** These all actually happened while building this:
1. **Evals found a data bug, not a model bug.** The first real eval run flagged "LTB orders, most recent 20"
   as wrong. The SQL was correct, but every LTB order had the same date: in Faker, `"-12m"` means 12
   *minutes*, not months, so all "random" dates collapsed onto today. *Lesson: when a metric fails, check the
   data and the gold answer before blaming the model.*
2. **Ambiguous gold answers.** Two gold queries had no LIMIT or a different sort order than the question
   implied, so a correct model answer "failed". I rewrote them to be deterministic. *Lesson: eval datasets
   need review too. Execution accuracy is only as good as the gold set.*
3. **Model retirement in production.** `gemini-2.5-flash-lite` started returning 404 "no longer available to
   new users". That's why model names are configuration, not code.
4. **Overload.** `gemini-3.5-flash` returned 503 "high demand", so I added a **model fallback chain**. The
   trace shows which model actually answered.
5. **Network blips.** A DNS failure mid-eval failed 6 cases because connection errors weren't classified as
   retryable. Now they are, and embedding calls get the same retry wrapper as generation calls.
6. **From v1 → v2:** questions were embedded with the *document* task type (silently hurting recall), the
   regex SQL validator was bypassable, the reranker parsed JSON by hand and failed silently, and the eval set
   referenced platforms that didn't exist ("PowerEdge").

## 4. Numbers to have ready (measured 2026-11-07, see [eval_results.md](eval_results.md))

| Metric | Value |
|---|---|
| Golden cases passing | **37 / 37**, all 13 categories |
| Soft execution accuracy | **100%** (26 gold-SQL cases); strict 58% because of helpful extra columns |
| Table recall@context | **100%**, so retrieval was never the bottleneck |
| Guardrail block rate | **6 / 6** adversarial prompts |
| Repair rate | 0% on the golden set (the loop is unit-tested with injected DB errors) |
| Latency p50 | ~5 s on the free tier |
| Cost per question | ~4,400 tokens → about $0.002 at list price; $0 on the free tier |
| Cache hit | **12 ms, 0 LLM calls** (vs ~10-16 s for a full run) |
| Post-rerank graph expansion | **13–17% fewer tokens** at the same accuracy |
| Genuine model error found and fixed | Few-shot over-copying (E23) → prompt rule, `sql-v3` → `sql-v4` |

## 5. Concept cheat sheet

The full version, with *why* and *alternatives* for each of 44 concepts, is in [concepts.md](concepts.md).


- **RAG:** retrieve relevant context, then generate conditioned on it, to ground the model in your data.
- **Dense vs sparse retrieval:** embeddings (semantic) vs BM25 (lexical, exact terms).
- **RRF:** rank-based fusion, `Σ 1/(k + rank)`.
- **Reranking:** a second, more expensive relevance pass over a small candidate set (cross-encoder or LLM).
- **Asymmetric embeddings:** different task types for queries vs documents.
- **Matryoshka embeddings:** vectors that can be truncated (3072 → 768) with small quality loss.
- **Structured outputs:** constrained decoding to a JSON schema.
- **Agentic workflow:** an LLM-driven control loop with tools, state and feedback (here: execute → error → repair).
- **Semantic cache:** cache keyed by meaning (embedding similarity), not exact string.
- **LLM-as-judge:** a model grading outputs against a rubric. Cheap, but validate it against human labels.
- **Execution accuracy:** text-to-SQL metric comparing result sets (Spider, BIRD benchmarks).
- **MCP:** Model Context Protocol, an open standard for exposing tools and resources to LLM agents.
- **Prompt injection:** untrusted input that tries to override instructions. Mitigate with delimiting, system prompts, output validation and least privilege.
