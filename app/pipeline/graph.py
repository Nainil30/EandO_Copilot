r"""The agentic text-to-SQL pipeline, modelled as an explicit state graph.

    guard -> contextualize -> route --(definition)--> retrieve(glossary) -> answer
                                    \--(unsafe/out_of_scope)--> END
                                    \--(data_query)--> cache? -> retrieve -> rerank
                                         -> generate_sql -> [validate -> preflight/execute]
                                              ^                     | error
                                              +------ repair <------+   (bounded loop)
                                         -> summarize -> END

Each node is a small pure-ish function over `PipelineState`. The orchestrator
is a generator that yields an event after every node, which gives us
server-sent-event streaming for the UI "thinking" panel for free, and a full
per-step trace (latency + payload) for observability. This is the same shape
as a LangGraph StateGraph, implemented without the framework dependency so
every transition is visible and unit-testable.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.guardrails.input_guard import check_input
from app.llm.base import UsageTracker, set_usage_tracker
from app.llm.factory import get_llm
from app.observability.audit import write_audit
from app.pipeline import prompts as P
from app.pipeline.memory import Turn, format_history, memory
from app.pipeline.outputs import AnswerSummary, DefinitionAnswer, QueryRewrite, RouteDecision, SQLGeneration
from app.rag import semantic_cache, vector_store
from app.rag.context import build_context
from app.rag.documents import Chunk
from app.rag.reranker import rerank
from app.rag.retriever import expand_with_schema_graph, retrieve
from app.sql import executor
from app.sql.catalog import compact_schema
from app.sql.validator import validate_sql


@dataclass
class PipelineState:
    question: str
    execute: bool = True
    session_id: str | None = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    history: list[Turn] = field(default_factory=list)
    standalone_question: str = ""
    keywords: list[str] = field(default_factory=list)
    intent: str = ""
    route_reason: str = ""

    query_embedding: list[float] | None = None
    candidates: list[Chunk] = field(default_factory=list)
    context_chunks: list[Chunk] = field(default_factory=list)
    context_text: str = ""
    rerank_meta: dict[str, Any] = field(default_factory=dict)
    retrieval_debug: dict[str, Any] = field(default_factory=dict)

    generation: SQLGeneration | None = None
    sql: str = ""
    warnings: list[str] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    preflight: dict[str, Any] | None = None
    result: dict[str, Any] | None = None

    answer: str = ""
    key_insights: list[str] = field(default_factory=list)
    chart: dict[str, Any] | None = None
    citations: list[str] = field(default_factory=list)

    status: str = "running"  # ok | blocked | clarification | out_of_scope | error | sql_only
    error: str | None = None
    cache_hit: dict[str, Any] | None = None
    cached_response: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    usage: UsageTracker = field(default_factory=UsageTracker)
    started: float = field(default_factory=time.perf_counter)


# --------------------------------------------------------------------------
# nodes
# --------------------------------------------------------------------------


def node_guard(st: PipelineState) -> dict:
    g = check_input(st.question)
    if not g.allowed:
        st.status, st.error, st.answer = "blocked", g.category, g.reason
    return {"allowed": g.allowed, "category": g.category}


def node_contextualize(st: PipelineState) -> dict:
    st.history = memory.history(st.session_id)
    st.standalone_question = st.question
    if not st.history or not get_settings().use_query_rewrite:
        return {"rewritten": False}
    rw = get_llm().generate_structured(
        P.REWRITE_USER.format(history=format_history(st.history), question=st.question),
        QueryRewrite,
        task="rewrite",
        system=P.REWRITE_SYSTEM,
        tier="fast",
    )
    st.standalone_question = rw.standalone_question.strip() or st.question
    st.keywords = rw.search_keywords
    return {"rewritten": True, "standalone_question": st.standalone_question, "keywords": st.keywords}


def node_route(st: PipelineState) -> dict:
    d = get_llm().generate_structured(
        P.ROUTER_USER.format(history=format_history(st.history), question=st.standalone_question),
        RouteDecision,
        task="route",
        system=P.ROUTER_SYSTEM,
        tier="fast",
    )
    st.intent, st.route_reason = d.intent, d.reason
    if d.intent == "unsafe":
        st.status = "blocked"
        st.error = "unsafe_intent"
        st.answer = "I can only run read-only analysis. Requests to modify or delete data are blocked."
    elif d.intent == "out_of_scope":
        st.status = "out_of_scope"
        st.answer = (
            "I'm an E&O inventory analytics copilot. Ask me about excess, inventory, forecasts, "
            "LTB orders, suppliers, platforms or scrap approvals."
        )
    return {"intent": d.intent, "reason": d.reason}


def node_cache_lookup(st: PipelineState) -> dict:
    st.query_embedding = get_llm().embed([st.standalone_question], task="query")[0]
    if not get_settings().use_semantic_cache or not st.execute:
        return {"enabled": False}
    hit = semantic_cache.lookup(st.query_embedding)
    if hit:
        resp, sim = hit
        st.cache_hit = {"similarity": sim, "original_request_id": resp.get("request_id")}
        st.cached_response = resp
        return {"hit": True, "similarity": sim}
    return {"hit": False}


def node_retrieve(st: PipelineState) -> dict:
    s = get_settings()
    r = retrieve(st.standalone_question, keywords=st.keywords, query_embedding=st.query_embedding)
    st.candidates = r.chunks
    st.retrieval_debug = {"dense": r.dense_ids, "bm25": r.sparse_ids, "graph_expanded": r.expanded_ids}
    if st.intent == "definition":
        st.candidates = [c for c in r.chunks if c.kind in ("glossary", "table")] or r.chunks
    return {
        "candidates": [{"id": c.id, "kind": c.kind, "title": c.title, "rrf": c.score} for c in st.candidates],
        "top_k": s.retrieval_top_k,
    }


def node_rerank(st: PipelineState) -> dict:
    s = get_settings()
    fused = [c for c in st.candidates if "expanded" not in c.signals]
    if s.use_rerank:
        kept, meta = rerank(st.standalone_question, fused, s.context_top_n)
    else:
        kept, meta = fused[: s.context_top_n], {"used": False, "reason": "disabled"}
    # Expand along the FK graph from the *kept* chunks only (after rerank), so the model gets every
    # table + join edge it needs for the chosen examples, without schema for tables nobody kept.
    pool = [c for c in st.candidates] + vector_store.all_chunks()
    pool = list({c.id: c for c in pool}.values())
    st.context_chunks = expand_with_schema_graph(kept, pool)
    meta["graph_expanded"] = [c.title for c in st.context_chunks[len(kept) :]]
    st.context_text, st.context_chunks = build_context(st.context_chunks, s.context_max_chars)
    st.citations = [c.id for c in st.context_chunks]
    st.rerank_meta = meta
    return {"kept": [c.id for c in st.context_chunks], **meta}


def node_answer_definition(st: PipelineState) -> dict:
    s = get_settings()
    st.context_text, st.context_chunks = build_context(st.candidates[: s.context_top_n], s.context_max_chars)
    ans = get_llm().generate_structured(
        P.DEFINITION_USER.format(context=st.context_text, question=st.standalone_question),
        DefinitionAnswer,
        task="definition",
        system=P.DEFINITION_SYSTEM,
        tier="fast",
    )
    valid = {c.id for c in st.context_chunks}
    st.answer = ans.answer
    st.citations = [i for i in ans.cited_ids if i in valid] or [c.id for c in st.context_chunks[:2]]
    st.status = "ok"
    return {"cited": st.citations}


def node_generate_sql(st: PipelineState) -> dict:
    s = get_settings()
    system = P.SQL_SYSTEM.format(default_limit=s.sql_default_limit)
    gen = get_llm().generate_structured(
        P.SQL_USER.format(context=st.context_text, question=st.standalone_question),
        SQLGeneration,
        task="generate_sql",
        system=system,
        tier="smart",
    )
    st.generation = gen
    st.sql = gen.sql
    if gen.needs_clarification or not gen.sql.strip():
        st.status = "clarification"
        st.answer = gen.clarification_question or "Could you clarify the question?"
    return {"plan": gen.plan, "sql": gen.sql, "confidence": gen.confidence, "needs_clarification": gen.needs_clarification}


def _repair(st: PipelineState, error: str) -> None:
    s = get_settings()
    system = P.REPAIR_SYSTEM.format(default_limit=s.sql_default_limit)
    context = st.context_text + f'\n<doc id="schema:compact" kind="table" title="all tables">\n{compact_schema()}\n</doc>'
    gen = get_llm().generate_structured(
        P.REPAIR_USER.format(context=context, question=st.standalone_question, sql=st.sql, error=error),
        SQLGeneration,
        task="repair_sql",
        system=system,
        tier="smart",
    )
    st.generation = gen
    st.sql = gen.sql


def node_validate_execute(st: PipelineState) -> dict:
    """Validate -> (preflight EXPLAIN -> execute) with a bounded self-correction loop."""
    s = get_settings()
    for attempt in range(s.max_repair_attempts + 1):
        v = validate_sql(st.sql, default_limit=s.sql_default_limit, max_rows=s.sql_max_rows)
        if not v.ok:
            err = "; ".join(v.errors)
            st.attempts.append({"attempt": attempt, "stage": "validate", "sql": st.sql, "error": err})
            # Security violations are never "repaired" – they end the request.
            if any(k in err for k in ("Forbidden", "read-only", "System catalog", "Function not allowed")):
                st.status, st.error = "blocked", err
                st.answer = "The generated SQL violated the read-only policy and was blocked."
                return {"blocked": True, "errors": v.errors}
            if attempt < s.max_repair_attempts:
                _repair(st, err)
                continue
            st.status, st.error = "error", err
            return {"ok": False, "errors": v.errors}

        st.sql, st.warnings = v.sql, v.warnings
        if not st.execute:
            st.status = "sql_only"
            return {"validated": True, "executed": False, "warnings": v.warnings}
        try:
            st.preflight = executor.explain(st.sql)
            st.result = executor.run_sql(st.sql)
            st.status = "ok"
            return {
                "validated": True,
                "executed": True,
                "row_count": st.result["row_count"],
                "preflight": st.preflight,
                "repairs": len(st.attempts),
            }
        except executor.SQLExecutionError as e:
            st.attempts.append({"attempt": attempt, "stage": "execute", "sql": st.sql, "error": str(e)})
            if attempt < s.max_repair_attempts:
                _repair(st, str(e))
                continue
            st.status, st.error = "error", str(e)
            return {"ok": False, "error": str(e)}
    return {"ok": False}


def node_summarize(st: PipelineState) -> dict:
    if not st.result or not get_settings().summarize_results:
        return {"skipped": True}
    rows = st.result["rows"][:30]
    prompt = P.SUMMARY_USER.format(
        question=st.standalone_question,
        sql=st.sql,
        columns=json.dumps(st.result["columns"]),
        row_count=st.result["row_count"],
        shown=len(rows),
        rows="\n".join(json.dumps(r, default=str) for r in rows),
    )
    try:
        summ = get_llm().generate_structured(prompt, AnswerSummary, task="summarize", system=P.SUMMARY_SYSTEM, tier="fast")
    except Exception as e:  # noqa: BLE001 – summary is best effort
        return {"skipped": True, "error": str(e)}
    st.answer, st.key_insights = summ.answer, summ.key_insights
    cols = set(st.result["columns"])
    chart = summ.chart.model_dump()
    st.chart = chart if chart["type"] != "none" and chart["x"] in cols and chart["y"] in cols else None
    return {"answer": st.answer, "chart": st.chart}


# --------------------------------------------------------------------------
# orchestrator
# --------------------------------------------------------------------------


def _done(st: PipelineState) -> bool:
    return st.status not in ("running",)


def _step(st: PipelineState, name: str, fn) -> dict:
    # Re-bind per step: streaming generators may resume on different worker threads.
    set_usage_tracker(st.usage)
    t0 = time.perf_counter()
    try:
        data = fn(st)
    except Exception as e:  # noqa: BLE001 – any node failure becomes a clean error response
        st.status, st.error = "error", f"{name}: {e}"
        data = {"error": str(e)}
    ev = {"type": "step", "name": name, "ms": int((time.perf_counter() - t0) * 1000), "data": data}
    st.trace.append(ev)
    return ev


def run_pipeline_iter(question: str, *, execute: bool = True, session_id: str | None = None) -> Iterator[dict]:
    st = PipelineState(question=question, execute=execute, session_id=session_id)
    usage = st.usage
    yield {"type": "start", "request_id": st.request_id}

    yield _step(st, "guard", node_guard)
    if not _done(st):
        yield _step(st, "contextualize", node_contextualize)
    # Cache before routing: a repeated/paraphrased question costs zero LLM calls.
    if not _done(st):
        yield _step(st, "cache_lookup", node_cache_lookup)
        if st.cached_response is not None:
            resp = dict(st.cached_response)
            resp.update(
                request_id=st.request_id,
                question=st.question,
                cache_hit=st.cache_hit,
                trace=st.trace,
                usage=usage.as_dict(get_settings().price_in_per_m, get_settings().price_out_per_m),
            )
            st.status = resp.get("status", "ok")
            _finish(st, usage, resp)
            yield {"type": "final", "response": resp}
            return
    if not _done(st):
        yield _step(st, "route", node_route)

    if not _done(st) and st.intent == "definition":
        yield _step(st, "retrieve", node_retrieve)
        if not _done(st):
            yield _step(st, "answer_definition", node_answer_definition)
    elif not _done(st):
        for name, fn in (
            ("retrieve", node_retrieve),
            ("rerank", node_rerank),
            ("generate_sql", node_generate_sql),
            ("validate_execute", node_validate_execute),
            ("summarize", node_summarize),
        ):
            if _done(st) and name != "summarize":
                break
            if name == "summarize" and st.status != "ok":
                break
            yield _step(st, name, fn)

    if st.status == "running":
        st.status = "ok"
    resp = build_response(st, usage)
    _finish(st, usage, resp)
    if st.status == "ok" and st.execute and st.query_embedding and st.result is not None:
        semantic_cache.store(st.standalone_question, st.query_embedding, resp)
    yield {"type": "final", "response": resp}


def run_pipeline(question: str, *, execute: bool = True, session_id: str | None = None) -> dict:
    final: dict = {}
    for ev in run_pipeline_iter(question, execute=execute, session_id=session_id):
        if ev["type"] == "final":
            final = ev["response"]
    return final


def build_response(st: PipelineState, usage) -> dict:
    s = get_settings()
    g = st.generation
    by_id = {c.id: c for c in st.context_chunks}
    return {
        "request_id": st.request_id,
        "session_id": st.session_id,
        "status": st.status,
        "question": st.question,
        "standalone_question": st.standalone_question,
        "intent": st.intent,
        "answer": st.answer,
        "key_insights": st.key_insights,
        "chart": st.chart,
        "sql": st.sql or None,
        "plan": g.plan if g else None,
        "assumptions": g.assumptions if g else [],
        "confidence": g.confidence if g else None,
        "warnings": st.warnings,
        "error": st.error,
        "result": st.result,
        "attempts": st.attempts,
        "citations": [
            {
                "id": i,
                "kind": by_id[i].kind,
                "title": by_id[i].title,
                "source": by_id[i].source,
                "score": by_id[i].score,
                "signals": by_id[i].signals,
            }
            for i in st.citations
            if i in by_id
        ],
        "retrieval": {**st.retrieval_debug, "rerank": st.rerank_meta},
        "cache_hit": st.cache_hit,
        "trace": st.trace,
        "usage": usage.as_dict(s.price_in_per_m, s.price_out_per_m),
        "versions": {"prompt": s.prompt_version, "kb": s.kb_version, "provider": s.llm_provider},
        "latency_ms": int((time.perf_counter() - st.started) * 1000),
    }


def _finish(st: PipelineState, usage, resp: dict) -> None:
    s = get_settings()
    resp["latency_ms"] = int((time.perf_counter() - st.started) * 1000)
    if st.status in ("ok", "sql_only", "clarification"):
        memory.append(st.session_id, Turn(st.question, st.standalone_question, st.sql, st.answer))
    write_audit(
        {
            "type": "ask",
            "request_id": st.request_id,
            "session_id": st.session_id,
            "question": st.question,
            "standalone_question": st.standalone_question,
            "intent": st.intent,
            "status": st.status,
            "error": st.error,
            "sql": st.sql,
            "row_count": (st.result or {}).get("row_count"),
            "repair_attempts": len(st.attempts),
            "context_ids": st.citations,
            "retrieval": st.retrieval_debug,
            "cache_hit": bool(st.cache_hit),
            "steps": {e["name"]: e["ms"] for e in st.trace},
            "latency_ms": resp["latency_ms"],
            "usage": usage.as_dict(s.price_in_per_m, s.price_out_per_m),
            "versions": {"prompt": s.prompt_version, "kb": s.kb_version, "provider": s.llm_provider},
        }
    )
