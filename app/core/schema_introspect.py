from app.core.db import get_engine
from sqlalchemy import text

def get_schema_snapshot() -> dict:
    """
    Returns a structured schema view:
    - tables -> columns (name, type, nullable)
    - foreign_keys -> relationships for join hints
    """

    engine = get_engine()

    tables_sql = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_type = 'BASE TABLE'
    ORDER BY table_name;
    """

    cols_sql = """
    SELECT table_name, column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema = 'public'
    ORDER BY table_name, ordinal_position;
    """

    # FK info (join hints)
    fk_sql = """
    SELECT
      tc.table_name AS table_name,
      kcu.column_name AS column_name,
      ccu.table_name AS foreign_table_name,
      ccu.column_name AS foreign_column_name
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.key_column_usage AS kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage AS ccu
      ON ccu.constraint_name = tc.constraint_name
     AND ccu.table_schema = tc.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_schema = 'public'
    ORDER BY tc.table_name, kcu.column_name;
    """

    with engine.connect() as conn:
        tables = [r[0] for r in conn.execute(text(tables_sql)).fetchall()]
        cols = conn.execute(text(cols_sql)).fetchall()
        fks = conn.execute(text(fk_sql)).fetchall()

    table_map: dict[str, list[dict]] = {t: [] for t in tables}
    for t, c, dt, nul in cols:
        if t in table_map:
            table_map[t].append({
                "column": c,
                "type": dt,
                "nullable": (nul == "YES"),
            })

    fk_list = []
    for t, c, ft, fc in fks:
        fk_list.append({
            "from_table": t,
            "from_column": c,
            "to_table": ft,
            "to_column": fc,
        })

    return {
        "tables": table_map,
        "foreign_keys": fk_list,
    }
