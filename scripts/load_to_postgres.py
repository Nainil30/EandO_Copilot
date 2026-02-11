# scripts/load_to_postgres.py
import os
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
assert DB_URL, "DATABASE_URL missing in .env"

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

def clean_df_for_postgres(df: pd.DataFrame) -> pd.DataFrame:
    """
    1) Convert date-like columns to Python date objects (or None)
    2) Replace pandas NaN/NaT with None so psycopg2 inserts NULL
    """
    # Convert any column with 'date' or 'week' in the name to a python date
    for c in df.columns:
        if "date" in c.lower() or "week" in c.lower():
            s = pd.to_datetime(df[c], errors="coerce")
            df[c] = s.dt.date  # invalid/missing -> NaT -> becomes null-ish

    # Replace NaN/NaT across the dataframe with None (so DB gets NULL)
    df = df.where(pd.notnull(df), None)
    return df

def insert_df(cur, table: str, df: pd.DataFrame) -> None:
    cols = list(df.columns)
    values = [tuple(x) for x in df.to_numpy()]
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
                if not os.path.exists(csv_path):
                    raise FileNotFoundError(f"Missing file: {csv_path}. Run scripts/generate_data.py first.")

                df = pd.read_csv(csv_path)
                df = clean_df_for_postgres(df)

                insert_df(cur, table, df)
                conn.commit()
                print(f"  ✅ inserted {len(df)} rows")

        print("All data loaded.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
