from __future__ import annotations
from typing import List, Dict, Any
from pathlib import Path

from app.core.schema_introspect import get_schema_snapshot
from app.rag.vector_store import upsert_docs, stable_id

# Resolve project root robustly:
# app/rag/kb_builder.py -> app/rag -> app -> project_root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = PROJECT_ROOT / "docs"

def _read_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return path.read_text(encoding="utf-8").strip()

def build_schema_docs() -> List[Dict[str, Any]]:
    snap = get_schema_snapshot()
    tables = snap["tables"]
    fks = snap["foreign_keys"]

    docs = []

    for tname, cols in tables.items():
        lines = [f"TABLE {tname}:"]
        for c in cols:
            lines.append(f"- {c['column']} ({c['type']}), nullable={c['nullable']}")
        text = "\n".join(lines)

        docs.append({
            "id": stable_id("schema_table", text),
            "text": text,
            "metadata": {"kind": "table", "table": tname}
        })

    for fk in fks:
        text = (
            f"JOIN EDGE:\n"
            f"{fk['from_table']}.{fk['from_column']} -> {fk['to_table']}.{fk['to_column']}\n"
            f"Use this relationship for JOINs."
        )
        docs.append({
            "id": stable_id("schema_fk", text),
            "text": text,
            "metadata": {"kind": "fk", "from_table": fk["from_table"], "to_table": fk["to_table"]}
        })

    return docs

def build_business_docs() -> List[Dict[str, Any]]:
    path = DOCS_DIR / "business_glossary.md"
    text = _read_file(path)

    return [{
        "id": stable_id("business", text),
        "text": text,
        "metadata": {"kind": "business", "source": str(path)}
    }]

def build_sql_example_docs() -> List[Dict[str, Any]]:
    path = DOCS_DIR / "sql_examples.md"
    text = _read_file(path)

    blocks = []
    current = []
    title = "examples"

    for line in text.splitlines():
        if line.startswith("## "):
            if current:
                blocks.append((title, "\n".join(current).strip()))
            title = line.replace("## ", "").strip()
            current = [line]
        else:
            current.append(line)

    if current:
        blocks.append((title, "\n".join(current).strip()))

    docs = []
    for t, block in blocks:
        docs.append({
            "id": stable_id("sql_ex", block),
            "text": block,
            "metadata": {"kind": "sql_example", "title": t, "source": str(path)}
        })
    return docs

def build_all():
    schema_docs = build_schema_docs()
    business_docs = build_business_docs()
    sql_docs = build_sql_example_docs()

    upsert_docs("schema", schema_docs)
    upsert_docs("business", business_docs)
    upsert_docs("sql_examples", sql_docs)

    return {
        "schema_docs": len(schema_docs),
        "business_docs": len(business_docs),
        "sql_example_docs": len(sql_docs),
        "docs_dir": str(DOCS_DIR),
    }
