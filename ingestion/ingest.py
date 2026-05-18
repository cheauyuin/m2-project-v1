"""
Ingestion script: loads raw CSVs into DuckDB staging schema,
then builds warehouse star schema (dims + facts).
Run from project root: python3 ingestion/ingest.py
"""

import duckdb
import pathlib
import sys

DB_PATH = "db/olist.duckdb"
SQL_PATH = "warehouse/schema.sql"
DATA_DIR = "data"

EXPECTED_FILES = [
    "olist_customers_dataset.csv",
    "olist_geolocation_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "product_category_name_translation.csv",
]


def check_data_files():
    missing = [f for f in EXPECTED_FILES if not pathlib.Path(DATA_DIR, f).exists()]
    if missing:
        print(f"ERROR: Missing data files: {missing}")
        sys.exit(1)
    print(f"  All {len(EXPECTED_FILES)} CSV files found.")


def run(db_path: str = DB_PATH):
    print("=== Olist Ingestion Pipeline ===\n")

    check_data_files()

    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(db_path)

    print("Loading schema SQL...")
    sql = pathlib.Path(SQL_PATH).read_text()
    con.execute(sql)
    print("  Schema applied.\n")

    print("Row counts:")
    tables = {
        "staging":   ["orders", "order_items", "order_payments", "order_reviews",
                      "customers", "sellers", "products", "geolocation", "category_translation"],
        "warehouse": ["dim_date", "dim_customers", "dim_sellers", "dim_products",
                      "fact_orders", "fact_order_items"],
    }
    for schema, tbl_list in tables.items():
        for tbl in tbl_list:
            n = con.execute(f"SELECT COUNT(*) FROM {schema}.{tbl}").fetchone()[0]
            print(f"  {schema}.{tbl}: {n:,}")

    con.close()
    print(f"\nDone. Database written to: {db_path}")


if __name__ == "__main__":
    run()
