from __future__ import annotations

import time
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.core.db import run_sql
from app.core.schema_introspect import get_schema_snapshot
from app.core.sql_validate import validate_sql
from app.core.text2sql import build_prompt
from app.core.audit import write_audit_event

from app.llm.gemini import generate_sql
from app.rag.kb_builder import build_all

# For /rag/debug
from app.rag.hybrid_retriever import retrieve
from app.rag.reranker import rerank


app = FastAPI(title="E&O Copilot API")


class QueryRequest(BaseModel):
    sql: str


class NLQRequest(BaseModel):
    question: str
    execute: bool = True


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/schema")
def schema():
    return get_schema_snapshot()


@app.post("/rag/build")
def rag_build():
    return build_all()


@app.post("/rag/debug")
def rag_debug(req: NLQRequest):
    retrieval_bundle = retrieve(req.question)
    items = retrieval_bundle.get("items", [])[:12]

    rr = rerank(req.question, items, top_n=6)
    final_items = rr.get("kept_items") if rr.get("kept_items") else items[:6]

    return {
        "question": req.question,
        "retrieved": [{"id": i.get("id"), "source": i.get("source")} for i in items],
        "rerank_selected_ids": rr.get("selected_ids", []),
        "final": [{"id": i.get("id"), "source": i.get("source")} for i in final_items],
        "reason": rr.get("reason", ""),
        "rerank_model": rr.get("model", None),
    }


