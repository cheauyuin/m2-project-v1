"""
ELT Transform layer: builds analytical mart tables on top of the warehouse.
Adds RFM scores, CLV, delivery metrics, and monthly sales aggregates.
Run from project root: python3 transforms/transforms.py
"""

import duckdb

DB_PATH = "db/olist.duckdb"


TRANSFORMS = {
    # ── Customer RFM + CLV mart ───────────────────────────────────────────
    "mart.customer_rfm": """
        CREATE SCHEMA IF NOT EXISTS mart;

        CREATE OR REPLACE TABLE mart.customer_rfm AS
        WITH snapshot_date AS (
            SELECT MAX(purchased_at)::DATE + INTERVAL 1 DAY AS ref_date
            FROM warehouse.fact_orders
        ),
        customer_orders AS (
            SELECT
                fo.customer_key,
                dc.city,
                dc.state,
                dc.lat,
                dc.lng,
                COUNT(DISTINCT fo.order_id)                         AS frequency,
                SUM(fo.total_payment)                               AS monetary,
                MAX(fo.purchased_at::DATE)                          AS last_order_date,
                MIN(fo.purchased_at::DATE)                          AS first_order_date,
                (SELECT ref_date FROM snapshot_date)
                    - MAX(fo.purchased_at::DATE)                    AS recency_days,
                AVG(fo.avg_review_score)                            AS avg_review_score,
                AVG(fo.delivery_delay_days)                         AS avg_delivery_delay,
                COUNT(CASE WHEN fo.order_status = 'delivered' THEN 1 END) AS delivered_count
            FROM warehouse.fact_orders fo
            JOIN warehouse.dim_customers dc USING (customer_key)
            WHERE fo.order_status NOT IN ('canceled', 'unavailable')
            GROUP BY fo.customer_key, dc.city, dc.state, dc.lat, dc.lng
        ),
        rfm_scores AS (
            SELECT *,
                NTILE(5) OVER (ORDER BY recency_days ASC)  AS r_score,
                NTILE(5) OVER (ORDER BY frequency   DESC)  AS f_score,
                NTILE(5) OVER (ORDER BY monetary    DESC)  AS m_score
            FROM customer_orders
        )
        SELECT *,
            ROUND((r_score + f_score + m_score) / 3.0, 2)     AS rfm_score,
            CASE
                WHEN r_score >= 4 AND f_score >= 4             THEN 'Champion'
                WHEN r_score >= 3 AND f_score >= 3             THEN 'Loyal'
                WHEN r_score >= 4 AND f_score <= 2             THEN 'Promising'
                WHEN r_score <= 2 AND f_score >= 3             THEN 'At Risk'
                WHEN r_score <= 2 AND f_score <= 2
                     AND monetary >= 200                       THEN 'Cant Lose'
                WHEN r_score <= 1                              THEN 'Lost'
                ELSE 'Needs Attention'
            END                                                AS segment,
            -- simple CLV: monetary * (frequency / months active) * 12
            ROUND(
                monetary * frequency
                / NULLIF(
                    DATEDIFF('month', first_order_date, last_order_date) + 1,
                    0
                ) * 12, 2
            )                                                  AS annualised_clv
        FROM rfm_scores;
    """,

    # ── Monthly sales trends ─────────────────────────────────────────────
    "mart.monthly_sales": """
        CREATE SCHEMA IF NOT EXISTS mart;

        CREATE OR REPLACE TABLE mart.monthly_sales AS
        SELECT
            dd.year,
            dd.month,
            dd.month_name,
            DATE_TRUNC('month', fo.purchased_at)::DATE          AS month_start,
            COUNT(DISTINCT fo.order_id)                         AS total_orders,
            COUNT(DISTINCT fo.customer_key)                     AS unique_customers,
            ROUND(SUM(fo.total_payment), 2)                     AS total_revenue,
            ROUND(AVG(fo.total_payment), 2)                     AS avg_order_value,
            ROUND(AVG(fo.avg_review_score), 2)                  AS avg_review_score,
            SUM(CASE WHEN fo.delivery_delay_days > 0 THEN 1 ELSE 0 END) AS late_deliveries,
            COUNT(CASE WHEN fo.order_status = 'delivered' THEN 1 END)   AS delivered_orders
        FROM warehouse.fact_orders fo
        JOIN warehouse.dim_date dd ON fo.purchase_date_key = dd.date_key
        WHERE fo.order_status NOT IN ('canceled', 'unavailable')
          AND dd.year BETWEEN 2017 AND 2018
        GROUP BY dd.year, dd.month, dd.month_name, DATE_TRUNC('month', fo.purchased_at)::DATE
        ORDER BY month_start;
    """,

    # ── Product category performance ─────────────────────────────────────
    "mart.category_performance": """
        CREATE SCHEMA IF NOT EXISTS mart;

        CREATE OR REPLACE TABLE mart.category_performance AS
        SELECT
            dp.category,
            COUNT(DISTINCT fi.order_id)                         AS total_orders,
            COUNT(fi.order_item_id)                             AS units_sold,
            ROUND(SUM(fi.price), 2)                             AS total_revenue,
            ROUND(AVG(fi.price), 2)                             AS avg_price,
            ROUND(AVG(fi.freight_ratio), 3)                     AS avg_freight_ratio,
            ROUND(AVG(fi.avg_review_score), 2)                  AS avg_review_score,
            ROUND(AVG(fi.delivery_delay_days), 2)               AS avg_delivery_delay,
            COUNT(DISTINCT fi.seller_key)                       AS seller_count
        FROM warehouse.fact_order_items fi
        JOIN warehouse.dim_products dp USING (product_key)
        WHERE fi.order_status NOT IN ('canceled', 'unavailable')
        GROUP BY dp.category
        ORDER BY total_revenue DESC;
    """,

    # ── Delivery performance by state ───────────────────────────────────
    "mart.delivery_by_state": """
        CREATE SCHEMA IF NOT EXISTS mart;

        CREATE OR REPLACE TABLE mart.delivery_by_state AS
        SELECT
            dc.state                                            AS customer_state,
            COUNT(DISTINCT fo.order_id)                         AS total_orders,
            ROUND(AVG(fo.delivery_delay_days), 2)               AS avg_delay_days,
            SUM(CASE WHEN fo.delivery_delay_days > 0 THEN 1 ELSE 0 END) AS late_count,
            ROUND(
                100.0 * SUM(CASE WHEN fo.delivery_delay_days > 0 THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0), 1
            )                                                   AS late_pct,
            ROUND(AVG(fo.avg_review_score), 2)                  AS avg_review_score,
            ROUND(AVG(fi.freight_ratio), 3)                     AS avg_freight_ratio,
            ROUND(AVG(fi.freight_value), 2)                     AS avg_freight_value
        FROM warehouse.fact_orders fo
        JOIN warehouse.dim_customers dc USING (customer_key)
        JOIN warehouse.fact_order_items fi USING (order_id)
        WHERE fo.order_status = 'delivered'
        GROUP BY dc.state
        ORDER BY total_orders DESC;
    """,

    # ── Review score vs delivery delay buckets ──────────────────────────
    "mart.satisfaction_vs_delivery": """
        CREATE SCHEMA IF NOT EXISTS mart;

        CREATE OR REPLACE TABLE mart.satisfaction_vs_delivery AS
        SELECT
            fo.avg_review_score::INTEGER                        AS review_score,
            CASE
                WHEN fo.delivery_delay_days <= -14 THEN 'Very Early (>14d)'
                WHEN fo.delivery_delay_days <= -7  THEN 'Early (7-14d)'
                WHEN fo.delivery_delay_days <= 0   THEN 'On Time (0-7d early)'
                WHEN fo.delivery_delay_days <= 7   THEN 'Late (1-7d)'
                ELSE 'Very Late (>7d)'
            END                                                 AS delivery_bucket,
            COUNT(*)                                            AS order_count,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER
                (PARTITION BY fo.avg_review_score::INTEGER), 1) AS pct_of_score
        FROM warehouse.fact_orders fo
        WHERE fo.order_status = 'delivered'
          AND fo.avg_review_score IS NOT NULL
          AND fo.delivery_delay_days IS NOT NULL
        GROUP BY review_score, delivery_bucket
        ORDER BY review_score, delivery_bucket;
    """,
}


def run(db_path: str = DB_PATH):
    print("=== ELT Transform Layer ===\n")
    con = duckdb.connect(db_path)

    for name, sql in TRANSFORMS.items():
        print(f"Building {name}...")
        con.execute(sql)
        n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"  -> {n:,} rows\n")

    con.close()
    print("All transforms complete.")


if __name__ == "__main__":
    run()
