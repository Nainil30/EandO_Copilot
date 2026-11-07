"""Thin wrapper around a persistent ChromaDB collection.

One collection per KB version (`eando_kb_<version>`) using cosine distance.
Chunk kind lives in metadata so we can filter (`where={"kind": ...}`) instead
of maintaining several collections. Versioned collections make re-indexing a
blue/green operation: build v3 while v2 keeps serving, then flip KB_VERSION.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.rag.documents import Chunk


@lru_cache(maxsize=1)
def _client():
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    return chromadb.PersistentClient(path=get_settings().chroma_dir, settings=ChromaSettings(anonymized_telemetry=False))


def collection_name(version: str | None = None) -> str:
    return f"eando_kb_{version or get_settings().kb_version}"


def get_collection(version: str | None = None):
    return _client().get_or_create_collection(name=collection_name(version), metadata={"hnsw:space": "cosine"})


def reset_collection(version: str | None = None) -> None:
    try:
        _client().delete_collection(collection_name(version))
    except Exception:  # noqa: BLE001 – collection may not exist yet
        pass


def upsert(chunks: list[Chunk], embeddings: list[list[float]]) -> None:
    col = get_collection()
    col.upsert(
        ids=[c.id for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[c.chroma_metadata() for c in chunks],
        embeddings=embeddings,
    )


def query(embedding: list[float], k: int, where: dict[str, Any] | None = None) -> list[Chunk]:
    col = get_collection()
    n = col.count()
    if n == 0:
        return []
    res = col.query(
        query_embeddings=[embedding],
        n_results=min(k, n),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    out = []
    for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
        ch = Chunk.from_store(cid, doc, meta)
        ch.signals["dense_similarity"] = round(1.0 - float(dist), 4)
        out.append(ch)
    return out


def all_chunks() -> list[Chunk]:
    data = get_collection().get(include=["documents", "metadatas"])
    return [Chunk.from_store(i, d, m) for i, d, m in zip(data["ids"], data["documents"], data["metadatas"])]


def count() -> int:
    return get_collection().count()
