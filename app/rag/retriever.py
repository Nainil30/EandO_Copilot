"""Hybrid retrieval = dense (embeddings) + sparse (BM25), fused with RRF,
then expanded along the foreign-key graph.

Why Reciprocal Rank Fusion?
  Dense cosine similarities and BM25 scores live on different, uncalibrated
  scales, so adding them is meaningless. RRF only uses *ranks*:
      score(d) = sum_over_retrievers 1 / (k + rank_r(d))
  It is parameter-light (k=60), robust, and the default fusion in most
  production hybrid search engines (Elastic, OpenSearch, Weaviate, pgvector setups).

Why schema-graph expansion?
  If retrieval surfaces `fact_excess_calculation` and `dim_supplier`, the model
  also needs the join edge between them and the table chunk for any bridge
  table. We add those deterministically instead of hoping the retriever finds
  them – a cheap, high-impact trick for multi-join text-to-SQL.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.config import get_settings
from app.llm.factory import get_llm
from app.rag import bm25, vector_store
from app.rag.documents import Chunk


@dataclass
class RetrievalResult:
    query: str
    chunks: list[Chunk]
    dense_ids: list[str] = field(default_factory=list)
    sparse_ids: list[str] = field(default_factory=list)
    expanded_ids: list[str] = field(default_factory=list)
    query_embedding: list[float] | None = None


def rrf_fuse(rankings: dict[str, list[Chunk]], k: int = 60, weights: dict[str, float] | None = None) -> list[Chunk]:
    """Fuse several ranked lists. Returns chunks sorted by fused score (desc)."""
    weights = weights or {}
    scores: dict[str, float] = defaultdict(float)
    best: dict[str, Chunk] = {}
    for name, ranked in rankings.items():
        w = weights.get(name, 1.0)
        for rank, ch in enumerate(ranked, start=1):
            scores[ch.id] += w / (k + rank)
            if ch.id not in best:
                best[ch.id] = ch
            else:
                best[ch.id].signals.update(ch.signals)
            best[ch.id].signals[f"{name}_rank"] = rank
    fused = sorted(best.values(), key=lambda c: scores[c.id], reverse=True)
    for c in fused:
        c.score = round(scores[c.id], 5)
    return fused


def _tables_of(c: Chunk) -> set[str]:
    if c.kind == "table":
        return {c.title}
    raw = c.metadata.get("tables", "")
    if isinstance(raw, str):
        return {t for t in raw.split(",") if t}
    return set(raw or [])


def expand_with_schema_graph(chunks: list[Chunk], pool: list[Chunk], max_tables: int = 5) -> list[Chunk]:
    """Add table + join chunks needed to connect the tables referenced by top chunks."""
    have = {c.id for c in chunks}
    tables: list[str] = []
    for c in chunks:
        for t in sorted(_tables_of(c)):
            if t not in tables:
                tables.append(t)
    tables = tables[:max_tables]
    wanted_tables = set(tables)
    added: list[Chunk] = []
    by_table = {c.title: c for c in pool if c.kind == "table"}
    joins = [c for c in pool if c.kind == "join"]
    for t in tables:
        tc = by_table.get(t)
        if tc and tc.id not in have:
            tc.signals["expanded"] = "table_for_referenced_table"
            added.append(tc)
            have.add(tc.id)
    for j in joins:
        if _tables_of(j) <= wanted_tables and j.id not in have:
            j.signals["expanded"] = "join_between_referenced_tables"
            added.append(j)
            have.add(j.id)
    return chunks + added


def retrieve(
    query: str,
    *,
    keywords: list[str] | None = None,
    top_k: int | None = None,
    query_embedding: list[float] | None = None,
) -> RetrievalResult:
    s = get_settings()
    top_k = top_k or s.retrieval_top_k
    q_emb = query_embedding or get_llm().embed([query], task="query")[0]
    dense = vector_store.query(q_emb, k=top_k)
    sparse_query = query + " " + " ".join(keywords or [])
    sparse = bm25.search(sparse_query, k=top_k)

    fused = rrf_fuse({"dense": dense, "bm25": sparse}, k=s.rrf_k)[:top_k]
    pool = vector_store.all_chunks() if fused else []
    expanded = expand_with_schema_graph(fused, pool)
    return RetrievalResult(
        query=query,
        chunks=expanded,
        dense_ids=[c.id for c in dense],
        sparse_ids=[c.id for c in sparse],
        expanded_ids=[c.id for c in expanded[len(fused) :]],
        query_embedding=q_emb,
    )
