"""Second-stage reranking (LLM-as-reranker with structured output).

Retrieval optimises recall (12 candidates); reranking optimises precision
(keep ~6) so the generator sees less noise and fewer distracting tables.
Fails open: if the reranker errors or returns junk ids, we keep fused order –
a reranker outage must never take down the main path.
"""

from __future__ import annotations

import logging

from app.llm.factory import get_llm
from app.pipeline.outputs import RerankResult
from app.pipeline.prompts import RERANK_SYSTEM, RERANK_USER
from app.rag.documents import Chunk

log = logging.getLogger(__name__)


def format_candidates(chunks: list[Chunk], max_chars: int = 700) -> str:
    return "\n".join(
        f'<candidate id="{c.id}" kind="{c.kind}" title="{c.title}">\n{c.text[:max_chars]}\n</candidate>' for c in chunks
    )


def rerank(question: str, chunks: list[Chunk], top_n: int) -> tuple[list[Chunk], dict]:
    if len(chunks) <= top_n:
        return chunks, {"used": False, "reason": "fewer candidates than top_n"}
    prompt = RERANK_USER.format(question=question, candidates=format_candidates(chunks), top_n=top_n)
    try:
        res = get_llm().generate_structured(prompt, RerankResult, task="rerank", system=RERANK_SYSTEM, tier="fast")
    except Exception as e:  # noqa: BLE001
        log.warning("rerank failed, falling back to fused order: %s", e)
        return chunks[:top_n], {"used": False, "reason": f"rerank_error: {e}"}

    by_id = {c.id: c for c in chunks}
    kept = [by_id[i] for i in dict.fromkeys(res.selected_ids) if i in by_id][:top_n]
    if not kept:
        return chunks[:top_n], {"used": False, "reason": "rerank returned no valid ids"}
    for rank, c in enumerate(kept, 1):
        c.signals["rerank_rank"] = rank
    return kept, {"used": True, "reason": res.reason, "selected_ids": [c.id for c in kept]}
