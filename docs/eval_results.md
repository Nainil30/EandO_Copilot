# Evaluation Results

Measured on **2026-11-07** with real Gemini models against the Postgres warehouse.
Harness: [`evals/run_evals.py`](../evals/run_evals.py) · Dataset: [`evals/golden.jsonl`](../evals/golden.jsonl)
(37 cases, 13 categories).

## Final results

| Metric | Result | What it means |
|---|---|---|
| **Cases passing** | **37 / 37 (100%)** | All 13 categories at 100% |
| **Soft execution accuracy** | **100%** (26 gold-SQL cases) | Every result set contained the gold answer |
| Strict execution accuracy | 58% | Exact column match. Lower because the model adds helpful context columns (e.g. `country` next to `supplier_name`), which soft EX allows |
| **Table recall @ context** | **100%** | Retrieval always delivered every table the question needed |
| **Guardrail block rate** | **6 / 6 (100%)** | DROP, UPDATE, multi-statement delete, prompt injection, "wipe the data", `pg_shadow` access |
| Self-repair rate | 0% | No query needed the repair loop on this set (the loop is tested offline in `tests/test_pipeline.py`) |
| Latency p50 | ~5 s per question | Free tier; p95 is inflated by rate-limit back-off |
| Tokens per question | ~4,400 | 3-4 LLM calls, most on the `lite` model. The later post-rerank graph-expansion change cut a further 13–17% on multi-join questions |
| Est. cost per question | ≈ $0.002 at list price | **$0 actual**: the key is on the Gemini free tier |

Models: `gemini-3.5-flash` (SQL), `gemini-3.5-flash-lite` (route / rerank / rewrite / summary),
`gemini-embedding-001` (768-d), with fallback to `gemini-flash-latest` / `gemini-flash-lite-latest`.
Prompt version `sql-v4`, KB version `v2`. Eval runs use `USE_SEMANTIC_CACHE=false` (no cached answers) and
`SUMMARIZE_RESULTS=false` (execution accuracy doesn't need the prose summary, which saves a call per case).

## How we got there: the iteration log

This is the part to talk about in interviews. Each failure was diagnosed as one of
**infrastructure / eval data / retrieval / generation**, and fixed at the right layer.

| # | Observation | Root cause | Layer | Fix |
|---|---|---|---|---|
| 1 | `gemini-2.5-flash-lite` → 404 "no longer available to new users" | Model retired | Infra | Models are config; moved to the 3.5 family |
| 2 | `gemini-3.5-flash` → 503 "high demand" | Provider overload | Infra | **Model fallback chain** (`GEMINI_FALLBACK_MODELS`) |
| 3 | Run 1: 6 cases failed with `getaddrinfo failed` | Local DNS blip; network errors weren't classed as retryable, and embeddings weren't retried | Infra | Connection errors retryable; embeddings wrapped in retries |
| 4 | E09 "parts with EOL in last 6 months" failed with correct SQL | Gold query had no LIMIT, while the model correctly added `LIMIT 100` (173 rows exist) | Eval data | Made the case deterministic (COUNT) |
| 5 | E16 "20 most recent LTB orders" failed with correct SQL | **Data-generator bug:** Faker `"-12m"` means 12 *minutes*, so every LTB order, EOL date and delivery date was "today" | Data | `m` → `M` (months); regenerated data |
| 6 | Run 2: 3 cases → 429 `generate_content_free_tier_requests` (15 RPM) | Free-tier rate limit; back-off was shorter than the server's "retry in 16s" hint | Infra | Retry honours the server's `retry in Ns` hint |
| 7 | **E23** "top 20 consignment-eligible positions *by calculated excess*" ordered by `excess_value` | **Few-shot over-copying:** the retrieved example sorts by `excess_value`, and the model copied it | Generation | Prompt rule 10: "examples show patterns; the user's metric/sort/filter wins" (`sql-v3` → `sql-v4`); E01 re-checked as a regression |
| 8 | Sources tab showed `fact_inventory` / `fact_scrap_approval` in a supplier-excess context | Graph expansion ran on all 12 candidates, including weak ones | Retrieval | Expand only from the **reranked** chunks: same 5/5 pass on multi-join cases, **13–17% fewer tokens** |

Lessons:
- **Check the data and the gold answer before blaming the model.** Three of the "failures" (4, 5, and the
  run-1 network errors) weren't model errors at all.
- Separating **table recall** from **execution accuracy** showed retrieval was never the problem (100%).
  The one real miss was in generation, and the fix was a prompt rule, not a bigger model.
- Every change was verified with a targeted re-run (`--ids`) instead of a full run, to stay inside the free tier.

## Reproduce

```bash
python -m scripts.bootstrap --regenerate        # fresh data relative to today
USE_SEMANTIC_CACHE=false SUMMARIZE_RESULTS=false python -m evals.run_evals --sleep-ms 2000
python -m evals.run_evals --ids E23,E01         # re-check specific cases after a change
```
Reports are written to `reports/eval_<timestamp>.md|json` (git-ignored).
