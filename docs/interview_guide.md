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

## 6. Study links for a data engineering interview

- SQL on this exact schema, 12 questions with answers: [sql_interview_qa.md](sql_interview_qa.md)
- Kubernetes manifests and design decisions: [../k8s/README.md](../k8s/README.md)
- Datadog metrics module: [../app/observability/datadog_metrics.py](../app/observability/datadog_metrics.py)
- 7-step demo script and DE Q&A: README section 14

## 7. Data Engineering role framing

Text in **[brackets]** is a placeholder: replace it with your own real numbers and systems before the
interview. A specific, true number beats an impressive vague one.

### 7.1 Opening: the 90-second intro

Order matters: **domain → technical depth → proof**. Lead with what most DE candidates don't have.

> "I've spent [N years] in supply chain at Dell, most recently on [excess & obsolete inventory and last-time-buy
> planning]. That work is data work: every E&O decision comes from joining inventory, BOMs, forecasts and
> supplier data that live in different systems, and when the data is wrong, the company either scraps parts
> it needed or keeps parts it will never use.
>
> Over time I became the person who built the data side: [pulling ERP and planning data together with SQL and
> Python, automating the weekly E&O and LTB reports, building the checks that caught bad forecast or lifecycle
> data before it reached a decision]. That's what pulled me toward data engineering.
>
> To prove the engineering depth, I built E&O Copilot: a star-schema Postgres warehouse with a least-privilege
> read-only role, a tested and instrumented pipeline on top of it, an evaluation harness gated in CI, Datadog
> metrics, and Kubernetes manifests. I'd love to show you a few minutes of it, and I'm happy to go as deep as
> you want on any part."

Then stop and let them choose. Don't launch into the demo uninvited.

### 7.2 "You haven't used Datadog / DataRobot"

Formula: **honest → transferable → already started → plan**. Never bluff a tool.

> "Honestly, I haven't used either one in production. What I do have are the concepts underneath them. For
> Datadog: I already instrument this project with per-request latency, token cost, cache hit rate and repair
> rate, and I wrote a DogStatsD metrics module with the seven metrics and the alert thresholds I'd set, so
> counters, histograms, tags, p95 and monitors are not new ideas to me. For DataRobot: the problems it solves,
> such as deploying a model behind a stable interface, versioning, evaluating against a golden set and monitoring
> accuracy and drift, are exactly what I built by hand here. My plan for the first weeks is the Datadog Learning
> Center and DataRobot University fundamentals, then rebuilding something real, like this project's dashboards
> and a deployment of its SQL model, so I learn the tools on the team's actual problems. I picked up sqlglot,
> ChromaDB and Kubernetes for this project the same way."

Why it works: it admits the gap in the first sentence, then spends the rest on evidence that the gap closes fast.

### 7.3 Three behavioral questions (STAR answers)

Keep each answer to about 2 minutes. Spend most of the time on **Action**, and end on a number.

**1. "Tell me about a time you automated a manual data process."**
- **Situation:** Last-time-buy decisions for end-of-life parts were sized in spreadsheets. A planner exported
  inventory, open orders, BOM usage and forecast from [ERP / planning tools] every cycle and joined them by hand.
  It took [N hours per cycle] and errors were common.
- **Task:** I owned [the LTB recommendation for my commodity / program] and wanted the inputs to be repeatable
  and checkable.
- **Action:** I wrote [SQL / Python] that pulled each source, normalized part numbers, joined them at the right
  grain (part × site × week), and calculated the LTB quantity from remaining demand, on-hand stock and the
  supplier's minimums. I added checks that flagged parts with missing EOL dates or forecasts that didn't match
  the BOM, so bad inputs were caught before a buy, not after.
- **Result:** [Cycle time from N hours to N minutes], [N fewer manual errors], and the logic was documented
  so others could run it. *Link to the project:* the same "check the data before trusting the output" rule is
  why my eval harness caught a data-generator bug.

**2. "Tell me about a time bad data led to a wrong decision, and what you did."**
- **Situation:** An E&O review flagged [a group of parts] as excess, but the planners knew some of them were
  still being consumed.
- **Task:** Find out whether the excess number was right before [$ amount] of scrap was approved.
- **Action:** I traced it back and found the forecast was being joined at the platform level, so parts shared
  across platforms were [double-counted / missing demand]. I fixed the logic to aggregate demand per part
  before joining, re-ran the analysis, and added a reconciliation check comparing total demand before and after
  the join.
- **Result:** [N parts / $ amount] removed from the scrap list, [$ recovered or write-off avoided]. *Lesson I
  use now:* aggregate each fact to the join grain first. It's question 12 in my SQL Q&A doc for exactly this
  reason.

**3. "Tell me about working with stakeholders who wanted different things."**
- **Situation:** Finance wanted E&O exposure reported conservatively and early; planning wanted to hold
  inventory longer in case demand came back; suppliers wanted clarity on cancellations.
- **Task:** Produce one number and one recommendation that all three could act on.
- **Action:** I agreed definitions first (what counts as excess, which forecast version, what holding period),
  wrote them down as the source of truth, and built the report from those definitions so the debate moved
  from "whose number is right" to "which decision do we make." I also split the output into scrap / hold /
  return-to-supplier buckets so each team saw its own action.
- **Result:** [Review time cut, fewer reworks, $ recovered through supplier returns or consignment].
  *Link to the project:* that's why the copilot has a business glossary in its knowledge base: the definitions
  have to be explicit before anyone, human or model, writes the SQL.

### 7.4 When you don't know the answer to a technical question

1. **Don't bluff.** Interviewers ask follow-up questions specifically to find bluffing, and it costs more
   than the gap itself.
2. **Say what you do know, then reason out loud.** "I haven't used Kafka in production. My understanding is
   it's a distributed log, so producers append and consumers read at their own offset. If I were designing
   this, I'd want to know about ordering and replay..." Interviewers are grading how you think as much as
   what you remember.
3. **Ask a clarifying question.** "Is this for batch or streaming?" or "How big is the table?" often turns a
   question you can't answer into one you can, and it's what a good engineer does anyway.
4. **Connect it to something you've built.** "I haven't done CDC, but in my project I version the knowledge
   base so a rebuild never serves stale data; I'd guess the concern with CDC is similar, keeping consumers
   consistent with the source."
5. **Close the loop.** "I'd want to read up on that. Here's how I'd check..." If it's still bugging you after
   the interview, send a short follow-up note with the answer. It shows curiosity and follow-through.
