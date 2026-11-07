"""AST-based SQL guardrails (defence in depth layer #2, after the input guard).

Regex blocklists are trivially bypassed (comments, casing, unicode). We parse
the SQL into an AST with sqlglot and enforce policy on the tree:

- exactly one statement, and it must be a read-only query (SELECT / UNION / CTE)
- no DML/DDL/COPY/SELECT INTO anywhere in the tree (incl. data-modifying CTEs)
- table allow-list (CTE names are permitted)
- no system catalogs (pg_*, information_schema)
- dangerous function deny-list (pg_sleep, file access, dblink, ...)
- LIMIT injection / clamping so a bad query can't dump the warehouse

The DB layer adds layer #3: READ ONLY transaction + statement_timeout + a
least-privilege role (see db/schema.sql).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.sql.catalog import ALLOWED_TABLES

FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Merge,
    exp.Command,
    exp.Copy,
    exp.Grant,
    exp.Into,
)

DENIED_FUNCTIONS = {
    "pg_sleep",
    "pg_sleep_for",
    "pg_sleep_until",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "dblink",
    "dblink_exec",
    "set_config",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "query_to_xml",
    "current_setting",
}


@dataclass
class ValidationResult:
    ok: bool
    sql: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)


def _strip(sql: str) -> str:
    s = sql.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("sql"):
            s = s[3:]
    s = s.strip()
    # a single trailing semicolon is harmless; anything else is multi-statement
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def validate_sql(sql: str, *, default_limit: int = 100, max_rows: int = 1000) -> ValidationResult:
    s = _strip(sql or "")
    if not s:
        return ValidationResult(False, s, ["Empty SQL."])

    try:
        statements = [st for st in sqlglot.parse(s, read="postgres") if st is not None]
    except Exception as e:  # sqlglot.ParseError and friends
        return ValidationResult(False, s, [f"SQL parse failed: {e}"])

    if len(statements) != 1:
        return ValidationResult(False, s, ["Exactly one statement is allowed."])
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        return ValidationResult(False, s, [f"Only read-only SELECT queries are allowed (got {tree.key.upper()})."])

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return ValidationResult(False, s, [f"Forbidden operation: {node.key.upper()}."])

    errors: list[str] = []
    warnings: list[str] = []

    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    tables = sorted({t.name for t in tree.find_all(exp.Table) if t.name})
    for t in tree.find_all(exp.Table):
        schema = (t.db or "").lower()
        if schema in ("pg_catalog", "information_schema") or t.name.lower().startswith("pg_"):
            errors.append(f"System catalog access is not allowed: {t.sql()}")
    unknown = [t for t in tables if t not in ALLOWED_TABLES and t not in cte_names]
    if unknown:
        errors.append(f"Unknown or disallowed tables: {unknown}")

    for fn in tree.find_all(exp.Func):
        name = (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()
        if name in DENIED_FUNCTIONS:
            errors.append(f"Function not allowed: {name}()")

    if errors:
        return ValidationResult(False, s, errors, warnings, tables)

    # ---- LIMIT policy (on the outermost query) ---------------------------
    limit_node = tree.args.get("limit")
    if limit_node is None:
        tree = tree.limit(default_limit)
        warnings.append(f"No LIMIT found; added LIMIT {default_limit}.")
    else:
        try:
            n = int(limit_node.expression.name)
            if n > max_rows:
                tree = tree.limit(max_rows)
                warnings.append(f"LIMIT {n} exceeds max {max_rows}; clamped.")
        except (AttributeError, ValueError):
            warnings.append("Non-literal LIMIT; row cap enforced at execution.")

    if any(isinstance(sel, exp.Star) for sel in tree.find_all(exp.Star)):
        warnings.append("SELECT * used; prefer explicit columns.")

    return ValidationResult(
        True, tree.sql(dialect="postgres", pretty=True), [], warnings, [t for t in tables if t not in cte_names]
    )
