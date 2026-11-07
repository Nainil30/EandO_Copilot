# Concepts Guide: what I used, why, and what I considered instead

Each entry covers **what it is** (plain English), **why this project needs it**, **alternatives I
considered**, and **where it is in the code**. Read it top to bottom once and you can explain every design
choice in an interview.

---

## Part A. The problem framing

### 1. Text-to-SQL
- **What:** turn a natural-language question into a SQL query and run it.
- **Why:** E&O data is structured (tables, numbers, joins). The answer is a calculation, not a paragraph
  in a document. Generating SQL gives exact numbers and an auditable query.
- **Alternatives:** (a) classic document RAG over exported reports: can't do aggregations, goes stale;
  (b) a fixed BI dashboard: can't answer new questions; (c) letting the LLM "read" the whole table: too big,
  and LLMs are bad at arithmetic.
- **Where:** `app/pipeline/graph.py` (`node_generate_sql`).

### 2. RAG (Retrieval-Augmented Generation)
- **What:** before the model generates, retrieve relevant knowledge and put it in the prompt so the
  answer is grounded in your data, not the model's memory.
- **Why here:** the model has never seen this warehouse. It needs the schema (what exists), business
  definitions (how "excess value" is calculated) and worked examples (how joins are written).
  RAG supplies exactly the slice needed for each question.
- **Alternatives:** paste the full schema into every prompt. That works for 9 tables but not for 300, and even
  when it fits, irrelevant tables distract the model ("lost in the middle"). Fine-tuning is costly, goes stale
  whenever the schema changes, and doesn't cite sources.
- **Where:** `app/rag/`.

### 3. Semantic layer / data dictionary
- **What:** a human-written description of each table and column: meaning, units, valid values, joins.
- **Why:** `lifecycle_state varchar` tells the model nothing. "Product lifecycle stage; values 'Active',
  'EOL', 'EOSS', 'Discontinued'" lets it write `WHERE lifecycle_state = 'EOL'` correctly. This is the
  single highest-impact accuracy lever in text-to-SQL.
- **Alternatives:** raw `information_schema` introspection (names and types only), or dbt/LookML semantic
  models (great in a real company, overkill here).
- **Where:** `app/sql/catalog.py`.

---

## Part B. Building the knowledge base

### 4. Chunking strategy
- **What:** how source material is split into retrievable pieces.
- **Why these chunk types:**
  - one chunk per **table**, so all of a table's columns stay together (splitting causes "column doesn't exist" errors)
  - one chunk per **join edge**, small and keyword-dense so BM25 finds "join X with Y"
  - **glossary** split by heading, one business definition per chunk
  - **SQL examples** as question → SQL pairs (see dynamic few-shot)
- **Alternatives:** fixed-size token windows (e.g. 512 tokens with overlap). That's the default for documents,
  but it cuts tables in half and mixes unrelated definitions.
- **Where:** `app/rag/kb_builder.py`.

### 5. Embeddings
- **What:** a model turns text into a vector (here 768 numbers). Texts with similar meaning have vectors
  that are close together (cosine similarity).
- **Why:** lets "over-stock" match the "Excess inventory" chunk even with no shared words.
- **Where:** `app/llm/gemini.py` (`_embed`), stored in ChromaDB.

### 6. Asymmetric embeddings (query vs document task types)
- **What:** Gemini embeds a short *question* differently from a long *document* (`RETRIEVAL_QUERY` vs
  `RETRIEVAL_DOCUMENT`), so questions land near the documents that answer them.
- **Why:** v1 embedded questions as documents, which silently lowers recall. It's a one-line fix with a measurable gain.

### 7. Matryoshka embeddings (dimension truncation)
- **What:** some embedding models pack the most important information into the first dimensions, so you
  can keep just the first 768 of 3072 with little quality loss.
- **Why:** 4× smaller vectors means less storage and faster search, which is plenty for 54 chunks. Vectors are
  re-normalised after truncation so cosine similarity stays valid.

### 8. Vector database (ChromaDB)
- **What:** stores vectors and finds the nearest ones quickly (HNSW index).
- **Why Chroma:** embedded, no server to run, persistent on disk, free. Ideal for a laptop project.
- **Alternatives:** pgvector (one database for data and vectors; on the roadmap), Qdrant, Pinecone, Weaviate
  (managed/scale-out, unnecessary here).
