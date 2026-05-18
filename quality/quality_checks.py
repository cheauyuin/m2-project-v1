"""
Data quality checks for the Olist warehouse.
Validates nulls, duplicates, referential integrity, and business logic.
Run from project root: python3 quality/quality_checks.py
"""

import duckdb
from dataclasses import dataclass
from typing import Callable

DB_PATH = "db/olist.duckdb"


@dataclass
class Check:
    name: str
    sql: str
    expect_zero: bool = True
    description: str = ""


CHECKS = [
    # ── Nulls on required keys ───────────────────────────────────────────
    Check("no_null_order_id",
          "SELECT COUNT(*) FROM warehouse.fact_orders WHERE order_id IS NULL",
          description="fact_orders.order_id must never be null"),

    Check("no_null_customer_key",
          "SELECT COUNT(*) FROM warehouse.fact_orders WHERE customer_key IS NULL",
          description="Every order must link to a customer"),

    Check("no_null_product_key",
          "SELECT COUNT(*) FROM warehouse.fact_order_items WHERE product_key IS NULL",
          description="Every item must link to a product"),

    Check("no_null_purchase_date",
          "SELECT COUNT(*) FROM warehouse.fact_orders WHERE purchased_at IS NULL",
          description="All orders must have a purchase timestamp"),

    # ── Duplicates ───────────────────────────────────────────────────────
    Check("no_duplicate_orders",
          """SELECT COUNT(*) FROM (
              SELECT order_id, COUNT(*) AS n FROM warehouse.fact_orders
              GROUP BY order_id HAVING n > 1
          )""",
          description="Each order_id must appear exactly once in fact_orders"),

    Check("no_duplicate_customers",
          """SELECT COUNT(*) FROM (
              SELECT customer_key, COUNT(*) AS n FROM warehouse.dim_customers
              GROUP BY customer_key HAVING n > 1
          )""",
          description="Each customer_unique_id must appear once in dim_customers"),

    Check("no_duplicate_products",
          """SELECT COUNT(*) FROM (
              SELECT product_key, COUNT(*) AS n FROM warehouse.dim_products
              GROUP BY product_key HAVING n > 1
          )""",
          description="Each product_id must appear once in dim_products"),

    # ── Referential integrity ────────────────────────────────────────────
    Check("items_orders_fk",
          """SELECT COUNT(*) FROM warehouse.fact_order_items fi
             LEFT JOIN warehouse.fact_orders fo USING (order_id)
             WHERE fo.order_id IS NULL""",
          description="All order items must reference a valid order"),

    Check("items_product_fk",
          """SELECT COUNT(*) FROM warehouse.fact_order_items fi
             LEFT JOIN warehouse.dim_products dp USING (product_key)
             WHERE dp.product_key IS NULL""",
          description="All order items must reference a valid product"),

    Check("items_seller_fk",
          """SELECT COUNT(*) FROM warehouse.fact_order_items fi
             LEFT JOIN warehouse.dim_sellers ds USING (seller_key)
             WHERE ds.seller_key IS NULL""",
          description="All order items must reference a valid seller"),

    Check("orders_date_fk",
          """SELECT COUNT(*) FROM warehouse.fact_orders fo
             LEFT JOIN warehouse.dim_date dd ON fo.purchase_date_key = dd.date_key
             WHERE fo.purchase_date_key IS NOT NULL AND dd.date_key IS NULL""",
          description="All purchase dates must exist in dim_date"),

    # ── Business logic ───────────────────────────────────────────────────
    Check("no_negative_price",
          "SELECT COUNT(*) FROM warehouse.fact_order_items WHERE price < 0",
          description="Item prices must be non-negative"),

    Check("no_negative_freight",
          "SELECT COUNT(*) FROM warehouse.fact_order_items WHERE freight_value < 0",
          description="Freight values must be non-negative"),

    Check("no_negative_payment",
          "SELECT COUNT(*) FROM warehouse.fact_orders WHERE total_payment < 0",
          description="Order payments must be non-negative"),

    Check("review_score_range",
          """SELECT COUNT(*) FROM warehouse.fact_orders
             WHERE avg_review_score IS NOT NULL
               AND (avg_review_score < 1 OR avg_review_score > 5)""",
          description="Review scores must be between 1 and 5"),

    Check("freight_ratio_range",
          """SELECT COUNT(*) FROM warehouse.fact_order_items
             WHERE freight_ratio IS NOT NULL
               AND (freight_ratio < 0 OR freight_ratio > 1)""",
          description="Freight ratio must be between 0 and 1"),

    Check("delivery_before_purchase",
          """SELECT COUNT(*) FROM warehouse.fact_orders
             WHERE delivered_at IS NOT NULL
               AND delivered_at < purchased_at""",
          description="Delivery date cannot be before purchase date"),
]


def run(db_path: str = DB_PATH):
    print("=== Data Quality Checks ===\n")
    con = duckdb.connect(db_path)

    passed = 0
    failed = 0
    results = []

    for check in CHECKS:
        count = con.execute(check.sql).fetchone()[0]
        ok = (count == 0) if check.expect_zero else (count > 0)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        results.append((status, check.name, count, check.description))

    con.close()

    col_w = max(len(r[1]) for r in results) + 2
    print(f"{'Status':<6}  {'Check':<{col_w}}  {'Count':>7}  Description")
    print("-" * (6 + 2 + col_w + 2 + 7 + 2 + 50))
    for status, name, count, desc in results:
        marker = "✓" if status == "PASS" else "✗"
        print(f"{marker} {status:<5}  {name:<{col_w}}  {count:>7}  {desc}")

    print(f"\nSummary: {passed} passed, {failed} failed out of {len(CHECKS)} checks.")
    return failed == 0


if __name__ == "__main__":
    ok = run()
    exit(0 if ok else 1)
