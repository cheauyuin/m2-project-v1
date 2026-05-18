"""
Ingestion script: loads raw tables from BigQuery into DuckDB staging schema,
then builds warehouse star schema (dims + facts).
Requires: gcloud auth application-default login
Run from project root: python3 ingestion/ingest.py
"""

import duckdb
import pathlib

DB_PATH = "db/olist.duckdb"
SQL_PATH = "warehouse/schema.sql"
BQ_PROJECT = "dsai-module-2-project-496708"


def run(db_path: str = DB_PATH):
    print("=== Olist Ingestion Pipeline ===\n")

    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(db_path)

    print("Setting up BigQuery connection...")
    con.execute("INSTALL bigquery FROM community")
    con.execute("LOAD bigquery")
    con.execute(f"ATTACH 'project={BQ_PROJECT}' AS bq (TYPE bigquery, READ_ONLY)")
    print(f"  Connected to BigQuery project: {BQ_PROJECT}\n")

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
