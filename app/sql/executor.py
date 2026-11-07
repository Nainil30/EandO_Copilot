"""Guarded SQL execution against Postgres.

Every LLM-generated query runs:
- on a (optionally) least-privilege read-only role
- inside a READ ONLY transaction (Postgres rejects writes even if the validator missed one)
- with a per-statement timeout (protects against accidental cartesian joins)
- with a hard row cap (fetchmany) so responses stay small
`explain()` is used as a cheap pre-flight check that catches unknown columns
before we spend a round-trip executing, and feeds errors to the repair loop.
"""

from __future__ import annotations

import datetime as dt
import time
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import get_settings

_engines: dict[str, Engine] = {}


class SQLExecutionError(RuntimeError):
    pass


def get_engine(readonly: bool = True) -> Engine:
    s = get_settings()
    url = s.sqlalchemy_readonly_url if readonly else s.sqlalchemy_url
    if url not in _engines:
        _engines[url] = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, connect_args={"connect_timeout": 3})
    return _engines[url]


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def _guarded(conn, timeout_ms: int) -> None:
    conn.execute(text("SET TRANSACTION READ ONLY"))
    conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))


def run_sql(sql: str, *, max_rows: int | None = None, timeout_ms: int | None = None) -> dict[str, Any]:
    s = get_settings()
    max_rows = max_rows or s.sql_max_rows
    timeout_ms = timeout_ms or s.sql_statement_timeout_ms
    t0 = time.perf_counter()
    try:
        with get_engine().connect() as conn, conn.begin():
            _guarded(conn, timeout_ms)
            result = conn.execute(text(sql))
            cols = list(result.keys())
            rows = result.fetchmany(max_rows + 1)
    except Exception as e:  # noqa: BLE001
        raise SQLExecutionError(_clean_error(e)) from e
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    return {
        "columns": cols,
        "rows": [[_jsonable(v) for v in r] for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "execution_ms": int((time.perf_counter() - t0) * 1000),
    }


def explain(sql: str) -> dict[str, Any]:
    """Plan the query without executing it. Raises SQLExecutionError on invalid SQL."""
    try:
        with get_engine().connect() as conn, conn.begin():
            _guarded(conn, 3000)
            plan = conn.execute(text(f"EXPLAIN (FORMAT JSON) {sql}")).scalar()
    except Exception as e:  # noqa: BLE001
        raise SQLExecutionError(_clean_error(e)) from e
    top = (plan or [{}])[0].get("Plan", {})
    return {"estimated_rows": top.get("Plan Rows"), "total_cost": top.get("Total Cost"), "node": top.get("Node Type")}


def ping() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def _clean_error(e: Exception) -> str:
    # psycopg2 errors are wrapped by SQLAlchemy; keep the useful first lines.
    orig = getattr(e, "orig", None)
    msg = str(orig or e)
    return "\n".join(msg.strip().splitlines()[:4])
