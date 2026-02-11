# app/rag/reranker.py
from __future__ import annotations
import json
import os
from typing import Dict, Any, List

from app.llm.gemini_client import generate_text

RERANK_PROMPT_TEMPLATE = """
You are reranking retrieval results for a text-to-SQL system.

Return JSON only:
{{
  "selected_ids": ["id1","id2",...],
  "reason": "one short sentence"
}}

Rules:
- Select the most useful chunks to write correct SQL.
- Prefer schema chunks needed for joins + keys.
- Prefer SQL examples if they match the join/aggregation pattern.
- Select exactly {top_n} ids (or fewer if not enough exist).
- JSON ONLY. No markdown.

Question:
{question}

Chunks (JSON array of objects with id, source, text):
{chunks_json}
""".strip()

def rerank(question: str, items: List[Dict[str, Any]], top_n: int = 6) -> Dict[str, Any]:
    model = os.getenv("GEMINI_RERANK_MODEL", "gemini-2.5-flash-lite")

    # Keep rerank prompt small: truncate long docs
    slim = []
    for it in items:
        text = (it.get("text") or "")[:900]
        slim.append({"id": it.get("id"), "source": it.get("source"), "text": text})

    prompt = RERANK_PROMPT_TEMPLATE.format(
        question=question,
        top_n=top_n,
        chunks_json=json.dumps(slim, ensure_ascii=False),
    )

    raw = generate_text(prompt=prompt, model=model)


    # Defensive JSON parse
    try:
        data = json.loads(raw)
    except Exception:
        data = {"selected_ids": [], "reason": "rerank_parse_failed", "raw": raw}

    selected = data.get("selected_ids") or []
    selected_set = set(selected)

    kept = [it for it in items if it.get("id") in selected_set]

    return {
        "model": model,
        "reason": data.get("reason", ""),
        "selected_ids": selected,
        "kept_items": kept,
        "raw": raw if "raw" in data else None,
    }
