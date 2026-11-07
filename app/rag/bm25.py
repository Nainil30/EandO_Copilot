"""Sparse (lexical) retrieval with BM25.

Dense embeddings are weak on exact identifiers like `fact_ltb_orders` or
`consignment_eligible`; BM25 nails them. The index is built once per KB
version and kept in memory (the old code rebuilt it on every request).
"""

from __future__ import annotations

import re
import threading

from rank_bm25 import BM25Okapi

from app.rag import vector_store
from app.rag.documents import Chunk

_TOKEN = re.compile(r"[a-z0-9]+")
_lock = threading.Lock()
_index: tuple[BM25Okapi, list[Chunk]] | None = None


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens; snake_case identifiers also yield their parts
    (`calculated_excess` -> calculated_excess, calculated, excess)."""
    out: list[str] = []
    for word in re.findall(r"[a-z0-9_]+", text.lower()):
        out.append(word)
        if "_" in word:
            out.extend(p for p in _TOKEN.findall(word) if p)
    return [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t for t in out]


def _get_index() -> tuple[BM25Okapi, list[Chunk]] | None:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                chunks = vector_store.all_chunks()
                if not chunks:
                    return None
                corpus = [tokenize(f"{c.title} {c.text}") for c in chunks]
                _index = (BM25Okapi(corpus), chunks)
    return _index


def invalidate_bm25() -> None:
    global _index
    _index = None


def search(query: str, k: int) -> list[Chunk]:
    idx = _get_index()
    if idx is None:
        return []
    bm25, chunks = idx
    scores = bm25.get_scores(tokenize(query))
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)[:k]
    out = []
    for i in ranked:
        if scores[i] <= 0:
            break
        c = Chunk(**{**chunks[i].__dict__, "signals": {}})
        c.signals["bm25"] = round(float(scores[i]), 3)
        out.append(c)
    return out
