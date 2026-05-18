-- ============================================================
-- Olist Data Warehouse - Star Schema
-- ============================================================

-- ── STAGING TABLES (raw, unmodified) ─────────────────────────

CREATE SCHEMA IF NOT EXISTS staging;

CREATE OR REPLACE TABLE staging.orders AS SELECT * FROM bq.olist_raw.orders;
CREATE OR REPLACE TABLE staging.order_items AS SELECT * FROM bq.olist_raw.order_items;
CREATE OR REPLACE TABLE staging.order_payments AS SELECT * FROM bq.olist_raw.payments;
CREATE OR REPLACE TABLE staging.order_reviews AS SELECT * FROM bq.olist_raw.reviews;
CREATE OR REPLACE TABLE staging.customers AS SELECT * FROM bq.olist_raw.customers;
CREATE OR REPLACE TABLE staging.sellers AS SELECT * FROM bq.olist_raw.sellers;
CREATE OR REPLACE TABLE staging.products AS SELECT * FROM bq.olist_raw.products;
CREATE OR REPLACE TABLE staging.geolocation AS SELECT * FROM bq.olist_raw.geolocation;
CREATE OR REPLACE TABLE staging.category_translation AS SELECT * FROM bq.olist_raw.category_translation;


-- ── DIMENSION TABLES ─────────────────────────────────────────

-- dim_date: calendar spine for all time-based analysis
CREATE OR REPLACE TABLE bq.olist_warehouse.dim_date AS
WITH date_spine AS (
    SELECT CAST(range AS DATE) AS date
    FROM range(DATE '2016-09-01', DATE '2018-11-01', INTERVAL 1 DAY)
)
SELECT
    CAST(strftime(date, '%Y%m%d') AS INTEGER)   AS date_key,
    date,
    EXTRACT(year  FROM date)::INTEGER            AS year,
    EXTRACT(month FROM date)::INTEGER            AS month,
    EXTRACT(day   FROM date)::INTEGER            AS day,
    strftime(date, '%B')                         AS month_name,
    EXTRACT(quarter FROM date)::INTEGER          AS quarter,
    EXTRACT(dayofweek FROM date)::INTEGER        AS day_of_week,
    strftime(date, '%A')                         AS day_name,
    (EXTRACT(dayofweek FROM date) IN (0,6))      AS is_weekend
FROM date_spine;

-- dim_customers: one row per unique customer (customer_unique_id)
-- The source has one customer_id per order, so we deduplicate here,
-- keeping the most recent order's location as the canonical address.
CREATE OR REPLACE TABLE bq.olist_warehouse.dim_customers AS
WITH ranked AS (
    SELECT
        c.customer_unique_id,
        c.customer_id,
        c.customer_zip_code_prefix,
        c.customer_city,
        c.customer_state,
        ROW_NUMBER() OVER (
            PARTITION BY c.customer_unique_id
            ORDER BY c.customer_id DESC
        ) AS rn
    FROM staging.customers c
)
SELECT
    r.customer_unique_id                        AS customer_key,
    r.customer_id                               AS customer_id,
    r.customer_zip_code_prefix                  AS zip_code,
    r.customer_city                             AS city,
    r.customer_state                            AS state,
    g.geolocation_lat                           AS lat,
    g.geolocation_lng                           AS lng
FROM ranked r
LEFT JOIN (
    SELECT geolocation_zip_code_prefix,
           AVG(geolocation_lat) AS geolocation_lat,
           AVG(geolocation_lng) AS geolocation_lng
    FROM staging.geolocation
    GROUP BY geolocation_zip_code_prefix
) g ON r.customer_zip_code_prefix = g.geolocation_zip_code_prefix
WHERE r.rn = 1;

-- dim_sellers
CREATE OR REPLACE TABLE bq.olist_warehouse.dim_sellers AS
SELECT
    s.seller_id                                 AS seller_key,
    s.seller_zip_code_prefix                    AS zip_code,
    s.seller_city                               AS city,
    s.seller_state                              AS state,
    COALESCE(g.geolocation_lat, NULL)           AS lat,
    COALESCE(g.geolocation_lng, NULL)           AS lng