@app.post("/query")
def query(req: QueryRequest):
    ok, warnings = validate_sql(req.sql)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={"error": "unsafe_sql", "messages": warnings},
        )

    try:
        result = run_sql(req.sql)
        result["warnings"] = warnings
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/nlq")
def nlq(req: NLQRequest):
    t0 = time.time()

    # 1) Build prompt (includes retrieval + rerank selection metadata)
    bundle = build_prompt(req.question)

    prompt = bundle["prompt"]
    citations = bundle.get("citations", [])
    retrieval_items = bundle.get("retrieval_items", [])
    final_items = bundle.get("final_items", [])
    rerank_meta = bundle.get("rerank", {})
    prompt_version = bundle.get("prompt_version", "v1")
    kb_version = bundle.get("kb_version", "v1")

    retrieved_ids_12 = [x.get("id") for x in retrieval_items[:12] if x.get("id")]
    final_ids = [x.get("id") for x in final_items if x.get("id")]

    # 2) Generate SQL exactly once
    sql = generate_sql(prompt)

    # 3) Validate SQL safety
    ok, warnings = validate_sql(sql)
    if not ok:
        latency_ms = int((time.time() - t0) * 1000)
        write_audit_event({
            "type": "nlq",
            "question": req.question,
            "execute": req.execute,
            "retrieved_ids": retrieved_ids_12,
            "final_context_ids": final_ids,
            "rerank_selected_ids": rerank_meta.get("selected_ids", []),
            "rerank_reason": rerank_meta.get("reason", ""),
            "rerank_model": rerank_meta.get("model", None),
            "generated_sql": sql,
            "blocked": True,
            "warnings": warnings,
            "latency_ms": latency_ms,
            "prompt_version": prompt_version,
            "kb_version": kb_version,
        })

        raise HTTPException(
            status_code=400,
            detail={
                "error": "llm_generated_unsafe_sql",
                "generated_sql": sql,
                "messages": warnings,
            },
        )

    # 4) Execute (optional)
    result = None
    row_count: Optional[int] = None

    if req.execute:
        try:
            result = run_sql(sql)
            row_count = result.get("row_count")
        except Exception as e:
            # Optional repair attempt: only if you created app/core/sql_repair.py
            msg = str(e)

            try:
                from app.core.sql_repair import can_repair, repair_sql  # optional file
                if can_repair(msg):
                    repaired = repair_sql(sql=sql, error_message=msg, model="gemini-1.5-flash")
                    if repaired:
                        ok2, warnings2 = validate_sql(repaired)
                        if ok2:
                            try:
                                result2 = run_sql(repaired)
                                latency_ms = int((time.time() - t0) * 1000)

                                write_audit_event({
                                    "type": "nlq",
                                    "question": req.question,
                                    "execute": req.execute,
                                    "retrieved_ids": retrieved_ids_12,
                                    "final_context_ids": final_ids,
                                    "rerank_selected_ids": rerank_meta.get("selected_ids", []),
                                    "rerank_reason": rerank_meta.get("reason", ""),
                                    "rerank_model": rerank_meta.get("model", None),
                                    "generated_sql": repaired,
                                    "repaired_from": sql,
                                    "blocked": False,
                                    "warnings": warnings2,
                                    "row_count": result2.get("row_count"),
                                    "latency_ms": latency_ms,
                                    "prompt_version": prompt_version,
                                    "kb_version": kb_version,
                                    "repair_used": True,
                                    "repair_error": msg,
                                })

                                return {
                                    "question": req.question,
                                    "generated_sql": repaired,
                                    "repaired_from": sql,
                                    "repair_reason": "auto_fix_after_execution_error",
                                    "warnings": warnings2,
                                    "citations": citations,
                                    "result": result2,
                                    "retrieval_preview": [
                                        {
                                            "id": x.get("id"),
                                            "kind": (x.get("metadata") or {}).get("kind"),
                                            "title": (x.get("metadata") or {}).get("title", ""),
                                            "source": x.get("source"),
                                        }
                                        for x in retrieval_items[:8]
                                    ],
                                    "rerank": rerank_meta,
                                    "prompt_version": prompt_version,
                                    "kb_version": kb_version,
                                }
                            except Exception as e2:
                                msg = str(e2)
            except ModuleNotFoundError:
                # sql_repair.py doesn't exist; that's fine
                pass

            # Audit execution failure
            latency_ms = int((time.time() - t0) * 1000)
            write_audit_event({
                "type": "nlq",
                "question": req.question,
                "execute": req.execute,
                "retrieved_ids": retrieved_ids_12,
                "final_context_ids": final_ids,
                "rerank_selected_ids": rerank_meta.get("selected_ids", []),
                "rerank_reason": rerank_meta.get("reason", ""),
                "rerank_model": rerank_meta.get("model", None),
                "generated_sql": sql,
                "blocked": False,
                "execution_failed": True,
                "error": msg,
                "latency_ms": latency_ms,
                "prompt_version": prompt_version,
                "kb_version": kb_version,
            })

            raise HTTPException(status_code=500, detail={
                "error": "execution_failed",
                "generated_sql": sql,
                "message": msg,
            })

    # 5) Success audit log
    latency_ms = int((time.time() - t0) * 1000)
    write_audit_event({
        "type": "nlq",
        "question": req.question,
        "execute": req.execute,
        "retrieved_ids": retrieved_ids_12,
        "final_context_ids": final_ids,
        "rerank_selected_ids": rerank_meta.get("selected_ids", []),
        "rerank_reason": rerank_meta.get("reason", ""),
        "rerank_model": rerank_meta.get("model", None),
        "generated_sql": sql,
        "blocked": False,
        "warnings": warnings,
        "row_count": row_count,
        "latency_ms": latency_ms,
        "prompt_version": prompt_version,
        "kb_version": kb_version,
    })

    return {
        "question": req.question,
        "generated_sql": sql,
        "warnings": warnings,
        "citations": citations,
        "result": result,
        "retrieval_preview": [
            {
                "id": x.get("id"),
                "kind": (x.get("metadata") or {}).get("kind"),
                "title": (x.get("metadata") or {}).get("title", ""),
                "source": x.get("source"),
            }
            for x in retrieval_items[:8]
        ],
        "rerank": rerank_meta,
        "prompt_version": prompt_version,
        "kb_version": kb_version,
    }
