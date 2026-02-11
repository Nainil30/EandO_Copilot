# Decisions (ADR-lite) — E&O Copilot

## 1) Why Postgres + Docker?
- Reproducible environment
- Mirrors real enterprise analytics stacks
- Easy local start/stop via `docker compose`

## 2) Why hybrid retrieval (vector + BM25)?
- Vector retrieval handles semantic questions
- BM25 catches exact schema tokens (column/table names)
- Combined gives higher reliability on text-to-SQL tasks

## 3) Why reranking after retrieval?
- Retrieval returns “pretty good” docs, but not always the best
- Reranking selects the most relevant context and improves SQL accuracy
- Production pattern: retrieve k=10–20 → rerank → generate

## 4) Why Gemini model choices?
- One model for embeddings
- One model for reranking (cheap/fast)
- One model for SQL generation (more reliable)
Models are configurable in `.env` so we can swap without code changes.

## 5) Why audit logs?
- Governance: who asked what, what SQL ran
- Debugging: reproduce failures later
- Metrics: latency + accuracy tracking

## 6) Prompt versioning strategy
- Prompts are stored in docs/PROMPTS.md
- Code references `PROMPT_VERSION`
- Audit logs include prompt_version and kb_version
