from __future__ import annotations

import os
from typing import Any, Dict, List

from app.rag.hybrid_retriever import retrieve
from app.rag.prompt_context import build_context
from app.rag.reranker import rerank


SYSTEM_RULES = """
You are a senior analytics engineer. Convert the user's question into ONE safe PostgreSQL SELECT query.

Hard rules:
- Output ONLY SQL (no markdown, no commentary).
- Single SELECT statement only. No semicolons.
- Use ONLY tables/columns that appear in the provided Context docs. Do NOT invent columns.
- Column names must match EXACTLY (case + spelling) as shown in Context.
- Prefer explicit JOINs using join edges shown in Context.
- Always include LIMIT 100 unless the user explicitly asks for full results.
- If filtering lifecycle, use dim_part.lifecycle_state (values like 'Active','EOL','EOSS','Discontinued').

If the user request is ambiguous, choose the most useful interpretation and return a query that still helps.
""".strip()


def build_prompt(question: str) -> Dict[str, Any]:
    """
    Returns:
      - prompt (string)
      - citations (list[str])
      - retrieval_items (list[dict])  : full retrieved list (debug)
      - final_items (list[dict])      : items actually used in prompt context
      - rerank meta                  : selected_ids + reason
      - prompt_version / kb_version
    """
    retrieval_bundle = retrieve(question)
    retrieved_items: List[Dict[str, Any]] = retrieval_bundle.get("items", [])

    # Retrieve top 12 chunks
    candidates = retrieved_items[:12]

    use_rerank = os.getenv("USE_RERANK", "true").lower() in ("1", "true", "yes", "y")
    rr_meta: Dict[str, Any] = {"selected_ids": [], "reason": "", "model": None}

    # Rerank down to top 6 (or fall back)
    if use_rerank and candidates:
        rr = rerank(question, candidates, top_n=6)
        final_items = rr.get("kept_items") or candidates[:6]
        rr_meta = {
            "selected_ids": rr.get("selected_ids", []),
            "reason": rr.get("reason", ""),
            "model": rr.get("model", None),
        }
    else:
        final_items = candidates[:6]

    # Build context using ONLY final items
    ctx = build_context({"items": final_items})

    prompt = f"""{SYSTEM_RULES}

Context (authoritative):
{ctx['context_text']}

User question:
{question}

SQL:
"""

    return {
        "prompt": prompt,
        "citations": ctx.get("citations", []),
        "retrieval_items": retrieved_items,   # full retrieved list (debug)
        "final_items": final_items,           # actually used
        "rerank": rr_meta,
        "prompt_version": os.getenv("PROMPT_VERSION", "v1"),
        "kb_version": os.getenv("KB_VERSION", "v1"),
        "use_rerank": use_rerank,
    }