- **Where:** `app/rag/vector_store.py`.

### 9. Versioned index + content-hashed ids
- **What:** collections are named `eando_kb_<version>`, and each chunk id is a hash of its content.
- **Why:** rebuilding is idempotent, you can build v3 while v2 serves (blue/green), and every answer in the
  audit log records which KB version produced it.

---

## Part C. Retrieval

### 10. Sparse retrieval (BM25)
- **What:** classic keyword search that scores documents by term frequency, weighted by how rare the term is.
- **Why:** embeddings are weak at exact identifiers like `consignment_eligible` or `fact_ltb_orders`. BM25
  matches them exactly. The tokenizer also splits `snake_case`, so "excess" matches `calculated_excess`.
- **Where:** `app/rag/bm25.py` (index kept in memory, rebuilt only when the KB changes).

### 11. Hybrid search
- **What:** run dense (embedding) and sparse (BM25) retrieval and combine them.
- **Why:** they fail on different questions, so combined recall is higher than either alone.

### 12. Reciprocal Rank Fusion (RRF)
- **What:** merge ranked lists with `score(doc) = Σ 1 / (k + rank)`, k = 60.
- **Why:** cosine similarity (0 to 1) and BM25 scores (unbounded) aren't comparable, so adding them needs
  fragile tuning. RRF only uses ranks, has one parameter, and is the default in Elasticsearch, OpenSearch and
  Weaviate hybrid search. A document ranked well by *both* retrievers rises to the top.
- **Where:** `rrf_fuse()` in `app/rag/retriever.py`.

### 13. Schema-graph expansion
- **What:** after retrieval, look at which tables the top chunks mention and deterministically add their table
  definitions and the foreign-key join edges between them.
- **Why:** the most common text-to-SQL failure is a wrong or missing join. The FK graph is known for certain,
  so there's no reason to hope the retriever finds it. This is a cheap, graph-RAG-style trick.
- **Where:** `expand_with_schema_graph()`.

### 14. Reranking
- **What:** a second, more careful relevance pass over the candidates (12 → 6).
- **Why:** retrieval is tuned for recall (don't miss anything). Reranking is tuned for precision (don't
  distract the model). Fewer irrelevant tables means fewer hallucinated joins.
- **Why an LLM reranker:** no extra model to host. A cheap "lite" model does it in one call.
- **Alternatives:** cross-encoder rerankers (bge-reranker, Cohere Rerank) are faster and cheaper at scale;
  that's on the roadmap.
- **Fail-open:** if the reranker errors, keep the fused order. Optional components must never break the main path.
- **Where:** `app/rag/reranker.py`.

### 15. Dynamic few-shot prompting
- **What:** instead of fixed examples in the prompt, retrieve the approved question → SQL examples most similar
  to the current question.
- **Why:** the model copies the right join/aggregation pattern for *this* question. Fixing a recurring failure is
  as simple as adding one example to `docs/sql_examples.md`.

### 16. Context ordering and budgeting
- **What:** put chunks in a deliberate order (schema → joins → definitions → examples) and cap the total size.
- **Why:** models attend best to the start and end of the context ("lost in the middle"). The examples sit right
  before the question, and the cap keeps cost and latency predictable.
- **Where:** `app/rag/context.py`.

---

## Part D. Generation

### 17. Prompt engineering conventions
- **System prompt** holds stable rules. The **user turn** holds variable data.
- **XML-style tags** (`<context>`, `<question>`, `<doc id=…>`) clearly separate instructions from data and let
  the model cite chunk ids.
- User text is **declared untrusted**, which helps against prompt injection.
- The model gets an **allowed way out** (`needs_clarification`, "say you don't know") so it doesn't invent answers.
- **Versioned:** `PROMPT_VERSION` is logged with every answer. Where: `app/pipeline/prompts.py`.

### 18. Structured outputs
- **What:** the model must answer with JSON matching a schema (built from Pydantic models). Gemini enforces this
  during decoding (`response_schema`).
- **Why:** no fragile text parsing, typed code, and useful extra fields for free: `plan`, `assumptions`,
  `confidence`, `needs_clarification`. v1 parsed reranker JSON by hand and failed silently.
