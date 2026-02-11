from __future__ import annotations
from typing import Dict, Any, List

def build_context(retrieval: Dict[str, Any], max_chars: int = 6000) -> Dict[str, Any]:
    """
    Returns:
      - context_text (string)
      - citations (list of doc ids used)
    """
    chunks: List[str] = []
    used = []
    total = 0

    for item in retrieval["items"]:
        doc_id = item["id"]
        kind = item.get("metadata", {}).get("kind", "doc")
        title = item.get("metadata", {}).get("title", "")
        header = f"[{kind}] {title} (doc_id={doc_id})".strip()
        body = item["text"].strip()

        block = f"{header}\n{body}\n"
        if total + len(block) > max_chars:
            break

        chunks.append(block)
        used.append(doc_id)
        total += len(block)

    return {
        "context_text": "\n---\n".join(chunks),
        "citations": used
    }
