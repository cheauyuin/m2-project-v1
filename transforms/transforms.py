"""
ELT Transform layer: reads from BigQuery olist_warehouse, builds analytical mart tables
in BigQuery olist_marts. Includes customer RFM, sales trends, delivery metrics,
data quality assessment, outlier detection, and risk register.
Requires: gcloud auth application-default login
Run from project root: python3 transforms/transforms.py
"""

import duckdb
from google.cloud import bigquery

BQ_PROJECT = "dsai-module-2-project-496708"
BQ_WAREHOUSE = "olist_warehouse"
BQ_MARTS = "olist_marts"

W = f"bq.{BQ_WAREHOUSE}"
M = f"bq.{BQ_MARTS}"


def create_marts_dataset():
    client = bigquery.Client(project=BQ_PROJECT)
    dataset_ref = bigquery.Dataset(f"{BQ_PROJECT}.{BQ_MARTS}")
    dataset_ref.location = "US"
    client.create_dataset(dataset_ref, exists_ok=True)
    print(f"  Dataset {BQ_PROJECT}.{BQ_MARTS} ready.\n")


TRANSFORMS = {

    # ── Customer RFM + CLV mart ─────────────────────────────────────────────
    "customer_rfm": f"""
        CREATE OR REPLACE TABLE {M}.customer_rfm AS
        WITH snapshot_date AS (
            SELECT MAX(purchased_at)::DATE + INTERVAL 1 DAY AS ref_date
            FROM {W}.fact_orders
        ),
        customer_orders AS (
            SELECT
                fo.customer_key,
                dc.city,
                dc.state,
                dc.lat,
                dc.lng,
                COUNT(DISTINCT fo.order_id)::BIGINT                 AS frequency,
                SUM(fo.total_payment)                               AS monetary,
                MAX(fo.purchased_at::DATE)                          AS last_order_date,
                MIN(fo.purchased_at::DATE)                          AS first_order_date,
                (SELECT ref_date FROM snapshot_date)
                    - MAX(fo.purchased_at::DATE)                    AS recency_days,
                AVG(fo.avg_review_score)                            AS avg_review_score,
                AVG(fo.delivery_delay_days)                         AS avg_delivery_delay,
                COUNT(CASE WHEN fo.order_status = 'delivered' THEN 1 END) AS delivered_count
            FROM {W}.fact_orders fo
            JOIN {W}.dim_customers dc USING (customer_key)
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
            ROUND(
                monetary * frequency
                / NULLIF(
                    DATEDIFF('month', first_order_date, last_order_date) + 1,
                    0
                ) * 12, 2
            )                                                  AS annualised_clv
        FROM rfm_scores;
    """,

    # ── Monthly sales trends ─────────────────────────────────────────────────
    "monthly_sales": f"""
        CREATE OR REPLACE TABLE {M}.monthly_sales AS
        SELECT
            dd.year,
            dd.month,
            dd.month_name,
            DATE_TRUNC('month', fo.purchased_at)::DATE          AS month_start,
            COUNT(DISTINCT fo.order_id)::BIGINT                 AS total_orders,
            COUNT(DISTINCT fo.customer_key)::BIGINT             AS unique_customers,
            ROUND(SUM(fo.total_payment), 2)                     AS total_revenue,
            ROUND(AVG(fo.total_payment), 2)                     AS avg_order_value,
            ROUND(AVG(fo.avg_review_score), 2)                  AS avg_review_score,
            SUM(CASE WHEN fo.delivery_delay_days > 0 THEN 1 ELSE 0 END)::BIGINT AS late_deliveries,
            COUNT(CASE WHEN fo.order_status = 'delivered' THEN 1 END)   AS delivered_orders
        FROM {W}.fact_orders fo
        JOIN {W}.dim_date dd ON fo.purchase_date_key = dd.date_key
        WHERE fo.order_status NOT IN ('canceled', 'unavailable')
          AND dd.year BETWEEN 2017 AND 2018
        GROUP BY dd.year, dd.month, dd.month_name, DATE_TRUNC('month', fo.purchased_at)::DATE
        ORDER BY month_start;
    """,

    # ── Product category performance ─────────────────────────────────────────
    "category_performance": f"""
        CREATE OR REPLACE TABLE {M}.category_performance AS
        SELECT
            dp.category,
            COUNT(DISTINCT fi.order_id)::BIGINT                 AS total_orders,
            COUNT(fi.order_item_id)::BIGINT                     AS units_sold,
            ROUND(SUM(fi.price), 2)                             AS total_revenue,
            ROUND(AVG(fi.price), 2)                             AS avg_price,
            ROUND(AVG(fi.freight_ratio), 3)                     AS avg_freight_ratio,
            ROUND(AVG(fi.avg_review_score), 2)                  AS avg_review_score,
            ROUND(AVG(fi.delivery_delay_days), 2)               AS avg_delivery_delay,
            COUNT(DISTINCT fi.seller_key)::BIGINT               AS seller_count
        FROM {W}.fact_order_items fi
        JOIN {W}.dim_products dp USING (product_key)
        WHERE fi.order_status NOT IN ('canceled', 'unavailable')
        GROUP BY dp.category
        ORDER BY total_revenue DESC;
    """,

    # ── Delivery performance by state ────────────────────────────────────────
    "delivery_by_state": f"""
        CREATE OR REPLACE TABLE {M}.delivery_by_state AS
        SELECT
            dc.state                                            AS customer_state,
            COUNT(DISTINCT fo.order_id)::BIGINT                 AS total_orders,
            ROUND(AVG(fo.delivery_delay_days), 2)               AS avg_delay_days,
            SUM(CASE WHEN fo.delivery_delay_days > 0 THEN 1 ELSE 0 END)::BIGINT AS late_count,
            ROUND(
                100.0 * SUM(CASE WHEN fo.delivery_delay_days > 0 THEN 1 ELSE 0 END)::BIGINT
                / NULLIF(COUNT(*), 0), 1
            )                                                   AS late_pct,
            ROUND(AVG(fo.avg_review_score), 2)                  AS avg_review_score,
            ROUND(AVG(fi.freight_ratio), 3)                     AS avg_freight_ratio,
            ROUND(AVG(fi.freight_value), 2)                     AS avg_freight_value
        FROM {W}.fact_orders fo
        JOIN {W}.dim_customers dc USING (customer_key)
        JOIN {W}.fact_order_items fi USING (order_id)
        WHERE fo.order_status = 'delivered'
        GROUP BY dc.state
        ORDER BY total_orders DESC;
    """,

    # ── Review score vs delivery delay buckets ───────────────────────────────
    "satisfaction_vs_delivery": f"""
        CREATE OR REPLACE TABLE {M}.satisfaction_vs_delivery AS
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
        FROM {W}.fact_orders fo
        WHERE fo.order_status = 'delivered'
          AND fo.avg_review_score IS NOT NULL
          AND fo.delivery_delay_days IS NOT NULL
        GROUP BY review_score, delivery_bucket
        ORDER BY review_score, delivery_bucket;
    """,

    # ── Data Quality Summary ─────────────────────────────────────────────────
    "data_quality": f"""
        CREATE OR REPLACE TABLE {M}.data_quality AS
        WITH order_stats AS (
            SELECT
                'fact_orders'                                   AS table_name,
                COUNT(*)                                        AS total_rows,
                COUNT(CASE WHEN order_status IN ('canceled','unavailable') THEN 1 END)
                                                                AS excluded_status,
                COUNT(CASE WHEN total_payment = 0 THEN 1 END)  AS zero_payment,
                COUNT(CASE WHEN avg_review_score IS NULL THEN 1 END)
                                                                AS missing_review,
                COUNT(CASE WHEN delivery_delay_days IS NULL THEN 1 END)
                                                                AS missing_delay,
                COUNT(CASE WHEN delivered_at IS NULL
                    AND order_status = 'delivered' THEN 1 END)  AS missing_delivery_ts,
                COUNT(CASE WHEN order_status = 'delivered' THEN 1 END)
                                                                AS clean_delivered
            FROM {W}.fact_orders
        ),
        item_stats AS (
            SELECT
                'fact_order_items'                              AS table_name,
                COUNT(*)                                        AS total_rows,
                COUNT(CASE WHEN order_status IN ('canceled','unavailable') THEN 1 END)
                                                                AS excluded_status,
                COUNT(CASE WHEN price = 0 THEN 1 END)           AS zero_payment,
                0                                               AS missing_review,
                0                                               AS missing_delay,
                0                                               AS missing_delivery_ts,
                COUNT(CASE WHEN order_status = 'delivered' THEN 1 END)
                                                                AS clean_delivered
            FROM {W}.fact_order_items
        )
        SELECT * FROM order_stats
        UNION ALL
        SELECT * FROM item_stats;
    """,

    # ── Payment Outliers (IQR method) ────────────────────────────────────────
    "payment_outliers": f"""
        CREATE OR REPLACE TABLE {M}.payment_outliers AS
        WITH delivered AS (
            SELECT order_id, total_payment, customer_key, purchased_at
            FROM {W}.fact_orders
            WHERE order_status = 'delivered' AND total_payment > 0
        ),
        stats AS (
            SELECT
                quantile_cont(total_payment, 0.25)              AS q1,
                quantile_cont(total_payment, 0.75)              AS q3,
                AVG(total_payment)                              AS mean_payment,
                STDDEV(total_payment)                           AS std_payment,
                COUNT(*)                                        AS total_orders
            FROM delivered
        ),
        bounds AS (
            SELECT *,
                q3 - q1                                         AS iqr,
                q1 - 1.5 * (q3 - q1)                           AS lower_fence,
                q3 + 1.5 * (q3 - q1)                           AS upper_fence
            FROM stats
        )
        SELECT
            d.order_id,
            d.total_payment,
            d.customer_key,
            d.purchased_at,
            ROUND(b.q1, 2)                                      AS q1,
            ROUND(b.q3, 2)                                      AS q3,
            ROUND(b.iqr, 2)                                     AS iqr,
            ROUND(b.lower_fence, 2)                             AS lower_fence,
            ROUND(b.upper_fence, 2)                             AS upper_fence,
            ROUND(b.mean_payment, 2)                            AS mean_payment,
            ROUND(b.std_payment, 2)                             AS std_payment,
            b.total_orders,
            CASE
                WHEN d.total_payment > b.upper_fence            THEN 'high_outlier'
                WHEN d.total_payment < b.lower_fence            THEN 'low_outlier'
                ELSE 'normal'
            END                                                 AS outlier_flag,
            ROUND((d.total_payment - b.mean_payment)
                / NULLIF(b.std_payment, 0), 2)                  AS z_score
        FROM delivered d
        CROSS JOIN bounds b
        WHERE d.total_payment > b.upper_fence
           OR d.total_payment < b.lower_fence;
    """,

    # ── Delivery Delay Outliers (IQR method) ─────────────────────────────────
    "delivery_outliers": f"""
        CREATE OR REPLACE TABLE {M}.delivery_outliers AS
        WITH delivered AS (
            SELECT order_id, delivery_delay_days, customer_key, purchased_at,
                   avg_review_score
            FROM {W}.fact_orders
            WHERE order_status = 'delivered' AND delivery_delay_days IS NOT NULL
        ),
        stats AS (
            SELECT
                quantile_cont(delivery_delay_days, 0.25)        AS q1,
                quantile_cont(delivery_delay_days, 0.75)        AS q3,
                AVG(delivery_delay_days)                        AS mean_delay,
                STDDEV(delivery_delay_days)                     AS std_delay
            FROM delivered
        ),
        bounds AS (
            SELECT *,
                q3 - q1                                         AS iqr,
                q1 - 1.5 * (q3 - q1)                           AS lower_fence,
                q3 + 1.5 * (q3 - q1)                           AS upper_fence
            FROM stats
        )
        SELECT
            d.order_id,
            d.delivery_delay_days,
            d.customer_key,
            d.purchased_at,
            d.avg_review_score,
            ROUND(b.lower_fence, 2)                             AS lower_fence,
            ROUND(b.upper_fence, 2)                             AS upper_fence,
            CASE
                WHEN d.delivery_delay_days > b.upper_fence      THEN 'extreme_late'
                WHEN d.delivery_delay_days < b.lower_fence      THEN 'extreme_early'
            END                                                 AS outlier_flag,
            ROUND((d.delivery_delay_days - b.mean_delay)
                / NULLIF(b.std_delay, 0), 2)                    AS z_score
        FROM delivered d
        CROSS JOIN bounds b
        WHERE d.delivery_delay_days > b.upper_fence
           OR d.delivery_delay_days < b.lower_fence;
    """,

    # ── Risk Register ────────────────────────────────────────────────────────
    # Depends on: customer_rfm, delivery_by_state, payment_outliers
    "risk_register": f"""
        CREATE OR REPLACE TABLE {M}.risk_register AS
        WITH
        churn AS (
            SELECT
                'Customer Churn'                                AS risk_category,
                'High proportion of one-time buyers (97%) with valuable at-risk segments'
                                                                AS description,
                COUNT(CASE WHEN segment IN ('At Risk','Cant Lose') THEN 1 END)
                                                                AS affected_count,
                ROUND(100.0 * COUNT(CASE WHEN segment IN ('At Risk','Cant Lose') THEN 1 END)
                    / COUNT(*), 1)                              AS affected_pct,
                ROUND(SUM(CASE WHEN segment IN ('At Risk','Cant Lose')
                    THEN monetary ELSE 0 END), 0)               AS revenue_at_risk,
                'High'                                          AS likelihood,
                'High'                                          AS impact,
                'Loyalty programme, personalised re-engagement, subscription bundles'
                                                                AS mitigation
            FROM {M}.customer_rfm
        ),
        delivery AS (
            SELECT
                'Delivery Failure'                              AS risk_category,
                'Late deliveries correlated with 1-star reviews; concentrated in non-SP states'
                                                                AS description,
                SUM(late_count)::BIGINT                         AS affected_count,
                ROUND(AVG(late_pct), 1)                         AS affected_pct,
                NULL                                            AS revenue_at_risk,
                'Medium'                                        AS likelihood,
                'High'                                          AS impact,
                'Realistic ETA communication, regional carrier diversification'
                                                                AS mitigation
            FROM {M}.delivery_by_state
        ),
        geo AS (
            SELECT
                'Geographic Concentration'                      AS risk_category,
                'Seller base heavily concentrated in Sao Paulo; underserved states face high freight'
                                                                AS description,
                COUNT(CASE WHEN customer_state != 'SP' THEN 1 END)
                                                                AS affected_count,
                ROUND(100.0 * COUNT(CASE WHEN customer_state != 'SP' THEN 1 END)
                    / COUNT(*), 1)                              AS affected_pct,
                NULL                                            AS revenue_at_risk,
                'Medium'                                        AS likelihood,
                'Medium'                                        AS impact,
                'Recruit sellers in AM, RR, AP, PA to reduce freight disparity'
                                                                AS mitigation
            FROM {M}.delivery_by_state
        ),
        payment AS (
            SELECT
                'Payment Anomaly'                               AS risk_category,
                'Extreme payment values detected via IQR — potential fraud or data error'
                                                                AS description,
                COUNT(*)                                        AS affected_count,
                NULL                                            AS affected_pct,
                ROUND(SUM(CASE WHEN outlier_flag = 'high_outlier'
                    THEN total_payment ELSE 0 END), 0)          AS revenue_at_risk,
                'Low'                                           AS likelihood,
                'Medium'                                        AS impact,
                'Payment value caps, automated fraud rules, manual review queue'
                                                                AS mitigation
            FROM {M}.payment_outliers
        )
        SELECT * FROM churn
        UNION ALL SELECT * FROM delivery
        UNION ALL SELECT * FROM geo
        UNION ALL SELECT * FROM payment;
    """,
}


def run():
    print("=== ELT Transform Layer ===\n")

    print("Creating BigQuery marts dataset...")
    create_marts_dataset()

    con = duckdb.connect()
    con.execute("INSTALL bigquery FROM community")
    con.execute("LOAD bigquery")
    con.execute(f"ATTACH 'project={BQ_PROJECT}' AS bq (TYPE bigquery)")
    print(f"  Connected to BigQuery project: {BQ_PROJECT}\n")

    for name, sql in TRANSFORMS.items():
        print(f"Building {BQ_MARTS}.{name}...")
        con.execute(sql)
        n = con.execute(f"SELECT COUNT(*) FROM bq.{BQ_MARTS}.{name}").fetchone()[0]
        print(f"  -> {n:,} rows\n")

    con.close()
    print("All transforms complete.")


if __name__ == "__main__":
    run()