- **Fallback:** if a model rejects the schema, use JSON mode with the schema in the prompt, plus tolerant parsing.
- **Where:** `app/pipeline/outputs.py`, `app/llm/base.py`.

### 19. Plan-then-answer (lightweight chain of thought)
- **What:** the model writes a 2-4 step plan (tables → joins → filters → aggregation) before the SQL, in the same call.
- **Why:** reasoning first improves multi-join SQL, and the plan doubles as a human-readable explanation in the UI.

### 20. Model tiering
- **What:** use a cheap "lite" model for easy steps (routing, rewriting, reranking, summarising) and a stronger
  model only for SQL generation and repair.
- **Why:** most calls become cheap and fast. Quality is spent where it matters.

---

## Part E. The agentic layer

### 21. Agentic workflow
- **What:** the LLM sits inside a control loop that acts (runs SQL), observes the result (rows or an error)
  and decides the next step, instead of answering in one shot.
- **Why:** text-to-SQL failures are usually fixable with feedback from the database. The loop turns
  "wrong column name" from a failure into a retry.

### 22. Explicit state graph (LangGraph-style)
- **What:** each step is a small function (a "node") that reads and writes a shared `PipelineState`. An
  orchestrator decides the next node.
- **Why my own instead of LangGraph/LangChain:** every transition is visible and unit-testable with a fake LLM,
  there's no framework churn, and the orchestrator is a Python generator, so **streaming** and **tracing** come
  from the same events. It maps 1:1 onto a LangGraph `StateGraph`.
- **Where:** `app/pipeline/graph.py`.

### 23. Intent routing
- **What:** classify each message as data question / definition / out of scope / unsafe.
- **Why:** definitions skip SQL entirely (cheaper, faster), off-topic questions are refused politely, and unsafe
  requests are blocked even when phrased subtly.

### 24. Self-healing SQL (reflection / self-correction)
- **What:** `EXPLAIN` the query first (validates it without running it). If Postgres reports an error, send the
  failed SQL plus the exact error plus a compact full schema back to the model to fix. At most 2 repairs.
- **Why:** most errors (unknown column, type mismatch) are precisely described by Postgres, so the model fixes
  them reliably. The bound prevents infinite loops. **Security violations are never repaired;** they end the request.

### 25. Conversation memory + query rewriting
- **What:** remember the last few turns per session. Rewrite a follow-up ("only Taiwan") into a standalone
  question ("top 5 suppliers by excess value located in Taiwan") before retrieval.
- **Why:** retrieval and caching need complete questions. Rewriting is the standard fix for multi-turn RAG.
- **Where:** `app/pipeline/memory.py`, `node_contextualize`.

### 26. Grounded answer synthesis
- **What:** the final summary sees only the returned rows and must use only those numbers. The chart choice is
  checked against real column names.
- **Why:** it prevents the last step from hallucinating numbers. The LLM-as-judge eval measures this.

---

## Part F. Safety

### 27. Defence in depth
Six independent layers. Each can fail without data being modified:
input guard → LLM router → prompt rules → **AST validator** → **read-only transaction + timeout + row cap** →
**read-only database role**.

### 28. Prompt injection
- **What:** user text that tries to override the system's instructions ("ignore previous instructions…").
- **Mitigations here:** pattern checks before any LLM call, delimited untrusted input, system-prompt rules, and,
  most importantly, **least privilege**: even a fully hijacked model can only run read-only SELECTs.

### 29. AST-based SQL validation (sqlglot)
- **What:** parse the SQL into a syntax tree and check policies on the tree: one statement, read-only query
  type, no INSERT/UPDATE/DELETE/DDL anywhere (including inside CTEs), table allow-list, no system catalogs, no
  dangerous functions (`pg_sleep`, `pg_read_file`, `dblink`), and add/clamp LIMIT.
- **Why not regex:** regex is fooled by comments, casing and data-modifying CTEs (`WITH d AS (DELETE …) SELECT …`).
- **Where:** `app/sql/validator.py`.

### 30. Least privilege at the database
- **What:** generated SQL runs as `copilot_readonly`: SELECT grants only, `default_transaction_read_only = on`,
  and a statement timeout.
- **Why:** the final guarantee, enforced by Postgres itself. CI proves a `DELETE` fails.

---

## Part G. LLMOps: cost, reliability, observability

