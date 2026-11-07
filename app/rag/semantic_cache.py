"""Semantic cache: reuse answers for paraphrased questions.

Exact-match caches miss "top suppliers by excess $" vs "which suppliers have the
most excess value". We store (question embedding, response) and serve a hit
when cosine similarity >= threshold (default 0.95). Entries are scoped to the
KB + prompt version so a KB rebuild never serves stale SQL.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from diskcache import Cache

from app.config import get_settings

_lock = threading.Lock()
_cache: Cache | None = None
_KEY = "semantic_entries"
_MAX_ENTRIES = 500


def _store() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache(get_settings().cache_dir)
    return _cache


def _scope() -> str:
    s = get_settings()
    return f"{s.kb_version}|{s.prompt_version}|{s.llm_provider}"


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def lookup(embedding: list[float]) -> tuple[dict[str, Any], float] | None:
    s = get_settings()
    entries = _store().get(_KEY, []) or []
    now = time.time()
    best, best_sim = None, 0.0
    for e in entries:
        if e["scope"] != _scope() or now - e["ts"] > s.semantic_cache_ttl_s:
            continue
        sim = _cos(embedding, e["embedding"])
        if sim > best_sim:
            best, best_sim = e, sim
    if best is not None and best_sim >= s.semantic_cache_threshold:
        return best["response"], round(best_sim, 4)
    return None


def store(question: str, embedding: list[float], response: dict[str, Any]) -> None:
    with _lock:
        entries = _store().get(_KEY, []) or []
        entries.append({"q": question, "embedding": embedding, "response": response, "ts": time.time(), "scope": _scope()})
        _store().set(_KEY, entries[-_MAX_ENTRIES:])


def clear_semantic_cache() -> None:
    with _lock:
        _store().delete(_KEY)
