import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv()

def get_db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL missing in .env")
    return url

_engine: Engine | None = None

def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_db_url(), pool_pre_ping=True)
    return _engine

def run_sql(sql: str, params: dict | None = None) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        rows = result.fetchall()
        cols = list(result.keys())
    return {
        "columns": cols,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
    }