FROM staging.sellers s
LEFT JOIN (
    SELECT geolocation_zip_code_prefix,
           AVG(geolocation_lat) AS geolocation_lat,
           AVG(geolocation_lng) AS geolocation_lng
    FROM staging.geolocation
    GROUP BY geolocation_zip_code_prefix
) g ON s.seller_zip_code_prefix = g.geolocation_zip_code_prefix;

-- dim_products
CREATE OR REPLACE TABLE bq.olist_warehouse.dim_products AS
SELECT
    p.product_id                                AS product_key,
    COALESCE(t.product_category_name_english,
             p.product_category_name,
             'unknown')                         AS category,
    p.product_category_name                     AS category_pt,
    p.product_name_lenght                       AS name_length,
    p.product_description_lenght                AS description_length,
    p.product_photos_qty                        AS photos_qty,
    p.product_weight_g                          AS weight_g,
    p.product_length_cm                         AS length_cm,
    p.product_height_cm                         AS height_cm,
    p.product_width_cm                          AS width_cm,
    (p.product_length_cm * p.product_height_cm * p.product_width_cm) AS volume_cm3
FROM staging.products p
LEFT JOIN staging.category_translation t
       ON p.product_category_name = t.product_category_name;


-- ── FACT TABLES ───────────────────────────────────────────────

-- fact_orders: one row per order
CREATE OR REPLACE TABLE bq.olist_warehouse.fact_orders AS
WITH payment_agg AS (
    SELECT
        order_id,
        SUM(payment_value)                      AS total_payment,
        MAX(payment_installments)               AS max_installments,
        STRING_AGG(DISTINCT payment_type, ',')  AS payment_types
    FROM staging.order_payments
    GROUP BY order_id
),
review_agg AS (
    SELECT
        order_id,
        AVG(review_score)::DECIMAL(3,2)         AS avg_review_score,
        MAX(review_comment_message IS NOT NULL
            AND review_comment_message != '')::BOOLEAN AS has_review_comment
    FROM staging.order_reviews
    GROUP BY order_id
)
SELECT
    o.order_id,
    c.customer_unique_id                        AS customer_key,
    CAST(strftime(CAST(o.order_purchase_timestamp AS DATE), '%Y%m%d') AS INTEGER) AS purchase_date_key,
    o.order_status,
    CAST(o.order_purchase_timestamp AS TIMESTAMP)   AS purchased_at,
    CAST(o.order_approved_at AS TIMESTAMP)           AS approved_at,
    CAST(o.order_delivered_carrier_date AS TIMESTAMP) AS shipped_at,
    CAST(o.order_delivered_customer_date AS TIMESTAMP) AS delivered_at,
    CAST(o.order_estimated_delivery_date AS TIMESTAMP) AS estimated_delivery_at,
    -- delivery performance
    DATEDIFF('day',
        CAST(o.order_estimated_delivery_date AS TIMESTAMP),
        CAST(o.order_delivered_customer_date AS TIMESTAMP)
    )                                           AS delivery_delay_days,
    -- payment
    COALESCE(p.total_payment, 0)                AS total_payment,
    p.max_installments,
    p.payment_types,
    -- review
    r.avg_review_score,
    COALESCE(r.has_review_comment, FALSE)       AS has_review_comment
FROM staging.orders o
JOIN staging.customers c USING (customer_id)
LEFT JOIN payment_agg p USING (order_id)
LEFT JOIN review_agg r USING (order_id);

-- fact_order_items: one row per item in each order
CREATE OR REPLACE TABLE bq.olist_warehouse.fact_order_items AS
SELECT
    i.order_id,
    i.order_item_id,
    i.product_id                                AS product_key,
    i.seller_id                                 AS seller_key,
    fo.customer_key,
    fo.purchase_date_key,
    fo.purchased_at,
    i.price,
    i.freight_value,
    (i.price + i.freight_value)                 AS total_item_value,
    ROUND(i.freight_value / NULLIF(i.price + i.freight_value, 0), 4) AS freight_ratio,
    fo.avg_review_score,
    fo.delivery_delay_days,
    fo.order_status
FROM staging.order_items i
JOIN bq.olist_warehouse.fact_orders fo USING (order_id);
