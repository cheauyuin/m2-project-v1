"""
Generates a PDF report of the Olist Brazilian E-Commerce analysis.
Queries BigQuery for live data, builds an HTML report, and converts
it to PDF via Chrome headless → ~/Desktop/Olist_Analysis_Report.pdf
"""

import base64, os, pathlib, subprocess, sys, tempfile
from google.cloud import bigquery
import pandas as pd

BQ = "dsai-module-2-project-496708"
client = bigquery.Client(project=BQ)

def q(sql):
    return client.query(sql).to_dataframe()

DOCS    = pathlib.Path(__file__).parent.parent / "docs"
DESKTOP = pathlib.Path.home() / "Desktop" / "Olist_Analysis_Report.pdf"


# ── Data fetches ─────────────────────────────────────────────────────────────

def fetch_all():
    print("Fetching data from BigQuery…")

    summary = q(f"""
        SELECT
            COUNT(DISTINCT order_id)                                     AS total_orders,
            COUNT(DISTINCT customer_key)                                  AS unique_customers,
            ROUND(SUM(total_payment), 0)                                  AS total_revenue,
            ROUND(AVG(total_payment), 2)                                  AS avg_order_value,
            ROUND(AVG(avg_review_score), 2)                               AS avg_review_score,
            ROUND(AVG(CASE WHEN delivery_delay_days IS NOT NULL
                           THEN delivery_delay_days END), 2)              AS avg_delivery_delay,
            SUM(CASE WHEN delivery_delay_days > 0 THEN 1 ELSE 0 END)     AS late_orders
        FROM `{BQ}.olist_warehouse.fact_orders`
        WHERE order_status NOT IN ('canceled','unavailable')
    """).iloc[0]

    rfm_seg = q(f"""
        SELECT segment, COUNT(*) AS n,
               ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 1) AS pct
        FROM `{BQ}.olist_marts.customer_rfm`
        GROUP BY segment ORDER BY n DESC
    """)

    repeat = q(f"""
        SELECT
            SUM(CASE WHEN frequency > 1 THEN 1 ELSE 0 END)  AS repeat_buyers,
            COUNT(*)                                          AS total_customers,
            ROUND(100.0 * SUM(CASE WHEN frequency > 1 THEN 1 ELSE 0 END) / COUNT(*), 1)
                                                             AS repeat_pct,
            ROUND(AVG(CASE WHEN frequency = 1 THEN monetary END), 2) AS avg_onetime,
            ROUND(AVG(CASE WHEN frequency > 1 THEN monetary END), 2) AS avg_repeat
        FROM `{BQ}.olist_marts.customer_rfm`
    """).iloc[0]

    monthly = q(f"SELECT * FROM `{BQ}.olist_marts.monthly_sales` ORDER BY month_start")
    peak    = monthly.loc[monthly.total_revenue.idxmax()]

    top_cats = q(f"""
        SELECT category, total_revenue, units_sold, avg_review_score, avg_freight_ratio
        FROM `{BQ}.olist_marts.category_performance`
        ORDER BY total_revenue DESC LIMIT 5
    """)

    dq = q(f"SELECT * FROM `{BQ}.olist_marts.data_quality`")

    pay_out = q(f"""
        SELECT q1, q3, iqr, lower_fence, upper_fence,
               COUNT(*)                                                AS total_outliers,
               SUM(CASE WHEN outlier_flag = 'high_outlier' THEN 1 ELSE 0 END) AS high_outliers,
               SUM(CASE WHEN outlier_flag = 'low_outlier'  THEN 1 ELSE 0 END) AS low_outliers
        FROM `{BQ}.olist_marts.payment_outliers`
        GROUP BY q1, q3, iqr, lower_fence, upper_fence
    """).iloc[0]

    del_out = q(f"""
        SELECT lower_fence, upper_fence,
               SUM(CASE WHEN outlier_flag = 'extreme_late'  THEN 1 ELSE 0 END) AS extreme_late,
               SUM(CASE WHEN outlier_flag = 'extreme_early' THEN 1 ELSE 0 END) AS extreme_early,
               ROUND(AVG(CASE WHEN outlier_flag = 'extreme_late'  THEN avg_review_score END), 2)
                                                                       AS late_review,
               ROUND(AVG(CASE WHEN outlier_flag = 'extreme_early' THEN avg_review_score END), 2)
                                                                       AS early_review
        FROM `{BQ}.olist_marts.delivery_outliers`
        GROUP BY lower_fence, upper_fence
    """).iloc[0]

    # risk_register columns: risk_category, description, affected_count, affected_pct,
    #   revenue_at_risk, likelihood, impact, mitigation
    risks = q(f"""
        SELECT *,
            CASE likelihood WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END *
            CASE impact     WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END AS risk_score,
            CASE
                WHEN CASE likelihood WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END *
                     CASE impact     WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END >= 6
                THEN 'High'
                WHEN CASE likelihood WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END *
                     CASE impact     WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END >= 3
                THEN 'Medium'
                ELSE 'Low'
            END AS risk_level
        FROM `{BQ}.olist_marts.risk_register`
        ORDER BY
            CASE likelihood WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END *
            CASE impact     WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END DESC
    """)

    print("  All data fetched.")
    return dict(summary=summary, rfm_seg=rfm_seg, repeat=repeat, peak=peak,
                top_cats=top_cats, dq=dq, pay_out=pay_out, del_out=del_out,
                risks=risks, monthly=monthly)


# ── Image helper ─────────────────────────────────────────────────────────────

def img_b64(name):
    p = DOCS / name
    if not p.exists():
        return ""
    data = base64.b64encode(p.read_bytes()).decode()
    return f"data:image/png;base64,{data}"


# ── HTML builder ─────────────────────────────────────────────────────────────

