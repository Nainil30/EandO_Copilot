"""Live schema introspection (used by /v1/schema and drift checks)."""

from __future__ import annotations

from sqlalchemy import text

from app.sql.catalog import ALLOWED_TABLES
from app.sql.executor import get_engine

_COLS = """
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position
"""


def get_schema_snapshot() -> dict:
    with get_engine(readonly=False).connect() as conn:
        rows = conn.execute(text(_COLS)).fetchall()
    tables: dict[str, list[dict]] = {}
    for t, c, dt, nul in rows:
        if t in ALLOWED_TABLES:
            tables.setdefault(t, []).append({"column": c, "type": dt, "nullable": nul == "YES"})
    return {"tables": tables}


def sample_values(table: str, column: str, limit: int = 12) -> list[str]:
    """Distinct values of a low-cardinality column – used for value linking in the KB."""
    if table not in ALLOWED_TABLES or not column.isidentifier():
        return []
    sql = f"SELECT DISTINCT {column}::text FROM {table} WHERE {column} IS NOT NULL LIMIT {int(limit)}"
    with get_engine(readonly=False).connect() as conn:
        return [r[0] for r in conn.execute(text(sql)).fetchall()]