### 31. Hard usage budget
- **What:** a daily cap on API calls and tokens (`LLM_DAILY_CALL_LIMIT`, `LLM_DAILY_TOKEN_LIMIT`), checked
  *before* each request.
- **Why:** a bug, retry storm or long eval run can never run up a bill. Requests over the cap are refused
  locally and never sent.
- **Where:** `app/llm/budget.py`.

### 32. Caching (three kinds)
- **Semantic cache:** if a new question's embedding is ≥ 0.95 similar to a previously answered one, return the
  stored answer. It runs before routing, so a repeat costs **zero** LLM calls. Scoped to KB and prompt version.
- **Embedding cache:** embeddings are deterministic, so they are cached on disk. Rebuilding the KB costs 0 calls.
- **In-memory BM25 index** and singleton clients (no per-request rebuilding).

### 33. Retries, backoff and model fallback
- **Retries with exponential backoff + jitter** for 429/503 errors, so brief overloads don't fail requests.
- **Model fallback chain:** if the primary model is overloaded, try a backup model (`GEMINI_FALLBACK_MODELS`).
  This was observed in practice: `gemini-3.5-flash` returned 503 "high demand" and the request completed on
  the fallback.

### 34. Provider abstraction
- **What:** the app talks to an `LLMProvider` interface. Gemini, any OpenAI-compatible API (OpenAI, Azure,
  Ollama, vLLM) and an offline fake model all implement it.
- **Why:** no vendor lock-in, local models are a config change, and tests run offline.

### 35. Observability
- **Per answer:** a step-by-step trace with latency, token usage, estimated cost, versions, retrieval ranks and repair attempts.
- **Audit log** (JSONL): enough to replay any answer. **Metrics endpoint:** success rate, p50/p95, cache hit
  rate, repair rate, tokens, cost, feedback. **Structured logs** with request ids.
- **Why:** you can't improve or govern what you can't see. This is what LLMOps tools like Langfuse and
  LangSmith provide; here it's built in.

### 36. Feedback loop
- 👍/👎 plus optional corrected SQL is logged. A thumbs-down becomes a candidate golden eval case or a new
  SQL example.

---

## Part H. Evaluation

### 37. Golden dataset
- 37 hand-written cases across 13 categories, including adversarial (governance) and definition questions.
  Most data questions have a hand-written correct SQL ("gold SQL").

### 38. Execution accuracy (EX)
- **What:** run both the generated SQL and the gold SQL and compare the *result rows* (ignoring order).
  **Soft EX** also accepts extra columns or different aliases.
- **Why:** many different SQL strings are equally correct, so comparing results is what matters. It's the
  standard metric of the Spider and BIRD benchmarks.

### 39. Retrieval recall (table recall@context)
- **What:** did the context sent to the generator include every table the question needs?
- **Why:** it separates *retrieval* failures from *generation* failures, so you know which part to fix.

### 40. LLM-as-judge
- **What:** a model grades whether the written answer is supported by the returned rows (faithfulness, 1-5).
- **Caveat:** judges have biases. In a real team, calibrate against a sample of human labels.

### 41. Offline tests with a fake LLM
- A deterministic fake provider and a fake warehouse let 74 tests check routing, repair loops, caching, memory,
  streaming, budgets and the API in seconds, with no keys and no cost. CI also runs a real Postgres integration job.

---

## Part I. Interfaces

### 42. Server-Sent Events (SSE) streaming
- The API streams each pipeline step as it finishes, so the UI shows live progress ("Hybrid retrieval… LLM
  reranking…") instead of a spinner.

### 43. MCP (Model Context Protocol)
- **What:** an open standard (started by Anthropic) for exposing tools to AI agents and assistants.
- **Why:** with one small file, Claude Desktop, Claude Code, Cursor or any agent can call `ask_eando`,
  `run_readonly_sql`, `describe_schema` and `search_knowledge_base`, reusing all the same guardrails. Agents are
  increasingly the main consumers of internal tools.
- **Where:** `app/mcp_server.py`.

### 44. Production API hygiene
- Versioned routes (`/v1`), typed request/response models (OpenAPI docs for free), API-key auth, per-client rate
  limiting, request ids, liveness/readiness probes, a non-root Docker image, and a CI pipeline.
