"""
Ingestion script: reads from BigQuery olist_raw, transforms via DuckDB in-memory,
writes warehouse tables to BigQuery olist_warehouse.
Requires: gcloud auth application-default login
Run from project root: python3 ingestion/ingest.py
"""

import duckdb
import pathlib
from google.cloud import bigquery

BQ_PROJECT = "dsai-module-2-project-496708"
BQ_WAREHOUSE_DATASET = "olist_warehouse"
SQL_PATH = "warehouse/schema.sql"


def create_warehouse_dataset():
    client = bigquery.Client(project=BQ_PROJECT)
    dataset_ref = bigquery.Dataset(f"{BQ_PROJECT}.{BQ_WAREHOUSE_DATASET}")
    dataset_ref.location = "US"
    client.create_dataset(dataset_ref, exists_ok=True)
    print(f"  Dataset {BQ_PROJECT}.{BQ_WAREHOUSE_DATASET} ready.\n")


def run():
    print("=== Olist Ingestion Pipeline ===\n")

    print("Creating BigQuery warehouse dataset...")
    create_warehouse_dataset()

    con = duckdb.connect()

    print("Setting up BigQuery connection...")
    con.execute("INSTALL bigquery FROM community")
    con.execute("LOAD bigquery")
    con.execute(f"ATTACH 'project={BQ_PROJECT}' AS bq (TYPE bigquery)")
    print(f"  Connected to BigQuery project: {BQ_PROJECT}\n")

    print("Running transformations and writing to BigQuery...")
    sql = pathlib.Path(SQL_PATH).read_text()
    con.execute(sql)
    print("  Done.\n")

    print("Row counts:")
    tables = ["dim_date", "dim_customers", "dim_sellers", "dim_products",
              "fact_orders", "fact_order_items"]
    for tbl in tables:
        n = con.execute(f"SELECT COUNT(*) FROM bq.{BQ_WAREHOUSE_DATASET}.{tbl}").fetchone()[0]
        print(f"  {BQ_WAREHOUSE_DATASET}.{tbl}: {n:,}")

    con.close()
    print(f"\nDone. Warehouse written to BigQuery: {BQ_PROJECT}.{BQ_WAREHOUSE_DATASET}")


if __name__ == "__main__":
    run()
