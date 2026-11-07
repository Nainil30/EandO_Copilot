"""One-command setup: schema -> synthetic data -> load -> knowledge base.

    python -m scripts.bootstrap            # full setup (idempotent)
    python -m scripts.bootstrap --kb-only  # just rebuild the vector index

Requires Postgres to be running (docker compose up -d postgres).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def wait_for_db(timeout_s: int = 60) -> None:
    from sqlalchemy import create_engine, text

    from app.config import get_settings

    engine = create_engine(get_settings().sqlalchemy_url)
    deadline = time.time() + timeout_s
    while True:
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except Exception as e:  # noqa: BLE001
            if time.time() > deadline:
                raise SystemExit(f"Postgres not reachable: {e}") from e
            print("  waiting for postgres...")
            time.sleep(2)


def apply_schema() -> None:
    """schema.sql is idempotent (IF NOT EXISTS / DO blocks) so re-running is safe."""
    import psycopg2

    from app.config import get_settings

    dsn = get_settings().sqlalchemy_url.replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb-only", action="store_true")
    ap.add_argument("--regenerate", action="store_true", help="Regenerate CSVs even if present")
    args = ap.parse_args()

    if not args.kb_only:
        print("1/4 waiting for database")
        wait_for_db()
        print("2/4 applying schema + read-only role")
        apply_schema()
        if args.regenerate or not (ROOT / "data" / "dim_part.csv").exists():
            print("3/4 generating synthetic data")
            subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_data.py")], check=True, cwd=ROOT)
        else:
            print("3/4 data/ CSVs already present (use --regenerate to rebuild)")
        print("    loading CSVs into Postgres")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "load_to_postgres.py")], check=True, cwd=ROOT)

    print("4/4 building knowledge base (embeddings + BM25)")
    from app.rag.kb_builder import build_kb

    print("   ", build_kb())
    print("\nDone. Start the API:  uvicorn app.api.main:app --reload")


if __name__ == "__main__":
    main()
