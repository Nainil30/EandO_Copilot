# scripts/load_to_postgres.py
import os

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql://copilot_user:dev_password@localhost:5432/eando_copilot")
# psycopg2 wants a libpq URL, not the SQLAlchemy dialect form
DB_URL = DB_URL.replace("postgresql+psycopg2://", "postgresql://")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TABLES_IN_ORDER = [
    ("dim_platform", "data/dim_platform.csv"),
    ("dim_supplier", "data/dim_supplier.csv"),
    ("dim_part", "data/dim_part.csv"),
    ("fact_bom", "data/fact_bom.csv"),
    ("fact_inventory", "data/fact_inventory.csv"),
    ("fact_forecast", "data/fact_forecast.csv"),
    ("fact_ltb_orders", "data/fact_ltb_orders.csv"),
    ("fact_excess_calculation", "data/fact_excess_calculation.csv"),
    ("fact_scrap_approval", "data/fact_scrap_approval.csv"),
]

# Clear tables so you can re-run Day 3 safely
TRUNCATE_SQL = """
TRUNCATE TABLE
  fact_scrap_approval,
  fact_excess_calculation,
  fact_ltb_orders,
  fact_forecast,
  fact_inventory,
  fact_bom,
  dim_part,
  dim_supplier,
  dim_platform
RESTART IDENTITY CASCADE;
"""


DATE_HINTS = ("date", "week", "effective_")


def clean_df_for_postgres(df: pd.DataFrame) -> pd.DataFrame:
    """Parse date-like columns (by name) so they load as DATE, not text/float."""
    for c in df.columns:
        if any(h in c.lower() for h in DATE_HINTS):
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def to_db_value(v):
    """Convert a pandas/numpy cell to a plain Python value psycopg2 can adapt.

    Done per value (not with df.where) because NaN/NaT handling differs across
    pandas versions: pandas 3 keeps NaN in float columns.
    """
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, np.generic):
        return v.item()
    return v


def insert_df(cur, table: str, df: pd.DataFrame) -> None:
    cols = list(df.columns)
    values = [tuple(to_db_value(v) for v in row) for row in df.itertuples(index=False, name=None)]
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES %s"
    execute_values(cur, sql, values, page_size=2000)


def main():
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            print("Truncating tables...")
            cur.execute(TRUNCATE_SQL)
            conn.commit()

            for table, csv_path in TABLES_IN_ORDER:
                print(f"Loading {table} from {csv_path} ...")
                if not os.path.exists(os.path.join(ROOT, csv_path)):
                    raise FileNotFoundError(f"Missing file: {csv_path}. Run scripts/generate_data.py first.")

                df = pd.read_csv(os.path.join(ROOT, csv_path))
                df = clean_df_for_postgres(df)

                insert_df(cur, table, df)
                conn.commit()
                print(f"  ✅ inserted {len(df)} rows")

        print("All data loaded.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
