"""Model Context Protocol (MCP) server.

Exposes the copilot as tools any MCP client (Claude Desktop, Claude Code,
Cursor, VS Code, custom agents) can call:

    ask_eando(question)        -> full agentic pipeline (answer + SQL + rows)
    run_readonly_sql(sql)      -> guarded execution of analyst SQL
    describe_schema(table?)    -> semantic-layer docs
    search_knowledge_base(q)   -> hybrid retrieval results

Run (stdio):   python -m app.mcp_server
Claude Desktop / Claude Code config example is in the README.
"""

from __future__ import annotations

from typing import Any

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]

from app.config import get_settings
from app.pipeline.graph import run_pipeline
from app.sql import executor
from app.sql.catalog import TABLE_BY_NAME, TABLES, table_doc
from app.sql.validator import validate_sql

mcp = _Server(
    name="eando-copilot",
    instructions=(
        "Read-only analytics over an Excess & Obsolete (E&O) inventory warehouse: parts, suppliers, "
        "platforms, BOMs, inventory, forecasts, last-time-buy orders, excess calculations and scrap "
        "approvals. Prefer ask_eando for natural-language questions."
    ),
)


@mcp.tool()
def ask_eando(question: str, session_id: str | None = None) -> dict[str, Any]:
    """Answer a natural-language E&O analytics question. Returns answer, SQL, and up to 50 rows."""
    r = run_pipeline(question, execute=True, session_id=session_id)
    result = r.get("result") or {}
    return {
        "status": r["status"],
        "answer": r["answer"],
        "key_insights": r["key_insights"],
        "sql": r["sql"],
        "columns": result.get("columns", []),
        "rows": (result.get("rows") or [])[:50],
        "row_count": result.get("row_count", 0),
        "citations": [c["title"] for c in r["citations"]],
    }


@mcp.tool()
def run_readonly_sql(sql: str) -> dict[str, Any]:
    """Execute a single read-only PostgreSQL SELECT against the E&O warehouse (guardrailed)."""
    s = get_settings()
    v = validate_sql(sql, default_limit=s.sql_default_limit, max_rows=s.sql_max_rows)
    if not v.ok:
        return {"ok": False, "errors": v.errors}
    try:
        res = executor.run_sql(v.sql, max_rows=200)
    except executor.SQLExecutionError as e:
        return {"ok": False, "errors": [str(e)], "sql": v.sql}
    return {"ok": True, "sql": v.sql, "warnings": v.warnings, **res}


@mcp.tool()
def describe_schema(table: str | None = None) -> str:
    """Describe one table (or all tables) with column meanings, valid values and joins."""
    if table:
        t = TABLE_BY_NAME.get(table)
        return table_doc(t) if t else f"Unknown table {table}. Known: {', '.join(TABLE_BY_NAME)}"
    return "\n\n".join(table_doc(t) for t in TABLES)


@mcp.tool()
def search_knowledge_base(query: str, k: int = 6) -> list[dict[str, Any]]:
    """Hybrid (BM25 + dense, RRF-fused) search over schema docs, glossary and SQL examples."""
    from app.rag.retriever import retrieve

    return [{"id": c.id, "kind": c.kind, "title": c.title, "text": c.text} for c in retrieve(query, top_k=k).chunks[:k]]


if __name__ == "__main__":
    mcp.run()
