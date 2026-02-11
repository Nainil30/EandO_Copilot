from __future__ import annotations

from typing import List, Dict, Any, Tuple
import os
import re
from diskcache import Cache
from rank_bm25 import BM25Okapi

from app.rag.vector_store import get_collection, embed_texts

CACHE_DIR = os.getenv("RAG_CACHE_DIR", ".cache/rag")
cache = Cache(CACHE_DIR)

# ---------- text utils ----------

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

def _tokenize(s: str) -> List[str]:
    if not s:
        return []
    return _TOKEN_RE.findall(s.lower())

# ---------- bm25 ----------

def _bm25_rank(query: str, docs: List[Dict[str, Any]], top_k: int) -> List[Tuple[float, Dict[str, Any]]]:
    if not docs:
        return []
    corpus = [_tokenize(d.get("text", "")) for d in docs]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(_tokenize(query))
    pairs = list(zip(scores, docs))
    pairs.sort(key=lambda x: x[0], reverse=True)
    return pairs[: max(1, top_k)]

# ---------- chroma helpers ----------

def _safe_top_k(collection_name: str, requested: int) -> int:
    """
    Prevent Chroma warnings/errors when asking for more docs than the collection has.
    """
    try:
        col = get_collection(collection_name)
        count = col.count()
        if not count:
            return 1
        return min(requested, count)
    except Exception:
        return max(1, requested)

def _vector_search(collection_name: str, query: str, top_k: int) -> List[Dict[str, Any]]:
    col = get_collection(collection_name)
    top_k = _safe_top_k(collection_name, top_k)

    q_emb = embed_texts([query])[0]

    # IMPORTANT: Do NOT include "ids" in include[]. Many Chroma versions reject it.
    # IDs come back in res["ids"] automatically.
    res = col.query(
        query_embeddings=[q_emb],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    ids = res.get("ids", [[]])[0]
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]

    out: List[Dict[str, Any]] = []
    for i in range(len(ids)):
        out.append(
            {
                "id": ids[i],
                "text": docs[i],
                "metadata": metas[i],
                "score": float(dists[i]) if dists and i < len(dists) else None,  # smaller is better (distance)
                "source": collection_name,  # vector source
            }
        )
    return out

def _collection_docs(collection_name: str) -> List[Dict[str, Any]]:
    col = get_collection(collection_name)

    # IMPORTANT: Do NOT include "ids" in include[]. IDs still returned in data["ids"] automatically.
    data = col.get(include=["documents", "metadatas"])

    ids = data.get("ids", [])
    docs = data.get("documents", [])
    metas = data.get("metadatas", [])

    out: List[Dict[str, Any]] = []
    for i in range(len(ids)):
        out.append(
            {
                "id": ids[i],
                "text": docs[i],
                "metadata": metas[i] if metas and i < len(metas) else {},
                "source": collection_name,  # collection name
            }
        )
    return out

# ---------- main retrieve ----------

def retrieve(question: str, top_k_schema: int = 6, top_k_business: int = 2, top_k_examples: int = 3) -> Dict[str, Any]:
    cache_key = f"rag::{question}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    # Vector search per collection
    vec_schema = _vector_search("schema", question, top_k_schema)
    vec_business = _vector_search("business", question, top_k_business)
    vec_examples = _vector_search("sql_examples", question, top_k_examples)

    # BM25 across all collections (stronger hybrid than schema-only)
    schema_all = _collection_docs("schema")
    business_all = _collection_docs("business")
    examples_all = _collection_docs("sql_examples")

    bm25_schema = _bm25_rank(question, schema_all, top_k_schema)
    bm25_business = _bm25_rank(question, business_all, top_k_business)
    bm25_examples = _bm25_rank(question, examples_all, top_k_examples)

    # Merge all hits with dedupe by id
    merged: Dict[str, Dict[str, Any]] = {}

    def add_items(items: List[Dict[str, Any]]):
        for it in items:
            merged[it["id"]] = it

    add_items(vec_schema)
    add_items(vec_business)
    add_items(vec_examples)

    # Add BM25 hits (as boost metadata)
    def add_bm25(pairs: List[Tuple[float, Dict[str, Any]]], label: str):
        for score, doc in pairs:
            did = doc["id"]
            if did not in merged:
                merged[did] = {**doc, "score": float(score), "source": label}
            else:
                meta = merged[did].setdefault("metadata", {})
                meta[f"bm25_boost_{label}"] = float(score)

    add_bm25(bm25_schema, "schema_bm25")
    add_bm25(bm25_business, "business_bm25")
    add_bm25(bm25_examples, "examples_bm25")

    items = list(merged.values())

    # Ranking heuristic:
    # - Prefer sql_example > business > schema
    # - For vector results: smaller distance = better
    # - For bm25-only results: higher score = better
    def priority(d: Dict[str, Any]) -> int:
        kind = (d.get("metadata") or {}).get("kind", "")
        if kind == "sql_example":
            return 3
        if kind == "business":
            return 2
        if kind in ("fk", "table"):
            return 1
        return 0

    def sort_key(d: Dict[str, Any]):
        pri = priority(d)
        src = d.get("source", "")
        score = d.get("score", None)

        # Vector collections: score is distance => smaller is better
        if src in ("schema", "business", "sql_examples"):
            # None distance should be treated as "bad"
            dist = float(score) if score is not None else 1e9
            return (-pri, dist)
        # BM25-only: higher is better
        bm = float(score) if score is not None else 0.0
        return (-pri, -bm)

    items.sort(key=sort_key)

    out = {
        "question": question,
        "items": items[:12],  # context budget
    }

    cache.set(cache_key, out, expire=300)  # 5 min TTL
    return out

