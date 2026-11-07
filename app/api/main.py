"""FastAPI application: versioned REST API + SSE streaming."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import __version__
from app.api.schemas import AskRequest, AskResponse, FeedbackRequest, RetrieveRequest, SQLRequest
from app.api.security import rate_limited, require_api_key
from app.config import get_settings
from app.observability import audit
from app.observability.logging import configure_logging, request_id_var
from app.pipeline.graph import run_pipeline, run_pipeline_iter
from app.pipeline.memory import memory
from app.sql import executor
from app.sql.catalog import JOIN_EDGES, TABLES
from app.sql.validator import validate_sql

log = logging.getLogger("eando.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    configure_logging(s.log_level)
    log.info("starting E&O Copilot api provider=%s kb=%s", s.llm_provider, s.kb_version)
    yield


app = FastAPI(
    title="E&O Copilot API",
    version=__version__,
    description=(
        "Agentic RAG text-to-SQL copilot for Excess & Obsolete inventory analytics. "
        "Hybrid retrieval (BM25 + dense, RRF), LLM reranking, structured generation, "
        "AST guardrails, self-healing SQL and grounded answer synthesis."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    response.headers["X-Process-Time-Ms"] = str(int((time.perf_counter() - t0) * 1000))
    return response


# ---------------------------------------------------------------- health ----
@app.get("/health", tags=["ops"])
def health():
    """Liveness probe – process is up."""
    return {"status": "ok", "version": __version__}


@app.get("/ready", tags=["ops"])
def ready():
    """Readiness probe – dependencies reachable."""
    from app.rag import vector_store

    s = get_settings()
    try:
        kb_chunks = vector_store.count()
    except Exception:  # noqa: BLE001
        kb_chunks = -1
    db_ok = executor.ping()
    return {
        "status": "ok" if db_ok and kb_chunks > 0 else "degraded",
        "database": db_ok,
        "kb_chunks": kb_chunks,
        "kb_version": s.kb_version,
        "llm_provider": s.llm_provider,
    }


# ------------------------------------------------------------------- ask ----
@app.post("/v1/ask", response_model=AskResponse, tags=["copilot"])
def ask(req: AskRequest, _: str = Depends(rate_limited)):
    """Natural-language question -> grounded SQL -> results -> business answer."""
    return run_pipeline(req.question, execute=req.execute, session_id=req.session_id)


@app.post("/v1/ask/stream", tags=["copilot"])
def ask_stream(req: AskRequest, _: str = Depends(rate_limited)):
    """Same as /v1/ask but streams each pipeline step as Server-Sent Events."""

    def gen():
        for ev in run_pipeline_iter(req.question, execute=req.execute, session_id=req.session_id):
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.delete("/v1/sessions/{session_id}", tags=["copilot"])
def clear_session(session_id: str, _: str = Depends(require_api_key)):
    memory.clear(session_id)
    return {"cleared": session_id}


@app.post("/v1/feedback", tags=["copilot"])
def feedback(req: FeedbackRequest, _: str = Depends(require_api_key)):
    """Thumbs up/down (+ optional corrected SQL) – the raw material for new eval cases."""
    audit.write_feedback(req.model_dump())
    return {"recorded": True}


# ------------------------------------------------------------------- sql ----
@app.post("/v1/sql", tags=["sql"])
def run_sql(req: SQLRequest, _: str = Depends(rate_limited)):
    """Run analyst-written SQL through the same guardrails as generated SQL."""
    s = get_settings()
    v = validate_sql(req.sql, default_limit=s.sql_default_limit, max_rows=s.sql_max_rows)
    if not v.ok:
        raise HTTPException(400, {"error": "unsafe_sql", "messages": v.errors})
    try:
        result = executor.run_sql(v.sql)
    except executor.SQLExecutionError as e:
        raise HTTPException(422, {"error": "execution_failed", "message": str(e), "sql": v.sql}) from e
    return {"sql": v.sql, "warnings": v.warnings, "result": result}


@app.post("/v1/sql/validate", tags=["sql"])
def validate(req: SQLRequest, _: str = Depends(require_api_key)):
    s = get_settings()
    v = validate_sql(req.sql, default_limit=s.sql_default_limit, max_rows=s.sql_max_rows)
    return {"ok": v.ok, "sql": v.sql, "errors": v.errors, "warnings": v.warnings, "tables": v.tables}


@app.get("/v1/schema", tags=["sql"])
def schema():
    """Semantic layer: tables, column descriptions, valid values and join graph."""
    return {
        "tables": [
            {
                "name": t.name,
                "description": t.description,
                "grain": t.grain,
                "columns": [
                    {"name": c.name, "type": c.type, "description": c.description, "values": list(c.values)} for c in t.columns
                ],
            }
            for t in TABLES
        ],
        "joins": [e.__dict__ for e in JOIN_EDGES],
    }


# -------------------------------------------------------------------- kb ----
@app.post("/v1/kb/build", tags=["knowledge-base"])
def kb_build(_: str = Depends(require_api_key)):
    """(Re)index schema chunks, glossary and SQL examples into the vector store."""
    from app.rag.kb_builder import build_kb

    return build_kb()


@app.get("/v1/kb/stats", tags=["knowledge-base"])
def kb_stats():
    from app.rag import vector_store

    chunks = vector_store.all_chunks()
    by_kind: dict[str, int] = {}
    for c in chunks:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
    return {"collection": vector_store.collection_name(), "chunks": len(chunks), "by_kind": by_kind}


@app.get("/v1/kb/chunks", tags=["knowledge-base"])
def kb_chunks(kind: str | None = None):
    from app.rag import vector_store

    return [
        {"id": c.id, "kind": c.kind, "title": c.title, "source": c.source, "text": c.text}
        for c in vector_store.all_chunks()
        if kind is None or c.kind == kind
    ]


@app.post("/v1/retrieve/debug", tags=["knowledge-base"])
def retrieve_debug(req: RetrieveRequest, _: str = Depends(rate_limited)):
    """Inspect dense vs BM25 vs fused vs reranked results for a question."""
    from app.rag.reranker import rerank
    from app.rag.retriever import retrieve

    s = get_settings()
    r = retrieve(req.question)
    fused = [c for c in r.chunks if "expanded" not in c.signals]
    kept, meta = rerank(req.question, fused, s.context_top_n) if req.rerank else (fused[: s.context_top_n], {"used": False})
    return {
        "question": req.question,
        "dense": r.dense_ids,
        "bm25": r.sparse_ids,
        "fused": [c.public() for c in fused],
        "graph_expanded": r.expanded_ids,
        "reranked": [c.id for c in kept],
        "rerank": meta,
    }


# --------------------------------------------------------------- metrics ----
@app.get("/v1/metrics", tags=["ops"])
def metrics(_: str = Depends(require_api_key)):
    """Online quality + cost metrics aggregated from the audit log."""
    return audit.metrics()


@app.get("/v1/audit", tags=["ops"])
def audit_tail(limit: int = 50, _: str = Depends(require_api_key)):
    return list(reversed(audit.read_jsonl("audit.jsonl", min(limit, 500))))