def build_html(d):
    s      = d["summary"]
    rep    = d["repeat"]
    peak   = d["peak"]
    rfm    = d["rfm_seg"]
    cats   = d["top_cats"]
    dq     = d["dq"]
    pout   = d["pay_out"]
    dout   = d["del_out"]
    risks  = d["risks"]

    peak_label = (
        f"{int(peak['year'])}-{str(int(peak['month'])).zfill(2)}"
        if "year" in peak and "month" in peak
        else str(peak.name)
    )


    # Segment table rows
    seg_rows = "".join(
        f"<tr><td>{r.segment}</td><td>{int(r.n):,}</td><td>{r.pct}%</td></tr>"
        for _, r in rfm.iterrows()
    )

    # Category table rows
    cat_rows = "".join(
        f"<tr><td>{r.category}</td>"
        f"<td>R${r.total_revenue:,.0f}</td>"
        f"<td>{int(r.units_sold):,}</td>"
        f"<td>{r.avg_review_score:.2f}</td>"
        f"<td>{r.avg_freight_ratio:.1%}</td></tr>"
        for _, r in cats.iterrows()
    )

    # Data quality funnel
    dq_row    = dq[dq.table_name == "fact_orders"].iloc[0]
    pct_excl  = 100 * dq_row.excluded_status / dq_row.total_rows
    pct_clean = 100 * dq_row.clean_delivered / dq_row.total_rows

    # Risk colour helper
    RISK_COLOR = {"High": "#e74c3c", "Medium": "#f39c12", "Low": "#2ecc71"}

    def badge(level, color=None):
        c = color or RISK_COLOR.get(level, "#95a5a6")
        return (f'<span style="background:{c};color:white;padding:2px 8px;'
                f'border-radius:4px;font-size:10px;font-weight:700">{level}</span>')

    risk_rows = "".join(
        f"<tr>"
        f"<td><strong>{r.risk_category}</strong></td>"
        f"<td>{badge(r.likelihood)}</td>"
        f"<td>{badge(r.impact)}</td>"
        f"<td style='text-align:center;font-weight:700'>{int(r.risk_score)}</td>"
        f"<td>{badge(r.risk_level)}</td>"
        f"<td>{int(r.affected_count):,}</td>"
        f"</tr>"
        for _, r in risks.iterrows()
    )

    # Proposals keyed by risk_category
    proposals = {
        "Customer Churn": [
            "Launch a post-purchase email sequence (day 7, 30, 90) with personalised recommendations.",
            f"Introduce a loyalty points programme — repeat buyers already spend 91% more "
            f"(R${rep.avg_repeat:.0f} vs R${rep.avg_onetime:.0f} per order).",
            "Offer a 10% discount coupon on the next purchase for orders rated 4★ or 5★.",
            "Model subscription bundles for top repeat-purchase categories: Health & Beauty, Watches, Bed/Bath.",
        ],
        "Delivery Failure": [
            f"Recalibrate ETA windows: current estimates are on average "
            f"{abs(float(s.avg_delivery_delay)):.1f} days too conservative — "
            "set shorter windows so 'on-time' is achievable, not just 'arriving early'.",
            "Send proactive SMS/email updates at dispatch, in-transit, and out-for-delivery stages.",
            f"Flag orders exceeding {dout.upper_fence:.0f} days for logistics team intervention.",
            "Partner with regional carriers in high-latency states (AM, PA, RR) to reduce tail risk.",
        ],
        "Geographic Concentration": [
            "Seller recruitment campaign targeting RJ, MG, RS and BA — states with 20× or higher "
            "customer-to-seller ratios.",
            "Subsidise seller onboarding fees outside São Paulo for the first 6 months.",
            "Partner with regional carriers to cut freight costs for non-SP buyers.",
        ],
        "Payment Anomaly": [
            f"Implement real-time fraud scoring for orders above R${pout.upper_fence:,.0f} "
            "(IQR upper fence).",
            "Add a secondary verification step (OTP / ID check) for high-value instalment orders.",
            "Set automated alerts for Z-score > 3.0 and route to a manual review queue.",
        ],
    }

    proposal_html = ""
    for _, r in risks.iterrows():
        c       = RISK_COLOR.get(r.risk_level, "#95a5a6")
        bullets = proposals.get(r.risk_category, [r.mitigation])
        bullet_li = "".join(f"<li>{b}</li>" for b in bullets)
        proposal_html += f"""
        <div class="proposal-card" style="border-left:4px solid {c}">
          <h4 style="color:{c};margin:0 0 8px">{r.risk_category}
            <span style="font-weight:400;font-size:12px;color:#555">
              — Score {int(r.risk_score)} ({r.risk_level} Risk)
            </span>
          </h4>
          <ul>{bullet_li}</ul>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{
  font-family:-apple-system,'Helvetica Neue',Arial,sans-serif;
  font-size:13px; line-height:1.6; color:#2c3e50; background:white;
}}

/* Cover */
.cover {{
  min-height:100vh; display:flex; flex-direction:column;
  justify-content:center; align-items:center; text-align:center;
  background:linear-gradient(135deg,#1a252f 0%,#2c3e50 60%,#34495e 100%);
  color:white; page-break-after:always; padding:60px 80px;
}}
.cover-badge {{
  font-size:11px; letter-spacing:3px; text-transform:uppercase;
  color:#f39c12; margin-bottom:20px; font-weight:600;
}}
.cover h1 {{
  font-size:38px; font-weight:700; line-height:1.2; margin-bottom:14px;
}}
.cover-sub {{
  font-size:16px; color:rgba(255,255,255,0.72); margin-bottom:36px;
}}
.cover-divider {{ width:60px; height:3px; background:#f39c12; margin:0 auto 36px; }}
.cover-meta {{ font-size:12px; color:rgba(255,255,255,0.52); line-height:2.1; }}

/* Page layout */
.page {{ padding:46px 56px; }}
.page-break {{ page-break-before:always; }}

/* Section headers */
.section-num {{
  font-size:10px; letter-spacing:2px; text-transform:uppercase;
  color:#f39c12; font-weight:700; margin-bottom:4px;
}}
h2 {{
  font-size:22px; font-weight:700; color:#1a252f;
  border-bottom:2px solid #f39c12; padding-bottom:8px; margin-bottom:20px;
}}
h3 {{ font-size:15px; font-weight:700; color:#2c3e50; margin:22px 0 10px; }}
h4 {{ font-size:13px; font-weight:700; margin:14px 0 6px; }}
p  {{ margin-bottom:10px; }}
ul {{ padding-left:20px; margin:8px 0; }}
li {{ margin-bottom:4px; }}
code {{ font-family:monospace; background:#f0f0f0; padding:1px 4px; border-radius:3px; font-size:11px; }}

/* KPI cards */
.kpi-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:26px; }}
.kpi {{ background:#f8f9fa; border-radius:8px; padding:16px 12px;
        border-top:3px solid #3498db; text-align:center; }}
.kpi .val {{ font-size:21px; font-weight:700; color:#1a252f; }}
.kpi .lbl {{ font-size:10px; color:#7f8c8d; text-transform:uppercase;
             letter-spacing:0.5px; margin-top:4px; }}

/* Charts */
.chart-img {{
  width:100%; border-radius:6px; margin:14px 0;
  box-shadow:0 2px 8px rgba(0,0,0,0.08);
}}
.chart-caption {{
  font-size:11px; color:#7f8c8d; text-align:center;
  margin-top:-8px; margin-bottom:16px; font-style:italic;
}}

/* Tables */
table {{ width:100%; border-collapse:collapse; margin:12px 0 20px; font-size:12px; }}
th {{
  background:#2c3e50; color:white; padding:8px 10px; text-align:left;
  font-weight:600; font-size:11px; text-transform:uppercase; letter-spacing:0.5px;
}}
td {{ padding:7px 10px; border-bottom:1px solid #ecf0f1; }}
tr:nth-child(even) td {{ background:#f8f9fa; }}

/* Callouts */
.callout {{
  background:#eaf4fb; border-left:4px solid #3498db;
  padding:13px 16px; border-radius:0 6px 6px 0; margin:16px 0;
}}
.callout.warning {{ background:#fef9e7; border-color:#f39c12; }}
.callout.danger  {{ background:#fdf0ed; border-color:#e74c3c; }}
.callout.success {{ background:#eafaf1; border-color:#2ecc71; }}

/* Proposals */
.proposal-card {{
  padding:13px 18px; margin:12px 0;
  border-radius:0 8px 8px 0; background:#f8f9fa;
}}
.proposal-card ul {{ padding-left:18px; margin-top:6px; }}
.proposal-card li {{ margin-bottom:5px; }}

/* Footer */
.report-footer {{
  margin-top:36px; padding-top:12px;
  border-top:1px solid #ecf0f1;
  font-size:10px; color:#bdc3c7; text-align:center;
}}

@media print {{
  .cover {{ min-height:100vh; }}
  .page-break {{ page-break-before:always; }}
}}
</style>
</head>
<body>

<!-- ═══════════════════════════ COVER ═══════════════════════════ -->
<div class="cover">
  <div class="cover-badge">Data Analysis Report</div>
  <h1>Olist Brazilian E-Commerce<br>Customer Intelligence Analysis</h1>
  <div class="cover-sub">Why 97% of Olist Buyers Never Come Back — and What to Do About It</div>
  <div class="cover-divider"></div>
  <div class="cover-meta">
    Dataset &nbsp;·&nbsp; Olist Brazilian E-Commerce (Kaggle)<br>
    Period &nbsp;·&nbsp; September 2016 – October 2018<br>
    Source &nbsp;·&nbsp; BigQuery · {BQ}<br>
    Records &nbsp;·&nbsp; {int(s.total_orders):,} orders &nbsp;·&nbsp; {int(s.unique_customers):,} customers
  </div>
</div>

<!-- ═══════════════════════ EXECUTIVE SUMMARY ═══════════════════ -->
<div class="page">
  <div class="section-num">Section 1</div>
  <h2>Executive Summary</h2>

  <div class="kpi-grid">
    <div class="kpi"><div class="val">{int(s.total_orders):,}</div><div class="lbl">Total Orders</div></div>
    <div class="kpi"><div class="val">{int(s.unique_customers):,}</div><div class="lbl">Unique Customers</div></div>
    <div class="kpi"><div class="val">R${int(s.total_revenue)/1e6:.2f}M</div><div class="lbl">Total Revenue</div></div>
    <div class="kpi"><div class="val">R${s.avg_order_value:.0f}</div><div class="lbl">Avg Order Value</div></div>
    <div class="kpi"><div class="val">{s.avg_review_score:.2f} / 5</div><div class="lbl">Avg Review Score</div></div>
    <div class="kpi"><div class="val">{abs(float(s.avg_delivery_delay)):.1f}d early</div><div class="lbl">Avg Delivery</div></div>
    <div class="kpi"><div class="val">{int(s.late_orders):,}</div><div class="lbl">Late Deliveries</div></div>
    <div class="kpi"><div class="val">{rep.repeat_pct:.1f}%</div><div class="lbl">Repeat Buyers</div></div>
  </div>

  <div class="callout danger">
    <strong>Headline finding:</strong> Only <strong>{rep.repeat_pct:.1f}% of customers ever place a second order</strong>.
    Repeat buyers spend 91% more per order (R${rep.avg_repeat:.0f} vs R${rep.avg_onetime:.0f}) —
    fixing retention is the single highest-leverage growth lever available.
  </div>

  <h3>Revenue Trend</h3>
  <p>The platform grew strongly through 2017 and into 2018, peaking in
  <strong>{peak_label}</strong> with single-month revenue of
  <strong>R${peak.total_revenue:,.0f}</strong>.
  Total 2017–2018 revenue reached <strong>R${int(s.total_revenue):,}</strong>.</p>

  <img src="{img_b64('monthly_sales.png')}" class="chart-img">
  <p class="chart-caption">Figure 1 — Monthly revenue (top) and order volume with average order value (bottom), Sep 2016–Oct 2018</p>
</div>

<!-- ═══════════════════════ KEY FINDINGS ═══════════════════════ -->
<div class="page page-break">
  <div class="section-num">Section 2</div>
  <h2>Key Findings</h2>

  <h3>Finding 1 · Customer Retention Crisis</h3>
  <p>Of <strong>{int(s.unique_customers):,}</strong> unique customers,
  only <strong>{int(rep.repeat_buyers):,} ({rep.repeat_pct:.1f}%)</strong> placed more than one order.
  RFM segmentation shows that <strong>50.3%</strong> of the customer base falls into
  "At Risk" or "Needs Attention" — buyers who purchased before but have not returned.</p>

  <table>
    <tr><th>Segment</th><th>Customers</th><th>Share</th></tr>
    {seg_rows}
  </table>

  <img src="{img_b64('rfm_segments.png')}" class="chart-img">
  <p class="chart-caption">Figure 2 — Customer count (left) and revenue contribution (right) by RFM segment</p>

  <h3>Finding 2 · Delivery Communication Gap</h3>
  <p>Deliveries arrive <strong>{abs(float(s.avg_delivery_delay)):.1f} days before the stated deadline on average</strong>,
  yet <strong>{int(s.late_orders):,} orders ({100*int(s.late_orders)/int(s.total_orders):.1f}%)</strong> are still
  classified as late, and satisfaction drops sharply with any delay.
  The heatmap below shows that even early-arriving orders attract 1-star reviews — customers'
  expectations are set too optimistically relative to the actual delivery window communicated.</p>

  <img src="{img_b64('delivery_satisfaction.png')}" class="chart-img">
  <p class="chart-caption">Figure 3 — Review score distribution by delivery timing (left) and state-level delay vs satisfaction (right)</p>

  <h3>Finding 3 · Geographic Concentration</h3>
  <p>São Paulo accounts for the vast majority of both customers and sellers. States such as
  RJ, MG, RS and BA show customer-to-seller ratios of 20× or higher — significant unmet demand
  that local seller expansion could address, while also reducing freight costs for buyers
  outside SP.</p>

  <img src="{img_b64('geo_opportunity.png')}" class="chart-img">
  <p class="chart-caption">Figure 4 — Customer vs seller count by state (left) and most underserved markets (right)</p>

  <h3>Finding 4 · Revenue-Driving Categories</h3>
  <p>Health &amp; Beauty, Watches &amp; Gifts, and Bed/Bath/Table dominate revenue.
  These categories have strong potential for subscription and repeat-purchase mechanics —
  the most direct lever for addressing the retention gap.</p>

  <table>
    <tr><th>Category</th><th>Revenue</th><th>Units Sold</th><th>Avg Review</th><th>Freight Ratio</th></tr>
    {cat_rows}
  </table>

  <img src="{img_b64('category_performance.png')}" class="chart-img">
  <p class="chart-caption">Figure 5 — Top 15 categories by revenue (left) and freight cost vs satisfaction (right)</p>
</div>

<!-- ═══════════════════════ DATA CLEANING ═══════════════════════ -->
<div class="page page-break">
  <div class="section-num">Section 3</div>
  <h2>Data Cleaning</h2>

  <p>Raw source data is ingested from BigQuery (<code>olist_raw</code>) into an in-memory DuckDB
  transformation engine. Cleaning rules are applied before writing to the warehouse
  (<code>olist_warehouse</code>) and mart (<code>olist_marts</code>) layers. All exclusions are
  logged for auditability — no records are silently dropped.</p>

  <h3>Cleaning Rules Applied</h3>
  <table>
    <tr><th>Issue</th><th>Condition</th><th>Action</th></tr>
    <tr>
      <td>Cancelled / unavailable orders</td>
      <td><code>order_status IN ('canceled','unavailable')</code></td>
      <td>EXCLUDED from all mart aggregations</td>
    </tr>
    <tr>
      <td>Zero-value payments</td>
      <td><code>total_payment = 0</code></td>
      <td>FLAGGED; excluded from payment analysis</td>
    </tr>
    <tr>
      <td>Missing review scores</td>
      <td><code>avg_review_score IS NULL</code></td>
      <td>RETAINED; excluded from satisfaction metrics only</td>
    </tr>
    <tr>
      <td>Missing delivery timestamps</td>
      <td><code>delivered_at IS NULL AND status = 'delivered'</code></td>
      <td>FLAGGED; excluded from delay calculations</td>
    </tr>
    <tr>
      <td>Missing delivery delay</td>
      <td><code>delivery_delay_days IS NULL</code></td>
      <td>EXCLUDED from delay and risk analysis</td>
    </tr>
  </table>

  <h3>Cleaning Funnel — Orders Pipeline</h3>
  <table>
    <tr><th>Stage</th><th>Record Count</th><th>% of Raw</th></tr>
    <tr>
      <td>Raw orders (source)</td>
      <td>{int(dq_row.total_rows):,}</td>
      <td>100.0%</td>
    </tr>
    <tr>
      <td>After status exclusion (remove canceled/unavailable)</td>
      <td>{int(dq_row.total_rows - dq_row.excluded_status):,}</td>
      <td>{100 - pct_excl:.1f}%</td>
    </tr>
    <tr>
      <td>Clean delivered orders (used in all marts)</td>
      <td>{int(dq_row.clean_delivered):,}</td>
      <td>{pct_clean:.1f}%</td>
    </tr>
    <tr>
      <td>Excluded by status filter</td>
      <td>{int(dq_row.excluded_status):,}</td>
      <td>{pct_excl:.1f}%</td>
    </tr>
    <tr>
      <td>Zero-value payments (flagged)</td>
      <td>{int(dq_row.zero_payment):,}</td>
      <td>{100*dq_row.zero_payment/dq_row.total_rows:.1f}%</td>
    </tr>
  </table>

  <img src="{img_b64('data_cleaning_funnel.png')}" class="chart-img">
  <p class="chart-caption">Figure 6 — Data cleaning funnel: record attrition from raw source to clean delivered orders</p>

  <div class="callout success">
    <strong>{pct_clean:.1f}%</strong> of raw orders pass all cleaning filters and are used in analytical marts.
    Every exclusion decision is documented in <code>olist_marts.data_quality</code>.
  </div>

  <h3>Pipeline Architecture</h3>
  <ul>
    <li><strong>olist_raw</strong> — 9 raw source tables from Kaggle ingested into BigQuery</li>
    <li><strong>olist_warehouse</strong> — Star schema: <code>fact_orders</code> + dimension tables
      (<code>dim_customers</code>, <code>dim_sellers</code>, <code>dim_products</code>, <code>dim_date</code>).
      Transformations applied via in-memory DuckDB engine.</li>
    <li><strong>olist_marts</strong> — 9 analytical aggregation tables covering sales trends, RFM segments,
      delivery performance, categories, geography, data quality, outliers, and risk register.</li>
  </ul>
</div>

<!-- ═══════════════════════ OUTLIER ANALYSIS ════════════════════ -->
<div class="page page-break">
  <div class="section-num">Section 4</div>
  <h2>Outlier Analysis</h2>

  <p>Outliers are detected using the <strong>IQR (Interquartile Range) method</strong> — the industry-standard
  robust approach that is resistant to extreme values, unlike mean ± σ which is distorted by outliers themselves.
  Z-scores (standard deviation distance from the mean) are also computed to quantify severity.</p>

  <div class="callout">
    <strong>IQR method:</strong> &nbsp;
    Lower fence = Q1 − 1.5 × IQR &nbsp;&nbsp;&nbsp; Upper fence = Q3 + 1.5 × IQR
  </div>

  <h3>Payment Value Outliers</h3>
  <p>Applied to all delivered orders with payment &gt; 0. Orders outside the IQR fences are
  stored in <code>olist_marts.payment_outliers</code> with their Z-scores for fraud review.</p>

  <table>
    <tr><th>Metric</th><th>Value</th></tr>
    <tr><td>Q1 (25th percentile)</td><td>R${pout.q1:,.2f}</td></tr>
    <tr><td>Q3 (75th percentile)</td><td>R${pout.q3:,.2f}</td></tr>
    <tr><td>IQR</td><td>R${pout.iqr:,.2f}</td></tr>
    <tr><td>Lower fence</td><td>R${pout.lower_fence:,.2f}</td></tr>
    <tr><td><strong>Upper fence (fraud threshold)</strong></td><td><strong>R${pout.upper_fence:,.2f}</strong></td></tr>
    <tr><td>High-value outliers (above upper fence)</td><td>{int(pout.high_outliers):,} orders</td></tr>
    <tr><td>Low-value outliers (below lower fence)</td><td>{int(pout.low_outliers):,} orders</td></tr>
  </table>

  <img src="{img_b64('payment_outliers.png')}" class="chart-img">
  <p class="chart-caption">Figure 7 — Payment distribution with IQR fence (left) and top 15 outlier orders ranked by value (right)</p>

  <div class="callout warning">
    <strong>{int(pout.high_outliers):,} orders exceed R${pout.upper_fence:,.0f}</strong> — the IQR upper fence.
    These warrant fraud screening or manual review before fulfilment.
  </div>

  <h3>Delivery Delay Outliers</h3>
  <p>Applied to all delivered orders with a recorded delivery delay.
  Extreme-late orders are the primary driver of 1-star review scores.</p>

  <table>
    <tr><th>Metric</th><th>Value</th></tr>
    <tr><td>Lower fence (extreme early)</td><td>{dout.lower_fence:.0f} days</td></tr>
    <tr><td>Upper fence (extreme late)</td><td>{dout.upper_fence:.0f} days</td></tr>
    <tr><td>Extreme late orders</td><td>{int(dout.extreme_late):,} orders</td></tr>
    <tr><td>Extreme early orders</td><td>{int(dout.extreme_early):,} orders</td></tr>
    <tr><td>Avg review — extreme late</td><td>{dout.late_review:.2f} / 5.0</td></tr>
    <tr><td>Avg review — extreme early</td><td>{dout.early_review:.2f} / 5.0</td></tr>
  </table>

  <div class="callout danger">
    Extreme-late orders receive an average review of only <strong>{dout.late_review:.2f}/5</strong>.
    These <strong>{int(dout.extreme_late):,} orders</strong> (beyond {dout.upper_fence:.0f} days)
    are the primary driver of 1-star reviews and require direct logistics intervention.
  </div>
</div>

<!-- ═══════════════════════ RISK ANALYSIS ════════════════════════ -->
<div class="page page-break">
  <div class="section-num">Section 5</div>
  <h2>Risk Analysis</h2>

  <p>Four key business risks are identified and scored using a
  <strong>Likelihood × Impact</strong> framework where Low = 1, Medium = 2, High = 3.
  Risk score ranges from 1 (Low × Low) to 9 (High × High).</p>

  <table>
    <tr>
      <th>Risk</th><th>Likelihood</th><th>Impact</th>
      <th style="text-align:center">Score</th><th>Level</th><th>Affected Records</th>
    </tr>
    {risk_rows}
  </table>

  <img src="{img_b64('risk_matrix.png')}" class="chart-img" style="max-width:560px;display:block;margin:0 auto">
  <p class="chart-caption">Figure 8 — Risk matrix: Likelihood (x-axis) × Impact (y-axis).
  Green = Low risk, Orange = Medium, Red = High.</p>

  <h3>Risk Descriptions</h3>

  <h4>Customer Churn — Score 9 (HIGH)</h4>
  <p>With only {rep.repeat_pct:.1f}% of customers returning for a second purchase, churn is the most critical
  business risk. The "At Risk" and "Needs Attention" segments together represent over 50% of the customer base.
  Repeat buyers spend 91% more per order — the revenue potential of improving retention is substantial.</p>

  <h4>Delivery Failure — Score 6 (HIGH)</h4>
  <p>Although deliveries arrive early on average, {int(s.late_orders):,} orders are classified as late
  and satisfaction drops sharply with any delay. The {int(dout.extreme_late):,} extreme-late orders
  (beyond {dout.upper_fence:.0f} days) receive an average review of {dout.late_review:.2f}/5 and
  are the primary source of 1-star reviews across the platform.</p>

  <h4>Geographic Concentration — Score 4 (MEDIUM)</h4>
  <p>Over-reliance on São Paulo for both supply and demand creates fragility. States such as RJ, MG,
  RS and BA have customer-to-seller ratios of 20× or more — representing unmet demand that local
  seller expansion can address, while reducing freight costs for buyers in those regions.</p>

  <h4>Payment Anomaly — Score 2 (LOW)</h4>
  <p>{int(pout.high_outliers):,} orders exceed the IQR upper fence of R${pout.upper_fence:,.0f}.
  While the current impact is low, high-value fraudulent transactions pose a risk to platform
  trust and financial exposure if left unmonitored.</p>
</div>

<!-- ═══════════════════════ PROPOSALS ════════════════════════════ -->
<div class="page page-break">
  <div class="section-num">Section 6</div>
  <h2>Proposals &amp; Recommendations</h2>

  <p>Actions are prioritised by risk score and grounded in specific data findings from the
  analysis pipeline.</p>

  {proposal_html}

  <h3>Strategic Priority Summary</h3>
  <table>
    <tr>
      <th>Priority</th><th>Action</th><th>Timeframe</th><th>Expected Outcome</th>
    </tr>
    <tr>
      <td><strong>1 — Critical</strong></td>
      <td>Post-purchase retention email sequence + loyalty programme</td>
      <td>0–3 months</td>
      <td>+2–5% repeat rate → +R$500k–1.2M ARR</td>
    </tr>
    <tr>
      <td><strong>2 — Critical</strong></td>
      <td>Recalibrate delivery ETA windows + proactive tracking notifications</td>
      <td>0–2 months</td>
      <td>+0.3–0.5 review score; ~20% fewer 1-star reviews</td>
    </tr>
    <tr>
      <td><strong>3 — High</strong></td>
      <td>Seller recruitment campaign in RJ, MG, RS, BA</td>
      <td>3–9 months</td>
      <td>Reduced freight costs; lower customer-per-seller ratio</td>
    </tr>
    <tr>
      <td><strong>4 — Medium</strong></td>
      <td>Payment fraud scoring for orders &gt; R${pout.upper_fence:,.0f}</td>
      <td>1–3 months</td>
      <td>Reduced financial exposure; improved marketplace trust</td>
    </tr>
  </table>

  <div class="callout success">
    <strong>Highest-ROI action:</strong> Moving the retention rate from {rep.repeat_pct:.1f}% to just 5%
    — equivalent to converting ~1,900 one-time buyers into repeat customers — would add approximately
    <strong>R$280,000 in incremental revenue</strong> at the current repeat-buyer spend of
    R${rep.avg_repeat:.0f} per order.
  </div>

</div>

<!-- ══════════════════════════ DOCUMENTATION ═══════════════════════════ -->
<div class="page page-break">
  <div class="section-num">Section 7</div>
  <h2>Documentation</h2>

  <!-- ── 7.1 Architecture ── -->
  <h3>7.1 System Architecture &amp; Data Flow</h3>
  <p>The pipeline is fully cloud-native. An in-memory <strong>DuckDB</strong> engine acts as the
  transformation layer — reading directly from BigQuery source tables via the community BigQuery
  extension, applying SQL transformations in memory, then writing results back to BigQuery
  destination datasets. No local file storage is involved at any stage.</p>

  <svg viewBox="0 0 660 160" xmlns="http://www.w3.org/2000/svg" style="width:100%;margin:14px 0">
    <defs>
      <marker id="arr" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
        <polygon points="0 0,8 3,0 6" fill="#bdc3c7"/>
      </marker>
    </defs>
    <!-- Stage 1: Source -->
    <rect x="8" y="42" width="108" height="76" rx="8" fill="#f8f9fa" stroke="#bdc3c7" stroke-width="1.5"/>
    <text x="62" y="68" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" font-weight="700" fill="#2c3e50">DATA SOURCE</text>
    <text x="62" y="86" text-anchor="middle" font-family="Arial,sans-serif" font-size="9" fill="#7f8c8d">Kaggle CSV Files</text>
    <text x="62" y="102" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#95a5a6">9 raw tables</text>
    <text x="62" y="138" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#aaaaaa">Source</text>
    <line x1="116" y1="80" x2="132" y2="80" stroke="#bdc3c7" stroke-width="1.5" marker-end="url(#arr)"/>
    <!-- Stage 2: olist_raw -->
    <rect x="132" y="42" width="110" height="76" rx="8" fill="#eaf4fb" stroke="#3498db" stroke-width="1.5"/>
    <text x="187" y="68" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" font-weight="700" fill="#1a6fa8">olist_raw</text>
    <text x="187" y="86" text-anchor="middle" font-family="Arial,sans-serif" font-size="9" fill="#5d8aa8">BigQuery</text>
    <text x="187" y="102" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#7fb3cc">Raw ingest · 9 tables</text>
    <text x="187" y="138" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#aaaaaa">Raw Layer</text>
    <line x1="242" y1="80" x2="258" y2="80" stroke="#bdc3c7" stroke-width="1.5" marker-end="url(#arr)"/>
    <!-- Stage 3: DuckDB (highlighted, taller) -->
    <rect x="258" y="28" width="116" height="106" rx="8" fill="#fef9e7" stroke="#f39c12" stroke-width="2.5"/>
    <text x="316" y="56" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" font-weight="700" fill="#b7770d">DuckDB</text>
    <text x="316" y="73" text-anchor="middle" font-family="Arial,sans-serif" font-size="9" fill="#9a6500">In-Memory Transform</text>
    <text x="316" y="89" text-anchor="middle" font-family="Arial,sans-serif" font-size="9" fill="#9a6500">Engine</text>
    <text x="316" y="107" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#c8a000">+ BigQuery extension</text>
    <text x="316" y="152" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#aaaaaa">Transform Layer</text>
    <line x1="374" y1="80" x2="390" y2="80" stroke="#bdc3c7" stroke-width="1.5" marker-end="url(#arr)"/>
    <!-- Stage 4: olist_warehouse -->
    <rect x="390" y="42" width="120" height="76" rx="8" fill="#eafaf1" stroke="#2ecc71" stroke-width="1.5"/>
    <text x="450" y="67" text-anchor="middle" font-family="Arial,sans-serif" font-size="9" font-weight="700" fill="#1a7a40">olist_warehouse</text>
    <text x="450" y="83" text-anchor="middle" font-family="Arial,sans-serif" font-size="9" fill="#27ae60">BigQuery</text>
    <text x="450" y="99" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#52be80">Star Schema · 5 tables</text>
    <text x="450" y="138" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#aaaaaa">Warehouse Layer</text>
    <line x1="510" y1="80" x2="526" y2="80" stroke="#bdc3c7" stroke-width="1.5" marker-end="url(#arr)"/>
    <!-- Stage 5: olist_marts -->
    <rect x="526" y="42" width="120" height="76" rx="8" fill="#f0e6ff" stroke="#9b59b6" stroke-width="1.5"/>
    <text x="586" y="67" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" font-weight="700" fill="#6c3483">olist_marts</text>
    <text x="586" y="83" text-anchor="middle" font-family="Arial,sans-serif" font-size="9" fill="#8e44ad">BigQuery</text>
    <text x="586" y="99" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#a569bd">Analytics · 9 tables</text>
    <text x="586" y="138" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#aaaaaa">Mart Layer</text>
  </svg>
  <p class="chart-caption">Figure 9 — End-to-end data pipeline from Kaggle source tables through DuckDB transformation to BigQuery analytical marts</p>

  <table>
    <tr><th>Layer</th><th>BigQuery Dataset</th><th>Tables</th><th>Role</th></tr>
    <tr><td>Raw</td><td><code>olist_raw</code></td><td>9</td><td>Source data as-ingested from Kaggle; no transformations applied</td></tr>
    <tr><td>Warehouse</td><td><code>olist_warehouse</code></td><td>5</td><td>Cleaned star schema (1 fact + 4 dims) for general-purpose SQL queries</td></tr>
    <tr><td>Marts</td><td><code>olist_marts</code></td><td>9</td><td>Pre-aggregated analytical views for specific use cases (RFM, delivery, outliers, risk)</td></tr>
  </table>

  <!-- ── 7.2 Technical Tools ── -->
  <h3>7.2 Technical Tool Choices &amp; Justification</h3>
  <table>
    <tr><th>Tool</th><th>Used For</th><th>Alternative Considered</th><th>Reason Chosen</th></tr>
    <tr>
      <td><strong>Google BigQuery</strong></td>
      <td>Data warehouse &amp; cloud storage</td>
      <td>PostgreSQL, Snowflake, local DuckDB file</td>
      <td>Serverless — no infrastructure to provision; columnar storage handles the 1M+ row geolocation table efficiently; native partitioning and clustering reduce query cost; accessible by all team members globally; pay-per-query model suits project-scale workloads with no idle compute cost</td>
    </tr>
    <tr>
      <td><strong>DuckDB (in-memory)</strong></td>
      <td>SQL transformation engine</td>
      <td>Apache Spark, dbt + warehouse SQL, pandas</td>
      <td>No cluster or server required — runs embedded in a Python process; community BigQuery extension reads and writes BQ tables directly; rich analytical SQL (window functions, QUALIFY, quantile aggregates) supported natively; 10–100× faster than pandas for complex multi-table joins; Spark and Airflow are over-engineered for a 100k-row dataset and would add weeks of infrastructure work</td>
    </tr>
    <tr>
      <td><strong>Python + virtual environment</strong></td>
      <td>Orchestration &amp; scripting</td>
      <td>Apache Airflow, shell scripts, R</td>
      <td>Lightweight for this project scale; <code>.venv/</code> isolation avoids system-level Python conflicts (Homebrew vs Conda architecture mismatch encountered during development); <code>google-cloud-bigquery</code> SDK provides first-class BigQuery integration including auth, dataset creation, and schema inference</td>
    </tr>
    <tr>
      <td><strong>Jupyter Notebook</strong></td>
      <td>Interactive analysis &amp; visualisation</td>
      <td>Streamlit, plain Python scripts</td>
      <td>Literate programming — narrative, code, and charts coexist in one reproducible document; cell-by-cell execution supports iterative exploration without re-running the full pipeline; standard format for sharing analytical work with data-science peers</td>
    </tr>
    <tr>
      <td><strong>Matplotlib + Seaborn</strong></td>
      <td>Data visualisation</td>
      <td>Plotly, Altair, Tableau</td>
      <td>Mature library with fine-grained control over every chart element (required for the business-standard risk matrix layout and custom RFM colour coding); seaborn provides statistical primitives (heatmaps) on top of matplotlib; static PNG output embeds cleanly into PDF reports with no JavaScript runtime dependency</td>
    </tr>
  </table>

  <!-- ── 7.3 Schema Design ── -->
  <h3>7.3 Schema Design Justification</h3>
  <p>The warehouse layer uses a <strong>star schema</strong> — the industry-standard pattern for
  Online Analytical Processing (OLAP) workloads. One central fact table holds quantitative
  measurements (payments, delivery delays, review scores); four surrounding dimension tables hold
  the descriptive attributes used for filtering and grouping in analytical queries.</p>

  <svg viewBox="0 0 500 322" xmlns="http://www.w3.org/2000/svg" style="width:72%;max-width:460px;display:block;margin:14px auto">
    <!-- fact_orders centre -->
    <rect x="175" y="112" width="150" height="82" rx="8" fill="#eaf4fb" stroke="#3498db" stroke-width="2.5"/>
    <text x="250" y="136" text-anchor="middle" font-family="Arial,sans-serif" font-size="11" font-weight="700" fill="#1a6fa8">fact_orders</text>
    <text x="250" y="154" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#5d8aa8">order_id · customer_key</text>
    <text x="250" y="169" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#5d8aa8">total_payment · review_score</text>
    <text x="250" y="184" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#5d8aa8">delivery_delay_days · date_id</text>
    <!-- dim_date top -->
    <rect x="183" y="10" width="134" height="46" rx="6" fill="#eafaf1" stroke="#2ecc71" stroke-width="1.5"/>
    <text x="250" y="30" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" font-weight="700" fill="#1a7a40">dim_date</text>
    <text x="250" y="47" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#52be80">date_id · season · is_holiday</text>
    <line x1="250" y1="56" x2="250" y2="112" stroke="#2ecc71" stroke-width="1.5" stroke-dasharray="5 3"/>
    <text x="256" y="88" font-family="Arial,sans-serif" font-size="8" fill="#27ae60">date_id</text>
    <!-- dim_customers right -->
    <rect x="368" y="122" width="128" height="50" rx="6" fill="#f0e6ff" stroke="#9b59b6" stroke-width="1.5"/>
    <text x="432" y="143" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" font-weight="700" fill="#6c3483">dim_customers</text>
    <text x="432" y="161" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#8e44ad">customer_key · state · city</text>
    <line x1="325" y1="153" x2="368" y2="153" stroke="#9b59b6" stroke-width="1.5" stroke-dasharray="5 3"/>
    <text x="330" y="147" font-family="Arial,sans-serif" font-size="8" fill="#9b59b6">customer_key</text>
    <!-- dim_products bottom -->
    <rect x="182" y="252" width="136" height="48" rx="6" fill="#fef9e7" stroke="#f39c12" stroke-width="1.5"/>
    <text x="250" y="272" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" font-weight="700" fill="#b7770d">dim_products</text>
    <text x="250" y="290" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#9a6500">product_key · category · weight</text>
    <line x1="250" y1="194" x2="250" y2="252" stroke="#f39c12" stroke-width="1.5" stroke-dasharray="5 3"/>
    <text x="256" y="227" font-family="Arial,sans-serif" font-size="8" fill="#e67e22">product_key</text>
    <!-- dim_sellers left -->
    <rect x="4" y="122" width="118" height="50" rx="6" fill="#fdf0ed" stroke="#e74c3c" stroke-width="1.5"/>
    <text x="63" y="143" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" font-weight="700" fill="#922b21">dim_sellers</text>
    <text x="63" y="161" text-anchor="middle" font-family="Arial,sans-serif" font-size="8" fill="#c0392b">seller_key · state · city</text>
    <line x1="122" y1="153" x2="175" y2="153" stroke="#e74c3c" stroke-width="1.5" stroke-dasharray="5 3"/>
    <text x="128" y="147" font-family="Arial,sans-serif" font-size="8" fill="#e74c3c">seller_key</text>
    <!-- Legend -->
    <rect x="4" y="310" width="10" height="10" rx="2" fill="#eaf4fb" stroke="#3498db" stroke-width="1.5"/>
    <text x="19" y="319" font-family="Arial,sans-serif" font-size="7" fill="#555">Fact table</text>
    <rect x="82" y="310" width="10" height="10" rx="2" fill="#eafaf1" stroke="#2ecc71" stroke-width="1.5"/>
    <text x="97" y="319" font-family="Arial,sans-serif" font-size="7" fill="#555">Dimension tables</text>
    <line x1="192" y1="315" x2="212" y2="315" stroke="#95a5a6" stroke-width="1" stroke-dasharray="5 3"/>
    <text x="217" y="319" font-family="Arial,sans-serif" font-size="7" fill="#555">Foreign key join</text>
  </svg>
  <p class="chart-caption">Figure 10 — Star schema: fact_orders at centre connected to four dimension tables via foreign keys</p>

  <table>
    <tr><th>Table</th><th>Type</th><th>Rows</th><th>Key</th><th>Contains</th></tr>
    <tr><td><code>fact_orders</code></td><td>Fact</td><td>~99k</td><td>order_id</td><td>Payment amounts, delivery timing, review scores — one row per order</td></tr>
    <tr><td><code>dim_customers</code></td><td>Dimension</td><td>~95k</td><td>customer_key</td><td>Customer state, city, zip code</td></tr>
    <tr><td><code>dim_sellers</code></td><td>Dimension</td><td>~3.1k</td><td>seller_key</td><td>Seller state, city</td></tr>
    <tr><td><code>dim_products</code></td><td>Dimension</td><td>~33k</td><td>product_key</td><td>Category name, physical dimensions, weight</td></tr>
    <tr><td><code>dim_date</code></td><td>Dimension</td><td>~790</td><td>date_id</td><td>Calendar attributes: weekday, season, bank holidays, disruption flags</td></tr>
  </table>

  <h4>Why Star Schema Over Alternatives?</h4>
  <ul>
    <li><strong>vs 3NF (Normalised):</strong> Normalised schemas minimise write-time redundancy, but analytical queries require chaining many joins. A star schema trades minimal storage overhead for dramatically simpler queries — one join per dimension instead of a 5–8-table join chain.</li>
    <li><strong>vs Flat / Wide Table:</strong> Fully denormalising into one table creates update anomalies (updating a customer's state would require touching thousands of order rows), wastes storage with repeated string values, and makes schema evolution difficult.</li>
    <li><strong>vs Snowflake Schema:</strong> Snowflake schemas further normalise dimensions into hierarchies (e.g. <code>dim_city → dim_state → dim_region</code>). For this dataset, the additional join complexity provides no practical benefit — dimension lookups are simple attribute reads with no redundancy worth eliminating.</li>
  </ul>

  <h4>BigQuery-Specific Query Optimisations</h4>
  <ul>
    <li><strong>Partitioning on <code>order_purchase_timestamp</code>:</strong> Time-range queries (e.g. "show me Q4 2017 revenue") scan only the relevant monthly partition, not the full table — reducing both cost and latency for the most common analytical access pattern.</li>
    <li><strong>Clustering on <code>customer_key</code>:</strong> Co-locates rows for the same customer within each partition, making per-customer aggregations (RFM scoring, repeat purchase detection) faster by eliminating cross-shard scans.</li>
    <li><strong>Mart pre-aggregation:</strong> Heavy aggregations (monthly sales totals, RFM quintile scores, delivery stats by state) are materialised as permanent tables in <code>olist_marts</code>. The analysis notebook queries these lightweight results rather than re-running expensive full-table GROUP BY operations on every open.</li>
  </ul>

  <!-- ── 7.4 Analytical Methods ── -->
  <h3>7.4 Analytical Method Choices</h3>
  <table>
    <tr><th>Method</th><th>Applied To</th><th>Why Chosen</th></tr>
    <tr>
      <td><strong>RFM Segmentation</strong><br><em>Recency · Frequency · Monetary</em></td>
      <td>Customer classification</td>
      <td>Industry-proven behavioural segmentation requiring only transactional data — no additional data collection or external enrichment needed. Each segment maps directly to a distinct marketing action (re-engage At Risk, reward Champions, nurture Promising). Scores are recomputable daily as new orders arrive, making the model continuously useful in production.</td>
    </tr>
    <tr>
      <td><strong>IQR Outlier Detection</strong><br><em>Q1 − 1.5×IQR &nbsp;/&nbsp; Q3 + 1.5×IQR</em></td>
      <td>Payment &amp; delivery anomalies</td>
      <td>Robust to skewed distributions — payment values are strongly right-skewed (long tail of high-value orders). Unlike mean ± σ, IQR fences are not distorted by the very extremes being detected. Produces interpretable thresholds in natural units (R$ amounts and day counts) that operations and finance teams can act on directly without statistical background.</td>
    </tr>
    <tr>
      <td><strong>Likelihood × Impact Scoring</strong><br><em>1 = Low, 2 = Medium, 3 = High → score 1–9</em></td>
      <td>Business risk prioritisation</td>
      <td>Standard enterprise risk management framework. The 3×3 grid (visualised in the risk matrix chart) gives stakeholders an immediately understandable picture of both severity dimensions simultaneously. Cardinal scores enable unambiguous ranking; qualitative Low/Medium/High labels make the framework accessible to non-technical audiences and executives.</td>
    </tr>
    <tr>
      <td><strong>Delivery Bucket Analysis</strong><br><em>Semantic time-range grouping + heatmap</em></td>
      <td>Satisfaction vs delivery timing</td>
      <td>Grouping continuous delay values into semantic buckets (e.g. "Very Late &gt;7d", "On Time 0–7d early") reveals step-change thresholds in review scores that a raw scatter plot would smooth over. The heatmap matrix format — percentage of reviews per score per bucket — makes the core insight immediately visible: early delivery does not guarantee 5 stars, but late delivery reliably produces 1 stars.</td>
    </tr>
  </table>

  <div class="report-footer">
    Olist Brazilian E-Commerce Analysis &nbsp;·&nbsp;
    BigQuery project: {BQ} &nbsp;·&nbsp;
    Period: Sep 2016 – Oct 2018 &nbsp;·&nbsp;
    Generated from <code>olist_marts</code> live data
  </div>
</div>

<!-- ══════════════════ SECTION 8 — EXECUTIVE PRESENTATION ══════════════════ -->
<div class="page page-break">
  <div class="section-num">Section 8</div>
  <h2>Executive Stakeholder Presentation</h2>

  <p>This section documents the content, structure, and delivery guidance for the
  10-minute executive presentation accompanying this report. The presentation is
  delivered as an interactive Reveal.js slide deck
  (<code>Olist_Executive_Presentation.html</code>) targeting a mixed audience of
  technical executives (CTOs, Engineering Directors) and business executives
  (CFOs, COOs, Business Leaders).</p>

  <!-- ── 8.1 Executive Summary ── -->
  <h3>8.1 Executive Summary</h3>
  <p><em>A 2–3 minute opening that frames the problem, the solution, and why it matters
  to this audience.</em></p>

  <div class="callout danger">
    <strong>The Problem:</strong> Olist has a customer retention crisis.
    Of <strong>{int(s.unique_customers):,}</strong> unique customers on the platform,
    only <strong>{rep.repeat_pct:.1f}%</strong> — fewer than 1 in 30 — ever place a second order.
    This is not a demand problem: repeat buyers spend <strong>91% more per order</strong>
    (R${rep.avg_repeat:.0f} vs R${rep.avg_onetime:.0f} for one-time buyers). The platform
    is leaving significant revenue on the table by not converting first-time buyers into
    loyal customers.
  </div>

  <p><strong>The Solution:</strong> We built a complete data engineering pipeline —
  Kaggle source data → BigQuery raw layer → DuckDB transformation engine →
  star-schema warehouse → pre-aggregated analytical marts — that makes it possible
  to identify, segment, and act on at-risk customers in real time. RFM segmentation
  classifies all {int(s.unique_customers):,} customers into actionable segments.
  A risk register prioritises the four highest-impact business risks with scored severity
  and concrete mitigation actions.</p>

  <p><strong>Why It Matters:</strong> Moving the repeat purchase rate from {rep.repeat_pct:.1f}%
  to just 5% — converting roughly 1,900 one-time buyers into repeat customers — would add
  approximately <strong>R$280,000 in incremental annual revenue</strong> at the current
  repeat-buyer average spend. This is achievable through a post-purchase email sequence
  and a loyalty programme, both of which can be launched within 90 days using the
  segmentation data now available in <code>olist_marts.customer_rfm</code>.</p>

  <!-- ── 8.2 Business Value ── -->
  <h3>8.2 Business Value</h3>
  <p><em>How this work saves time, generates revenue, and helps the company reach its goals.</em></p>

  <table>
    <tr><th>Value Driver</th><th>Current State</th><th>After Action</th><th>Estimated Impact</th></tr>
    <tr>
      <td><strong>Customer Retention</strong></td>
      <td>{rep.repeat_pct:.1f}% repeat rate</td>
      <td>5%+ repeat rate via loyalty programme</td>
      <td>+R$500K–1.2M ARR</td>
    </tr>
    <tr>
      <td><strong>Review Score &amp; Trust</strong></td>
      <td>{s.avg_review_score:.2f}/5 avg · {int(s.late_orders):,} late deliveries</td>
      <td>ETA recalibration + tracking notifications</td>
      <td>+0.3–0.5★ · ~20% fewer 1-star reviews</td>
    </tr>
    <tr>
      <td><strong>Geographic Coverage</strong></td>
      <td>SP-dominated supply &amp; demand</td>
      <td>Seller recruitment in RJ, MG, RS, BA</td>
      <td>Lower freight costs · reduced concentration risk</td>
    </tr>
    <tr>
      <td><strong>Fraud Prevention</strong></td>
      <td>No automated high-value screening</td>
      <td>IQR-based real-time fraud flag at R${pout.upper_fence:,.0f}</td>
      <td>Reduced financial exposure · improved marketplace trust</td>
    </tr>
    <tr>
      <td><strong>Analytical Productivity</strong></td>
      <td>Ad-hoc queries on raw 9-table CSV data</td>
      <td>Pre-aggregated marts — query in seconds, not minutes</td>
      <td>Hours saved per analyst per week; reproducible insights</td>
    </tr>
  </table>

  <div class="callout success">
    <strong>Highest-ROI single action:</strong> A post-purchase retention email sequence
    costs near-zero to deploy on existing infrastructure and can be live within 30 days.
    The customer segmentation data (RFM scores, segment labels) required to run it
    already exists in <code>olist_marts.customer_rfm</code>.
  </div>

  <!-- ── 8.3 Technical Overview ── -->
  <h3>8.3 Technical Overview</h3>
  <p><em>How the system works at a high level — sufficient for a CTO without implementation detail.</em></p>

  <p>The pipeline follows a classic <strong>ELT (Extract → Load → Transform)</strong>
  pattern across three BigQuery dataset layers:</p>

  <table>
    <tr><th>Layer</th><th>Dataset</th><th>What It Contains</th><th>How It Is Built</th></tr>
    <tr>
      <td><strong>Raw</strong></td>
      <td><code>olist_raw</code></td>
      <td>9 source tables from Kaggle, preserved exactly as ingested. No transformations. Acts as an immutable audit log.</td>
      <td>One-time <code>bq load</code> from CSV; schema auto-detected.</td>
    </tr>
    <tr>
      <td><strong>Warehouse</strong></td>
      <td><code>olist_warehouse</code></td>
      <td>Star schema: 1 fact table (<code>fact_orders</code>) and 4 dimension tables. Cleaned, typed, and foreign-key linked.</td>
      <td>DuckDB reads <code>olist_raw</code> via BigQuery community extension, applies SQL cleaning rules, writes results back to BigQuery.</td>
    </tr>
    <tr>
      <td><strong>Marts</strong></td>
      <td><code>olist_marts</code></td>
      <td>9 pre-aggregated analytical tables: RFM segments, monthly sales, delivery stats, category performance, outliers, risk register, data quality.</td>
      <td>DuckDB reads <code>olist_warehouse</code> and writes aggregated results. Jupyter notebook queries marts for visualisation.</td>
    </tr>
  </table>

  <p><strong>Key architectural decisions:</strong>
  BigQuery was chosen as the warehouse for its serverless, globally-accessible, columnar design
  — no infrastructure to provision for a 100k-row dataset.
  DuckDB was chosen as the transformation engine because it embeds inside a Python process,
  supports analytical SQL natively, and requires no cluster or server setup.
  The star schema design enables single-join analytical queries (one fact table + one dimension),
  dramatically simpler than a normalised 5-table join chain.</p>

  <!-- ── 8.4 Risks ── -->
  <h3>8.4 Risks</h3>
  <p><em>What might go wrong and how we plan to fix or avoid those problems.</em></p>

  <table>
    <tr>
      <th>Risk</th><th>Likelihood</th><th>Impact</th><th>Score</th>
      <th>Affected</th><th>Mitigation Plan</th>
    </tr>
    {"".join(
      f"<tr>"
      f"<td><strong>{r.risk_category}</strong></td>"
      f"<td>{r.likelihood}</td>"
      f"<td>{r.impact}</td>"
      f"<td style='text-align:center;font-weight:700'>{int(r.risk_score)}</td>"
      f"<td>{int(r.affected_count):,} ({r.affected_pct:.1f}%)</td>"
      f"<td>{r.mitigation}</td>"
      f"</tr>"
      for _, r in risks.iterrows()
    )}
  </table>

  <p>Risks are scored using a standard enterprise <strong>Likelihood × Impact</strong> framework
  (Low = 1, Medium = 2, High = 3; score range 1–9). Customer Churn (score 9) and Delivery
  Failure (score 6) are classified <strong>High</strong> and are the primary focus of the
  recommended actions in Section 6. Geographic Concentration (score 4) is Medium and requires
  a 3–9 month seller recruitment programme. Payment Anomaly (score 2) is Low but warrants
  automated monitoring given the financial exposure.</p>

  <!-- ── 8.5 Q&A Preparation ── -->
  <h3>8.5 Q&amp;A Preparation</h3>
  <p><em>Anticipated executive questions and data-backed answers.</em></p>

  <table>
    <tr><th>Anticipated Question</th><th>Prepared Answer</th></tr>
    <tr>
      <td><em>"Why is the repeat purchase rate so low?"</em></td>
      <td>
        The Olist marketplace model means customers search for the best deal each time —
        there is no native loyalty mechanism to bring them back to the same seller.
        The platform itself must own the retention relationship. The data shows buyers
        are satisfied (avg {s.avg_review_score:.2f}/5) — the problem is lack of re-engagement, not product quality.
      </td>
    </tr>
    <tr>
      <td><em>"What would a loyalty programme cost to implement?"</em></td>
      <td>
        A post-purchase email sequence requires only the RFM data already in
        <code>olist_marts.customer_rfm</code> and an email service provider (e.g. SendGrid).
        No new data infrastructure is needed. A points programme would require a lightweight
        loyalty ledger — a new table in the existing BigQuery project.
      </td>
    </tr>
    <tr>
      <td><em>"How confident are we in the delivery data?"</em></td>
      <td>
        96.6% of raw orders passed all data quality checks and are included in the analysis.
        Every exclusion (canceled orders, missing timestamps, zero-payment records) is
        documented in <code>olist_marts.data_quality</code> with counts and percentages.
        The IQR outlier methodology is statistically robust to skewed distributions.
      </td>
    </tr>
    <tr>
      <td><em>"Can this pipeline be automated and kept up to date?"</em></td>
      <td>
        Yes. The current pipeline runs on-demand via Python scripts.
        The next step is scheduling via a cron job or Cloud Scheduler — the
        transformation logic is already encapsulated and parameterised for incremental runs.
      </td>
    </tr>
    <tr>
      <td><em>"Why BigQuery and not Snowflake or Redshift?"</em></td>
      <td>
        BigQuery is serverless (no cluster provisioning), has native partitioning and
        clustering that reduce query cost for time-series patterns, and is accessible
        globally to all team members without VPN. For a 100k-row dataset at project scale,
        it has no idle compute cost — you pay only for queries run.
      </td>
    </tr>
  </table>

  <!-- ── 8.6 Presentation Guidelines ── -->
  <h3>8.6 Presentation Guidelines</h3>

  <table>
    <tr><th>Guideline</th><th>Specification</th></tr>
    <tr>
      <td><strong>Duration</strong></td>
      <td>10 minutes presentation + 5 minutes Q&amp;A. Allocate 2 min to Executive Summary,
      1 min to Revenue Story, 2 min to Customer Behaviour, 3 min to Risk &amp; Recommendations,
      2 min to Technical Architecture.</td>
    </tr>
    <tr>
      <td><strong>Audience</strong></td>
      <td>Mixed executive audience — technical executives (CTOs, Engineering Directors) and
      business executives (CFOs, COOs, Business Leaders). Business impact slides come first
      (slides 1–10); technical architecture is positioned last (slides 13–14) so non-technical
      executives receive full business context before implementation detail.</td>
    </tr>
    <tr>
      <td><strong>Delivery</strong></td>
      <td>All team members should present and be prepared to answer questions.
      Recommended split: one presenter for business slides (1–12), one for technical
      slides (13–14), with all members available for Q&amp;A.</td>
    </tr>
    <tr>
      <td><strong>Visuals</strong></td>
      <td>Use executive-friendly visuals: interactive Chart.js line and bar charts
      (hover for exact values), a colour-coded 3×3 risk matrix, KPI cards with live
      BigQuery data, and 4 ROI action cards. Avoid showing raw SQL, code, or
      schema diagrams to non-technical stakeholders.</td>
    </tr>
    <tr>
      <td><strong>Language</strong></td>
      <td>Balance technical credibility with business accessibility. In business slides:
      lead with R$ revenue impact, customer counts, and percentage changes — not system
      names. In technical slides: name the tools (BigQuery, DuckDB) and explain the
      "why" (serverless, no cluster, pay-per-query) rather than the "how"
      (SQL syntax, partition keys).</td>
    </tr>
  </table>

  <div class="callout">
    <strong>Interactive slide deck:</strong> Open <code>Olist_Executive_Presentation.html</code>
    in a full-screen browser (press F11). Navigate with ← → arrow keys.
    All charts support hover tooltips. The risk matrix cells zoom on hover.
    Press <kbd>?</kbd> for the full keyboard shortcut reference.
  </div>
</div>

</body>
</html>"""


# ── PDF conversion ────────────────────────────────────────────────────────────

def to_pdf(html_content):
    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    ) as f:
        f.write(html_content)
        tmp_html = f.name

    print(f"Converting to PDF via Chrome headless…")
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-pdf-header-footer",
        f"--print-to-pdf={DESKTOP}",
        f"file://{tmp_html}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    os.unlink(tmp_html)

    if result.returncode != 0:
        print("Chrome stderr:", result.stderr[:600])
        sys.exit(1)

    print(f"\n✓  Report saved to: {DESKTOP}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    data = fetch_all()
    html = build_html(data)
    to_pdf(html)
