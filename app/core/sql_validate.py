from __future__ import annotations

from typing import Tuple, Set
import sqlglot
from sqlglot import expressions as exp

ALLOWED_TABLES: Set[str] = {
    "dim_part",
    "dim_platform",
    "dim_supplier",
    "fact_bom",
    "fact_inventory",
    "fact_forecast",
    "fact_ltb_orders",
    "fact_excess_calculation",
    "fact_scrap_approval",
}

def extract_tables(sql: str) -> Set[str]:
    parsed = sqlglot.parse_one(sql, read="postgres")
    tables = set()
    for t in parsed.find_all(exp.Table):
        # t.name gives table identifier without schema
        tables.add(t.name)
    return tables

def validate_sql(sql: str) -> Tuple[bool, list[str]]:
    """
    Returns (is_ok, warnings).
    - Only one statement
    - Must be SELECT
    - No semicolons
    - Only allowed tables
    """
    s = sql.strip()

    warnings: list[str] = []

    if ";" in s:
        return False, ["Semicolons/multiple statements are not allowed."]

    try:
        parsed = sqlglot.parse_one(s, read="postgres")
    except Exception as e:
        return False, [f"SQL parse failed: {e}"]

    # Ensure it is a SELECT query
    if not isinstance(parsed, exp.Select) and not parsed.find(exp.Select):
        return False, ["Only SELECT queries are allowed."]

    # Disallow INTO (SELECT INTO)
    if parsed.find(exp.Into):
        return False, ["SELECT INTO is not allowed."]

    # Table allowlist enforcement
    used_tables = extract_tables(s)
    unknown = sorted([t for t in used_tables if t not in ALLOWED_TABLES])

    if unknown:
        return False, [f"Query uses disallowed/unknown tables: {unknown}"]

    # Soft warnings
    if "limit" not in s.lower():
        warnings.append("No LIMIT detected. Consider adding LIMIT to reduce large results.")

    return True, warnings
