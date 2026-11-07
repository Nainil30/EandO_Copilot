"""Knowledge-base ingestion: turn schema + docs into retrievable chunks.

Chunking strategy (each chosen for text-to-SQL, not generic prose):
- table     : one chunk per table (columns + semantics + valid values + its joins).
              Table-level chunks keep all columns of a table together, which is
              what the generator needs; splitting tables mid-way hurts accuracy.
- join      : one chunk per FK edge (lets BM25 hit "join X and Y" questions)
- glossary  : markdown split on headings (business metric definitions)
- sql_example: one chunk per approved question->SQL pair (dynamic few-shot)

Ids are content hashes, so rebuilding is idempotent and diffs are cheap.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings
from app.llm.factory import get_llm
from app.rag import vector_store
from app.rag.bm25 import invalidate_bm25
from app.rag.documents import Chunk
from app.sql.catalog import JOIN_EDGES, TABLES, edge_doc, table_doc

DOCS_DIR = PROJECT_ROOT / "docs"
GLOSSARY = DOCS_DIR / "business_glossary.md"
SQL_EXAMPLES = DOCS_DIR / "sql_examples.md"

_FENCE = re.compile(r"```sql\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def schema_chunks(live_values: dict[str, list[str]] | None = None) -> list[Chunk]:
    chunks = []
    for t in TABLES:
        text = table_doc(t)
        for key, values in (live_values or {}).items():
            tbl, col = key.split(".")
            if tbl == t.name and values:
                text += f"\nSample values of {key}: " + ", ".join(f"'{v}'" for v in values) + "."
        chunks.append(Chunk(Chunk.make_id("table", t.name, text), "table", t.name, text, "catalog", {"table": t.name}))
    for e in JOIN_EDGES:
        text = edge_doc(e)
        title = f"{e.from_table} -> {e.to_table}"
        chunks.append(
            Chunk(
                Chunk.make_id("join", title, text),
                "join",
                title,
                text,
                "catalog",
                {"tables": [e.from_table, e.to_table]},
            )
        )
    return chunks


def split_markdown(text: str, level: int = 2) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) sections at the given heading level."""
    marker = "#" * level + " "
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith(marker):
            sections.append((line[len(marker) :].strip(), []))
        elif sections:
            sections[-1][1].append(line)
    return [(h, "\n".join(b).strip()) for h, b in sections if "\n".join(b).strip()]


def glossary_chunks(path: Path = GLOSSARY) -> list[Chunk]:
    out = []
    for title, body in split_markdown(path.read_text(encoding="utf-8")):
        text = f"{title}\n{body}"
        out.append(Chunk(Chunk.make_id("glossary", title, text), "glossary", title, text, path.name))
    return out


def sql_example_chunks(path: Path = SQL_EXAMPLES) -> list[Chunk]:
    out = []
    for title, body in split_markdown(path.read_text(encoding="utf-8")):
        m = _FENCE.search(body)
        if not m:
            continue
        sql = m.group(1).strip().rstrip(";")
        question = ""
        qm = re.search(r"^Question:\s*(.+)$", body, re.MULTILINE)
        if qm:
            question = qm.group(1).strip()
        tables = sorted(set(re.findall(r"\b((?:dim|fact)_[a-z_]+)\b", sql)))
        text = f"Example: {title}\nQuestion: {question or title}\nTables: {', '.join(tables)}\nSQL:\n{sql}"
        out.append(
            Chunk(
                Chunk.make_id("sql_example", title, text),
                "sql_example",
                title,
                text,
                path.name,
                {"tables": tables},
            )
        )
    return out


def _live_values() -> dict[str, list[str]]:
    """Best effort: enrich with real distinct values when the DB is reachable."""
    try:
        from app.sql.introspect import sample_values

        return {"dim_platform.platform_name": sample_values("dim_platform", "platform_name")}
    except Exception:  # noqa: BLE001
        return {}


def build_all_chunks(include_live_values: bool = True) -> list[Chunk]:
    live = _live_values() if include_live_values else {}
    return schema_chunks(live) + glossary_chunks() + sql_example_chunks()


def build_kb(*, reset: bool = True, include_live_values: bool = True) -> dict:
    t0 = time.perf_counter()
    chunks = build_all_chunks(include_live_values)
    if reset:
        vector_store.reset_collection()
    embeddings = get_llm().embed([c.text for c in chunks], task="document")
    vector_store.upsert(chunks, embeddings)
    invalidate_bm25()
    from app.rag.semantic_cache import clear_semantic_cache

    clear_semantic_cache()
    by_kind: dict[str, int] = {}
    for c in chunks:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
    return {
        "kb_version": get_settings().kb_version,
        "collection": vector_store.collection_name(),
        "chunks": len(chunks),
        "by_kind": by_kind,
        "embedding_dim": len(embeddings[0]) if embeddings else 0,
        "build_ms": int((time.perf_counter() - t0) * 1000),
    }
