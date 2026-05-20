"""
Generates an interactive executive stakeholder presentation for the Olist analysis.
Queries BigQuery for live data → Reveal.js HTML → ~/Desktop/Olist_Executive_Presentation.html
Open in any browser; navigate with arrow keys or click.
"""
import json, pathlib, webbrowser
from google.cloud import bigquery
import pandas as pd

BQ     = "dsai-module-2-project-496708"
client = bigquery.Client(project=BQ)
q      = lambda sql: client.query(sql).to_dataframe()

DEST = pathlib.Path.home() / "Desktop" / "Olist_Executive_Presentation.html"


# ── Data fetches ──────────────────────────────────────────────────────────────

def fetch():
    print("Fetching data from BigQuery…")

    kpi = q(f"""
        SELECT
            COUNT(DISTINCT order_id)                                          AS total_orders,
            COUNT(DISTINCT customer_key)                                       AS unique_customers,
            ROUND(SUM(total_payment), 0)                                       AS total_revenue,
            ROUND(AVG(total_payment), 2)                                       AS avg_order_value,
            ROUND(AVG(avg_review_score), 2)                                    AS avg_review_score,
            ROUND(ABS(AVG(CASE WHEN delivery_delay_days IS NOT NULL
                              THEN delivery_delay_days END)), 2)               AS avg_days_early,
            SUM(CASE WHEN delivery_delay_days > 0 THEN 1 ELSE 0 END)          AS late_orders
        FROM `{BQ}.olist_warehouse.fact_orders`
        WHERE order_status NOT IN ('canceled','unavailable')
    """).iloc[0]

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

    rfm = q(f"""
        SELECT segment, COUNT(*) AS n,
               ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 1) AS pct
        FROM `{BQ}.olist_marts.customer_rfm`
        GROUP BY segment ORDER BY n DESC
    """)

    cats = q(f"""
        SELECT category, total_revenue, units_sold, avg_review_score
        FROM `{BQ}.olist_marts.category_performance`
        ORDER BY total_revenue DESC LIMIT 8
    """)

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

    geo = q(f"""
        SELECT customer_state AS state, total_orders AS customers
        FROM `{BQ}.olist_marts.delivery_by_state`
        ORDER BY total_orders DESC LIMIT 8
    """)

    print("  Done.")
    return dict(kpi=kpi, repeat=repeat, monthly=monthly,
                rfm=rfm, cats=cats, risks=risks, geo=geo)


# ── HTML builder ──────────────────────────────────────────────────────────────

def build_html(d):
    kpi     = d["kpi"]
    rep     = d["repeat"]
    monthly = d["monthly"]
    rfm     = d["rfm"]
    cats    = d["cats"]
    risks   = d["risks"]
    geo     = d["geo"]

    # ── monthly labels ──
    if "year" in monthly.columns and "month" in monthly.columns:
        monthly["label"] = monthly.apply(
            lambda r: f"{int(r.year)}-{str(int(r.month)).zfill(2)}", axis=1)
    elif "month_start" in monthly.columns:
        monthly["label"] = pd.to_datetime(monthly["month_start"]).dt.strftime("%Y-%m")
    else:
        monthly["label"] = monthly.index.astype(str)

    peak = monthly.loc[monthly.total_revenue.idxmax()]
    peak_label = peak["label"]

    monthly_labels  = json.dumps(monthly["label"].tolist())
    monthly_revenue = json.dumps(monthly["total_revenue"].round(0).tolist())
    monthly_orders  = json.dumps(monthly["total_orders"].tolist()
                                 if "total_orders" in monthly.columns else [])

    rfm_labels = json.dumps(rfm["segment"].tolist())
    rfm_counts = json.dumps(rfm["n"].tolist())
    rfm_pcts   = json.dumps(rfm["pct"].tolist())

    cat_labels  = json.dumps(cats["category"].str.replace("_", " ").str.title().tolist())
    cat_revenue = json.dumps((cats["total_revenue"] / 1000).round(1).tolist())

    geo_labels   = json.dumps(geo["state"].tolist())
    geo_counts   = json.dumps(geo["customers"].tolist())

    # ── risk matrix ──
    RISK_LVL = {"High": 3, "Medium": 2, "Low": 1}
    risk_lookup = {}
    for _, r in risks.iterrows():
        risk_lookup[(RISK_LVL[r.likelihood], RISK_LVL[r.impact])] = r

    def rm_cell(lh, im):
        score = lh * im
        bg = "#c0392b" if score >= 6 else ("#e67e22" if score >= 3 else "#27ae60")
        r  = risk_lookup.get((lh, im))
        if r is not None:
            inner = (f'<span class="rm-name">{r.risk_category}</span>'
                     f'<span class="rm-score">Score {int(r.risk_score)}</span>')
        else:
            inner = f'<span class="rm-score">{score}</span>'
        return f'<div class="rm-cell" style="background:{bg}">{inner}</div>'

    matrix_cells = "".join(
        rm_cell(lh, im)
        for im in [3, 2, 1]
        for lh in [1, 2, 3]
    )

    # ── RFM segment rows ──
    seg_rows = "".join(
        f'<tr><td>{r.segment}</td>'
        f'<td style="font-weight:700">{int(r.n):,}</td>'
        f'<td>{r.pct:.1f}%</td></tr>'
        for _, r in rfm.iterrows()
    )

    # ── CSS (not f-string — contains many {}) ──
    CSS = """
:root {
  --bg: #0f1923;
  --gold: #f39c12;
  --blue: #3498db;
  --green: #2ecc71;
  --red: #e74c3c;
  --card: rgba(255,255,255,0.06);
  --border: rgba(255,255,255,0.12);
  --text: #f0f4f8;
  --muted: rgba(240,244,248,0.62);
}
.reveal-viewport { background: var(--bg); }
.reveal { font-family: -apple-system,'Helvetica Neue',Arial,sans-serif; color: var(--text); }
.reveal .slides section { text-align: left; }
.reveal h1,.reveal h2,.reveal h3 { text-transform: none; letter-spacing: -0.01em; }
.reveal a { color: var(--gold); }

/* ── COVER ── */
.slide-cover {
  display: flex; flex-direction: column; justify-content: center; align-items: flex-start;
  height: 100vh; padding: 0 80px;
  background: linear-gradient(135deg, #0f1923 0%, #1a2940 60%, #0f2033 100%);
}
.cover-eyebrow {
  font-size: 12px; letter-spacing: 4px; text-transform: uppercase;
  color: var(--gold); font-weight: 700; margin-bottom: 20px;
}
.cover-title {
  font-size: 52px; font-weight: 800; line-height: 1.1;
  color: #fff; margin-bottom: 18px;
}
.cover-title span { color: var(--gold); }
.cover-sub {
  font-size: 20px; color: var(--muted); max-width: 680px;
  line-height: 1.5; margin-bottom: 48px;
}
.cover-divider { width: 64px; height: 3px; background: var(--gold); margin-bottom: 32px; }
.cover-meta { font-size: 13px; color: rgba(240,244,248,0.4); line-height: 2.2; }
.cover-stat-row { display: flex; gap: 40px; margin-top: 48px; }
.cover-stat .num { font-size: 32px; font-weight: 800; color: var(--gold); }
.cover-stat .lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }

/* ── SLIDE CHROME ── */
.slide-label {
  font-size: 10px; letter-spacing: 3px; text-transform: uppercase;
  color: var(--gold); font-weight: 700; margin-bottom: 8px;
}
.slide-title {
  font-size: 34px; font-weight: 800; color: #fff;
  margin-bottom: 28px; line-height: 1.15;
  border-bottom: 2px solid rgba(243,156,18,0.3);
  padding-bottom: 14px;
}
.slide-title span { color: var(--gold); }

/* ── AGENDA ── */
.agenda-list { list-style: none; padding: 0; margin: 0; }
.agenda-list li {
  display: flex; align-items: center; gap: 20px;
  padding: 14px 20px; margin-bottom: 10px;
  background: var(--card); border-radius: 10px;
  border-left: 4px solid var(--gold);
  font-size: 18px; font-weight: 500;
}
.agenda-list li .num {
  font-size: 24px; font-weight: 800; color: var(--gold); min-width: 32px;
}
.agenda-list li .time {
  margin-left: auto; font-size: 12px; color: var(--muted);
}

/* ── KPI CARDS ── */
.kpi-grid {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;
}
.kpi-card {
  background: var(--card); border-radius: 12px;
  padding: 20px 16px; border-top: 3px solid var(--gold);
  border: 1px solid var(--border); border-top: 3px solid var(--gold);
}
.kpi-card .val {
  font-size: 28px; font-weight: 800; color: #fff; line-height: 1;
}
.kpi-card .lbl {
  font-size: 10px; color: var(--muted); text-transform: uppercase;
  letter-spacing: 1px; margin-top: 6px;
}
.kpi-card.accent { border-top-color: var(--red); }
.kpi-card.accent .val { color: var(--red); }

/* ── HEADLINE CALLOUT ── */
.callout {
  background: rgba(52,152,219,0.12); border-left: 4px solid var(--blue);
  padding: 16px 20px; border-radius: 0 10px 10px 0; margin: 20px 0;
  font-size: 15px;
}
.callout.danger { background: rgba(231,76,60,0.12); border-color: var(--red); }
.callout.success { background: rgba(46,204,113,0.12); border-color: var(--green); }
.callout.gold { background: rgba(243,156,18,0.12); border-color: var(--gold); }
.callout strong { color: #fff; }

/* ── BIG STAT ── */
.big-stat { text-align: center; padding: 40px 0; }
.big-stat .number {
  font-size: 120px; font-weight: 900; line-height: 1;
  color: var(--red); display: block;
}
.big-stat .label {
  font-size: 22px; color: var(--muted); margin-top: 12px; display: block;
}
.big-stat .context { font-size: 16px; color: var(--muted); margin-top: 8px; }

/* ── CHART SLIDE ── */
.chart-wrap { position: relative; width: 100%; }
.chart-caption {
  font-size: 11px; color: var(--muted); text-align: center;
  margin-top: 6px; font-style: italic;
}

/* ── TWO COLUMN ── */
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 28px; align-items: start; }
.three-col { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }

/* ── RISK MATRIX ── */
.rm-wrapper { display: flex; gap: 28px; align-items: center; }
.rm-grid {
  display: grid; grid-template-columns: repeat(3, 1fr);
  grid-template-rows: repeat(3, 1fr); gap: 6px;
  width: 340px; height: 280px; flex-shrink: 0;
}
.rm-cell {
  border-radius: 8px; padding: 10px 8px;
  display: flex; flex-direction: column; justify-content: center; align-items: center;
  text-align: center; transition: transform 0.15s;
}
.rm-cell:hover { transform: scale(1.04); z-index: 10; }
.rm-name { font-size: 11px; font-weight: 700; color: #fff; line-height: 1.3; }
.rm-score { font-size: 10px; color: rgba(255,255,255,0.75); margin-top: 4px; }
.rm-axis { display: flex; flex-direction: column; justify-content: space-around; }
.rm-axis-label { font-size: 10px; color: var(--muted); text-align: center; width: 24px; }
.rm-bottom { display: flex; justify-content: space-around; margin-top: 4px; padding-left: 30px; }
.rm-bottom span { font-size: 10px; color: var(--muted); width: 106px; text-align: center; }
.rm-title { font-size: 11px; color: var(--muted); }
.rm-legend { margin-top: 12px; }
.rm-legend-item { display: flex; align-items: center; gap: 8px; font-size: 12px; margin-bottom: 8px; }
.rm-legend-dot { width: 14px; height: 14px; border-radius: 3px; flex-shrink: 0; }
.rm-detail { flex: 1; }
.rm-detail-item {
  background: var(--card); border-radius: 8px; padding: 12px 14px;
  margin-bottom: 10px; border-left: 4px solid #555;
}
.rm-detail-item.high { border-color: var(--red); }
.rm-detail-item.med  { border-color: #e67e22; }
.rm-detail-item.low  { border-color: var(--green); }
.rm-detail-item h4 { font-size: 12px; color: #fff; margin: 0 0 4px; }
.rm-detail-item p { font-size: 11px; color: var(--muted); margin: 0; line-height: 1.5; }

/* ── REC CARDS ── */
.rec-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.rec-card {
  background: var(--card); border-radius: 10px; padding: 18px;
  border-left: 5px solid #555;
}
.rec-card.p1 { border-color: var(--red); }
.rec-card.p2 { border-color: #e67e22; }
.rec-card.p3 { border-color: var(--blue); }
.rec-card.p4 { border-color: var(--green); }
.rec-card .pri { font-size: 10px; text-transform: uppercase; letter-spacing: 2px; color: var(--muted); }
.rec-card h4 { font-size: 14px; color: #fff; margin: 6px 0 8px; }
.rec-card .detail { font-size: 12px; color: var(--muted); line-height: 1.6; }
.rec-card .roi { font-size: 11px; color: var(--gold); font-weight: 700; margin-top: 8px; }
.rec-card .timeframe { font-size: 10px; color: var(--muted); }

/* ── TECH ARCH ── */
.arch-box {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px; text-align: center;
}
.arch-box h4 { font-size: 11px; font-weight: 700; color: #fff; margin: 0 0 4px; }
.arch-box p { font-size: 10px; color: var(--muted); margin: 0; }
.arch-arrow { display: flex; align-items: center; justify-content: center; font-size: 22px; color: var(--muted); }
.arch-row { display: flex; align-items: center; gap: 8px; }

/* ── TIMELINE ── */
.timeline { position: relative; padding-left: 28px; margin-top: 16px; }
.timeline::before { content: ''; position: absolute; left: 8px; top: 0; bottom: 0; width: 2px; background: var(--border); }
.tl-item { position: relative; padding: 0 0 24px 20px; }
.tl-dot { position: absolute; left: -24px; top: 4px; width: 12px; height: 12px; border-radius: 50%; background: var(--gold); border: 2px solid var(--bg); }
.tl-item h4 { font-size: 14px; color: #fff; margin: 0 0 4px; }
.tl-item p { font-size: 12px; color: var(--muted); margin: 0; }
.tl-item .when { font-size: 10px; color: var(--gold); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }

/* ── TABLES ── */
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th {
  background: rgba(243,156,18,0.15); color: var(--gold);
  padding: 8px 10px; text-align: left; font-size: 10px;
  text-transform: uppercase; letter-spacing: 1px; font-weight: 700;
}
.data-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); color: var(--text); }
.data-table tr:hover td { background: rgba(255,255,255,0.04); }

/* ── Q&A SLIDE ── */
.qa-slide { text-align: center; padding: 60px 40px; }
.qa-slide h1 { font-size: 72px; font-weight: 900; color: var(--gold); margin-bottom: 16px; }
.qa-slide p { font-size: 20px; color: var(--muted); }
.qa-links { display: flex; gap: 20px; justify-content: center; margin-top: 40px; flex-wrap: wrap; }
.qa-link-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 24px; text-align: center; min-width: 160px;
}
.qa-link-card .icon { font-size: 24px; margin-bottom: 6px; }
.qa-link-card p { font-size: 12px; color: var(--muted); margin: 0; }
.qa-link-card strong { font-size: 13px; color: #fff; display: block; margin-bottom: 2px; }

/* ── PROGRESS BAR ── */
.reveal .progress { color: var(--gold); }
.reveal .controls { color: var(--gold); }

/* ── FRAGMENTS ── */
.reveal .fragment.highlight-gold { opacity: 1; }
.reveal .fragment.highlight-gold.visible { color: var(--gold); }

/* ── VALUE PILL ── */
.pill {
  display: inline-block; padding: 4px 10px; border-radius: 20px;
  font-size: 12px; font-weight: 700;
}
.pill.green { background: rgba(46,204,113,0.2); color: var(--green); }
.pill.red   { background: rgba(231,76,60,0.2);  color: var(--red); }
.pill.gold  { background: rgba(243,156,18,0.2); color: var(--gold); }
.pill.blue  { background: rgba(52,152,219,0.2); color: var(--blue); }

/* ── VALUE HIGHLIGHT ── */
.val-big { font-size: 42px; font-weight: 900; color: var(--gold); }
.val-label { font-size: 13px; color: var(--muted); margin-top: 4px; }
.stat-trio { display: flex; gap: 0; }
.stat-item { flex: 1; padding: 16px; border-right: 1px solid var(--border); }
.stat-item:last-child { border-right: none; }
.stat-item .v { font-size: 32px; font-weight: 800; color: var(--gold); }
.stat-item .l { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }
"""

    # ── JS Chart setup ──
    JS = (
        "const MONTHLY_LABELS = MONTHLY_LABELS_PLACEHOLDER;\n"
        "const MONTHLY_REVENUE = MONTHLY_REVENUE_PLACEHOLDER;\n"
        "const MONTHLY_ORDERS = MONTHLY_ORDERS_PLACEHOLDER;\n"
        "const RFM_LABELS = RFM_LABELS_PLACEHOLDER;\n"
        "const RFM_COUNTS = RFM_COUNTS_PLACEHOLDER;\n"
        "const CAT_LABELS = CAT_LABELS_PLACEHOLDER;\n"
        "const CAT_REVENUE = CAT_REVENUE_PLACEHOLDER;\n"
        "const GEO_LABELS = GEO_LABELS_PLACEHOLDER;\n"
        "const GEO_COUNTS = GEO_COUNTS_PLACEHOLDER;\n"
    ).replace("MONTHLY_LABELS_PLACEHOLDER",  monthly_labels) \
     .replace("MONTHLY_REVENUE_PLACEHOLDER", monthly_revenue) \
     .replace("MONTHLY_ORDERS_PLACEHOLDER",  monthly_orders) \
     .replace("RFM_LABELS_PLACEHOLDER",      rfm_labels) \
     .replace("RFM_COUNTS_PLACEHOLDER",      rfm_counts) \
     .replace("CAT_LABELS_PLACEHOLDER",      cat_labels) \
     .replace("CAT_REVENUE_PLACEHOLDER",     cat_revenue) \
     .replace("GEO_LABELS_PLACEHOLDER",      geo_labels) \
     .replace("GEO_COUNTS_PLACEHOLDER",      geo_counts)

    CHART_JS = """
const CHART_DEFAULTS = {
  color: 'rgba(240,244,248,0.75)',
  plugins: { legend: { labels: { color: 'rgba(240,244,248,0.75)', font: { size: 12 } } } },
  scales: {
    x: { ticks: { color: 'rgba(240,244,248,0.55)', font: { size: 11 } }, grid: { color: 'rgba(255,255,255,0.06)' } },
    y: { ticks: { color: 'rgba(240,244,248,0.55)', font: { size: 11 } }, grid: { color: 'rgba(255,255,255,0.06)' } }
  }
};

function mkLine(id, labels, data, label, color) {
  const ctx = document.getElementById(id);
  if (!ctx) return;
  new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{ label, data, borderColor: color, backgroundColor: color + '22',
                   fill: true, tension: 0.35, pointRadius: 3, borderWidth: 2.5 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false },
                 tooltip: { callbacks: { label: ctx => ' R$ ' + Number(ctx.raw).toLocaleString('pt-BR') } } },
      scales: CHART_DEFAULTS.scales
    }
  });
}

function mkHBar(id, labels, data, colors, unit) {
  const ctx = document.getElementById(id);
  if (!ctx) return;
  new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [{ data, backgroundColor: colors, borderRadius: 5 }] },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ' ' + unit + Number(ctx.raw).toLocaleString('pt-BR') } }
      },
      scales: {
        x: { ticks: { color: 'rgba(240,244,248,0.55)', font: { size: 11 } }, grid: { color: 'rgba(255,255,255,0.06)' } },
        y: { ticks: { color: 'rgba(240,244,248,0.75)', font: { size: 12 } }, grid: { display: false } }
      }
    }
  });
}

function mkDoughnut(id, labels, data) {
  const ctx = document.getElementById(id);
  if (!ctx) return;
  const palette = ['#f39c12','#3498db','#2ecc71','#e74c3c','#9b59b6','#1abc9c','#e67e22','#34495e'];
  new Chart(ctx, {
    type: 'doughnut',
    data: { labels, datasets: [{ data, backgroundColor: palette, borderWidth: 0, hoverOffset: 8 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: '62%',
      plugins: {
        legend: { position: 'right', labels: { color: 'rgba(240,244,248,0.75)', font: { size: 12 }, boxWidth: 14 } },
        tooltip: { callbacks: { label: ctx => ' ' + ctx.label + ': ' + ctx.raw.toLocaleString('pt-BR') + ' customers' } }
      }
    }
  });
}

function mkVBar(id, labels, data, colors) {
  const ctx = document.getElementById(id);
  if (!ctx) return;
  new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [{ data, backgroundColor: colors, borderRadius: 6 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false },
                 tooltip: { callbacks: { label: ctx => ' R$ ' + Number(ctx.raw).toLocaleString('pt-BR') } } },
      scales: CHART_DEFAULTS.scales
    }
  });
}

Reveal.on('ready', () => {
  mkLine('chart-revenue', MONTHLY_LABELS, MONTHLY_REVENUE, 'Revenue', '#f39c12');

  const rfmColors = ['#f39c12','#3498db','#2ecc71','#e74c3c','#9b59b6','#1abc9c','#e67e22','#34495e'];
  mkHBar('chart-rfm', RFM_LABELS, RFM_COUNTS, rfmColors.slice(0, RFM_LABELS.length), '');

  const catGradient = CAT_LABELS.map((_, i) => `hsla(${210 + i*18}, 70%, ${60 - i*4}%, 0.85)`);
  mkHBar('chart-cats', CAT_LABELS, CAT_REVENUE, catGradient, 'R$K ');

  mkVBar('chart-geo', GEO_LABELS, GEO_COUNTS, GEO_LABELS.map(() => 'rgba(52,152,219,0.75)'));
});
"""

    # ── Formatted values ──
    total_orders      = f"{int(kpi.total_orders):,}"
    unique_customers  = f"{int(kpi.unique_customers):,}"
    total_revenue_m   = f"R${int(kpi.total_revenue)/1e6:.2f}M"
    avg_order_value   = f"R${kpi.avg_order_value:.0f}"
    avg_review        = f"{kpi.avg_review_score:.2f}"
    late_orders       = f"{int(kpi.late_orders):,}"
    repeat_pct        = f"{rep.repeat_pct:.1f}%"
    repeat_pct_raw    = float(rep.repeat_pct)
    churn_pct         = f"{100 - float(rep.repeat_pct):.1f}"
    avg_repeat_spend  = f"R${rep.avg_repeat:.0f}"
    avg_onetime_spend = f"R${rep.avg_onetime:.0f}"
    repeat_buyers     = f"{int(rep.repeat_buyers):,}"

    risk_rows = "".join(
        f'<tr><td><strong>{r.risk_category}</strong></td>'
        f'<td><span class="pill {"red" if r.likelihood=="High" else "gold" if r.likelihood=="Medium" else "green"}">'
        f'{r.likelihood}</span></td>'
        f'<td><span class="pill {"red" if r.impact=="High" else "gold" if r.impact=="Medium" else "green"}">'
        f'{r.impact}</span></td>'
        f'<td style="font-weight:700;color:{"#e74c3c" if r.risk_level=="High" else "#e67e22" if r.risk_level=="Medium" else "#2ecc71"}">'
        f'{int(r.risk_score)}</td>'
        f'<td>{r.mitigation[:60]}…</td></tr>'
        for _, r in risks.iterrows()
    )

    # ── Assemble HTML ──
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Olist Executive Presentation | NTU DSAI M2</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@4.6.1/dist/reset.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@4.6.1/dist/reveal.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
{CSS}
</style>
</head>
<body>
<div class="reveal">
<div class="slides">

<!-- ═══════════════════════════ SLIDE 1 — COVER ════════════════════════════ -->
<section data-background-gradient="linear-gradient(135deg,#0a1520 0%,#1a2940 55%,#0f2033 100%)">
  <div class="slide-cover">
    <div class="cover-eyebrow">NTU DSAI Module 2 · Executive Stakeholder Presentation</div>
    <h1 class="cover-title">
      Olist Brazilian<br>
      <span>E-Commerce Analysis</span>
    </h1>
    <p class="cover-sub">
      Why {churn_pct}% of buyers never come back —
      and the data-driven roadmap to change it.
    </p>
    <div class="cover-divider"></div>
    <div class="cover-stat-row">
      <div class="cover-stat"><div class="num">{total_orders}</div><div class="lbl">Orders Analysed</div></div>
      <div class="cover-stat"><div class="num">{total_revenue_m}</div><div class="lbl">Total Revenue</div></div>
      <div class="cover-stat"><div class="num">Sep 2016 – Oct 2018</div><div class="lbl">Dataset Period</div></div>
    </div>
    <div class="cover-meta" style="margin-top:36px">
      BigQuery · {BQ}<br>
      Pipeline: Kaggle CSV → olist_raw → DuckDB → olist_warehouse → olist_marts
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 2 — AGENDA ═══════════════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Today's Agenda</div>
    <h2 class="slide-title">What We'll Cover</h2>
    <ul class="agenda-list">
      <li><span class="num">01</span> Business Problem &amp; Key Metrics <span class="time">~2 min</span></li>
      <li><span class="num">02</span> Revenue &amp; Growth Story <span class="time">~1 min</span></li>
      <li><span class="num">03</span> Customer Behaviour &amp; Segmentation <span class="time">~2 min</span></li>
      <li><span class="num">04</span> Risk Landscape &amp; Priority Actions <span class="time">~3 min</span></li>
      <li><span class="num">05</span> Technical Architecture &amp; Pipeline <span class="time">~2 min</span></li>
    </ul>
    <div class="callout gold" style="margin-top:28px">
      <strong>Format:</strong> 10-minute presentation · 5-minute Q&amp;A · Slides are interactive — charts support hover tooltips.
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 3 — THE PROBLEM ══════════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 01 · Business Problem</div>
    <h2 class="slide-title">The Retention <span>Crisis</span></h2>
    <div class="two-col">
      <div>
        <div class="big-stat">
          <span class="number">{churn_pct}%</span>
          <span class="label">of customers <strong style="color:#fff">never return</strong></span>
          <p class="context">Only <strong style="color:#f39c12">{repeat_pct}</strong> ever place a second order.</p>
        </div>
      </div>
      <div>
        <div class="callout danger" style="margin-bottom:16px">
          <strong>Why this matters:</strong><br>
          Repeat buyers spend <strong style="color:#f39c12">91% more per order</strong><br>
          ({avg_repeat_spend} vs {avg_onetime_spend} for one-time buyers).
        </div>
        <div class="callout gold">
          <strong>The opportunity:</strong><br>
          Moving retention from {repeat_pct} to just 5% —
          converting ~1,900 one-time buyers into repeat customers —
          would add approximately <strong style="color:#f39c12">R$280,000</strong> in incremental annual revenue.
        </div>
        <div style="margin-top:20px;background:var(--card);border-radius:10px;padding:16px">
          <div style="display:flex;gap:20px">
            <div style="flex:1;text-align:center">
              <div style="font-size:28px;font-weight:800;color:#e74c3c">{repeat_pct}</div>
              <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px">Repeat Buyers</div>
            </div>
            <div style="flex:1;text-align:center">
              <div style="font-size:28px;font-weight:800;color:#2ecc71">{avg_repeat_spend}</div>
              <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px">Avg Repeat Spend</div>
            </div>
            <div style="flex:1;text-align:center">
              <div style="font-size:28px;font-weight:800;color:#3498db">{avg_onetime_spend}</div>
              <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px">Avg One-Time Spend</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 4 — KPI DASHBOARD ═══════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 01 · Key Metrics</div>
    <h2 class="slide-title">Data at a <span>Glance</span></h2>
    <div class="kpi-grid">
      <div class="kpi-card"><div class="val">{total_orders}</div><div class="lbl">Total Orders</div></div>
      <div class="kpi-card"><div class="val">{unique_customers}</div><div class="lbl">Unique Customers</div></div>
      <div class="kpi-card"><div class="val">{total_revenue_m}</div><div class="lbl">Total Revenue</div></div>
      <div class="kpi-card"><div class="val">{avg_order_value}</div><div class="lbl">Avg Order Value</div></div>
      <div class="kpi-card accent"><div class="val">{repeat_pct}</div><div class="lbl">Repeat Buyers ⚠</div></div>
      <div class="kpi-card"><div class="val">{avg_review}</div><div class="lbl">Avg Review Score / 5</div></div>
      <div class="kpi-card"><div class="val">{late_orders}</div><div class="lbl">Late Deliveries</div></div>
      <div class="kpi-card"><div class="val">Sep 2016</div><div class="lbl">Dataset Start</div></div>
    </div>
    <div class="callout success" style="margin-top:16px">
      <strong>Data quality:</strong> 96.6% of raw orders passed all cleaning filters and are included in this analysis.
      All exclusions are logged and auditable in <code>olist_marts.data_quality</code>.
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 5 — REVENUE STORY ════════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 02 · Revenue Growth</div>
    <h2 class="slide-title">Strong Growth — <span>Peaking in {peak_label}</span></h2>
    <div class="chart-wrap" style="height:340px">
      <canvas id="chart-revenue"></canvas>
    </div>
    <p class="chart-caption">Monthly revenue (R$) — Sep 2016 to Oct 2018. Hover for exact values.</p>
    <div class="callout gold" style="margin-top:12px">
      The platform grew 4× from Q4 2016 to its peak, driven by organic marketplace expansion.
      The primary growth lever now shifts from acquisition to <strong>retention</strong>.
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 6 — RFM SEGMENTS ════════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 03 · Customer Behaviour</div>
    <h2 class="slide-title">Customer <span>Segmentation</span></h2>
    <div class="two-col">
      <div>
        <div class="chart-wrap" style="height:300px">
          <canvas id="chart-rfm"></canvas>
        </div>
        <p class="chart-caption">RFM segments by customer count. Hover for values.</p>
      </div>
      <div>
        <div class="callout danger" style="margin-bottom:14px">
          <strong>50%+ "at risk":</strong> "At Risk" and "Needs Attention" segments combined represent
          over half the customer base — buyers who purchased before but haven't returned.
        </div>
        <table class="data-table">
          <thead><tr><th>Segment</th><th>Customers</th><th>Share</th></tr></thead>
          <tbody>{seg_rows}</tbody>
        </table>
      </div>
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 7 — TOP CATEGORIES ══════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 03 · Product Mix</div>
    <h2 class="slide-title">Revenue by <span>Category</span></h2>
    <div class="chart-wrap" style="height:340px">
      <canvas id="chart-cats"></canvas>
    </div>
    <p class="chart-caption">Top 8 categories by total revenue (R$ thousands). Hover for values.</p>
    <div class="callout" style="margin-top:12px">
      <strong>Repeat-purchase opportunity:</strong> Health &amp; Beauty, Watches, and Bed/Bath are
      natural subscription and loyalty-reward categories — high-frequency consumables with strong
      review scores above 4.0.
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 8 — GEOGRAPHIC ══════════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 03 · Geographic Concentration</div>
    <h2 class="slide-title">Geographic <span>Expansion Opportunity</span></h2>
    <div class="two-col">
      <div>
        <div class="chart-wrap" style="height:300px">
          <canvas id="chart-geo"></canvas>
        </div>
        <p class="chart-caption">Top states by customer count.</p>
      </div>
      <div>
        <div class="callout danger" style="margin-bottom:14px">
          <strong>São Paulo concentration risk:</strong> SP dominates both customers and sellers,
          creating a single point of fragility in the supply and demand network.
        </div>
        <div class="callout gold">
          <strong>Underserved markets:</strong> RJ, MG, RS and BA have customer-to-seller ratios
          of <strong>20× or higher</strong> — substantial unmet demand that targeted seller
          recruitment can address.
        </div>
        <div class="callout" style="margin-top:14px">
          <strong>Freight cost reduction:</strong> Local sellers in high-demand states
          reduce logistics distance and freight costs for buyers outside SP.
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 9 — DELIVERY ════════════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 03 · Delivery Performance</div>
    <h2 class="slide-title">Delivery <span>Communication Gap</span></h2>
    <div class="three-col" style="margin-bottom:20px">
      <div style="background:var(--card);border-radius:10px;padding:20px;text-align:center">
        <div style="font-size:36px;font-weight:800;color:#2ecc71">{abs(float(kpi.avg_days_early)):.1f}d</div>
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-top:6px">Avg Days Early</div>
      </div>
      <div style="background:var(--card);border-radius:10px;padding:20px;text-align:center">
        <div style="font-size:36px;font-weight:800;color:#e74c3c">{late_orders}</div>
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-top:6px">Late Deliveries</div>
      </div>
      <div style="background:var(--card);border-radius:10px;padding:20px;text-align:center">
        <div style="font-size:36px;font-weight:800;color:#f39c12">{avg_review} / 5</div>
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-top:6px">Avg Review Score</div>
      </div>
    </div>
    <div class="callout danger" style="margin-bottom:14px">
      <strong>The paradox:</strong> Deliveries arrive early on average, yet thousands of
      orders are still classified as late — customers' expectations are set too optimistically
      relative to actual delivery windows.
    </div>
    <div class="callout">
      <strong>Key insight:</strong> Even early-arriving orders attract 1-star reviews —
      a communication problem, not a logistics problem. Recalibrating ETA windows and
      adding proactive tracking would improve satisfaction scores without changing logistics.
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 10 — BUSINESS VALUE ══════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 03 · Business Value</div>
    <h2 class="slide-title">What This Analysis <span>Unlocks</span></h2>
    <div class="two-col">
      <div>
        <div style="background:var(--card);border-radius:12px;padding:24px;margin-bottom:16px;border-left:4px solid var(--green)">
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:2px;color:var(--muted);margin-bottom:8px">Revenue Impact</div>
          <div style="font-size:32px;font-weight:800;color:var(--green)">+R$280K</div>
          <div style="font-size:13px;color:var(--muted);margin-top:6px">Est. incremental annual revenue from moving retention rate from {repeat_pct} to 5%</div>
        </div>
        <div style="background:var(--card);border-radius:12px;padding:24px;border-left:4px solid var(--blue)">
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:2px;color:var(--muted);margin-bottom:8px">Review Score Uplift</div>
          <div style="font-size:32px;font-weight:800;color:var(--blue)">+0.3–0.5★</div>
          <div style="font-size:13px;color:var(--muted);margin-top:6px">Expected from ETA recalibration + proactive tracking, ~20% fewer 1-star reviews</div>
        </div>
      </div>
      <div>
        <div style="background:var(--card);border-radius:12px;padding:24px;margin-bottom:16px;border-left:4px solid var(--gold)">
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:2px;color:var(--muted);margin-bottom:8px">Seller Expansion</div>
          <div style="font-size:32px;font-weight:800;color:var(--gold)">4 States</div>
          <div style="font-size:13px;color:var(--muted);margin-top:6px">RJ, MG, RS, BA have 20× customer-to-seller ratios — targeted recruitment reduces freight costs &amp; improves service coverage</div>
        </div>
        <div style="background:var(--card);border-radius:12px;padding:24px;border-left:4px solid var(--red)">
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:2px;color:var(--muted);margin-bottom:8px">Fraud Prevention</div>
          <div style="font-size:32px;font-weight:800;color:var(--red)">Real-Time</div>
          <div style="font-size:13px;color:var(--muted);margin-top:6px">Automated IQR-based fraud scoring flags high-value orders for manual review before fulfilment</div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 11 — RISK MATRIX ═════════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 04 · Risk Landscape</div>
    <h2 class="slide-title">Risk <span>Assessment</span></h2>
    <div class="rm-wrapper">
      <div>
        <div style="font-size:10px;color:var(--muted);text-align:center;margin-bottom:4px;text-transform:uppercase;letter-spacing:1px">Impact ↑</div>
        <div style="display:flex;gap:6px">
          <div class="rm-axis">
            <div class="rm-axis-label">H</div>
            <div class="rm-axis-label">M</div>
            <div class="rm-axis-label">L</div>
          </div>
          <div class="rm-grid">
            {matrix_cells}
          </div>
        </div>
        <div class="rm-bottom">
          <span>Low</span><span>Medium</span><span>High</span>
        </div>
        <div style="font-size:10px;color:var(--muted);text-align:center;margin-top:2px;text-transform:uppercase;letter-spacing:1px">Likelihood →</div>
        <div class="rm-legend" style="margin-top:12px">
          <div class="rm-legend-item"><div class="rm-legend-dot" style="background:#c0392b"></div>High Risk (score ≥ 6)</div>
          <div class="rm-legend-item"><div class="rm-legend-dot" style="background:#e67e22"></div>Medium Risk (score 3–5)</div>
          <div class="rm-legend-item"><div class="rm-legend-dot" style="background:#27ae60"></div>Low Risk (score 1–2)</div>
        </div>
      </div>
      <div class="rm-detail">
        <table class="data-table" style="font-size:12px">
          <thead><tr><th>Risk</th><th>Likelihood</th><th>Impact</th><th>Score</th><th>Mitigation</th></tr></thead>
          <tbody>{risk_rows}</tbody>
        </table>
      </div>
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 12 — RECOMMENDATIONS ════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 04 · Priority Actions</div>
    <h2 class="slide-title">Our <span>Recommendations</span></h2>
    <div class="rec-grid">
      <div class="rec-card p1">
        <div class="pri">Priority 1 · Critical · 0–3 months</div>
        <h4>Customer Retention Programme</h4>
        <div class="detail">
          Post-purchase email sequence (day 7, 30, 90) + loyalty points programme
          targeting the "At Risk" &amp; "Needs Attention" segments.
        </div>
        <div class="roi">Expected: +2–5% repeat rate → +R$500K–1.2M ARR</div>
      </div>
      <div class="rec-card p2">
        <div class="pri">Priority 2 · Critical · 0–2 months</div>
        <h4>Delivery ETA Recalibration</h4>
        <div class="detail">
          Shorten stated delivery windows to achievable ranges + proactive
          SMS/email updates at dispatch, in-transit, and out-for-delivery stages.
        </div>
        <div class="roi">Expected: +0.3–0.5★ review score · ~20% fewer 1-star reviews</div>
      </div>
      <div class="rec-card p3">
        <div class="pri">Priority 3 · High · 3–9 months</div>
        <h4>Regional Seller Expansion</h4>
        <div class="detail">
          Subsidised seller onboarding in RJ, MG, RS, BA to reduce 20×
          customer-to-seller ratios and lower freight costs for non-SP buyers.
        </div>
        <div class="roi">Expected: Reduced freight costs · improved coverage</div>
      </div>
      <div class="rec-card p4">
        <div class="pri">Priority 4 · Medium · 1–3 months</div>
        <h4>Payment Fraud Scoring</h4>
        <div class="detail">
          Real-time IQR-based fraud flag for orders above the upper fence.
          Secondary OTP verification for high-value instalment orders.
        </div>
        <div class="roi">Expected: Reduced financial exposure · platform trust</div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 13 — TECH ARCH ══════════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 05 · Technical Architecture</div>
    <h2 class="slide-title">The <span>Data Pipeline</span></h2>
    <div class="arch-row" style="margin-bottom:24px">
      <div class="arch-box" style="flex:1;border-top:3px solid #3498db">
        <h4 style="color:#3498db">01 · SOURCE</h4>
        <p>Kaggle CSV Files<br>9 raw tables<br>~100k orders</p>
      </div>
      <div class="arch-arrow">→</div>
      <div class="arch-box" style="flex:1;border-top:3px solid #f39c12">
        <h4 style="color:#f39c12">02 · INGEST</h4>
        <p>BigQuery<br>olist_raw dataset<br>As-is, no transforms</p>
      </div>
      <div class="arch-arrow">→</div>
      <div class="arch-box" style="flex:1;border-top:3px solid #e67e22;border-width:3px">
        <h4 style="color:#e67e22">03 · TRANSFORM</h4>
        <p>DuckDB (in-memory)<br>SQL joins + cleaning<br>+ BigQuery extension</p>
      </div>
      <div class="arch-arrow">→</div>
      <div class="arch-box" style="flex:1;border-top:3px solid #2ecc71">
        <h4 style="color:#2ecc71">04 · WAREHOUSE</h4>
        <p>BigQuery<br>olist_warehouse<br>Star schema · 5 tables</p>
      </div>
      <div class="arch-arrow">→</div>
      <div class="arch-box" style="flex:1;border-top:3px solid #9b59b6">
        <h4 style="color:#9b59b6">05 · MARTS</h4>
        <p>BigQuery<br>olist_marts<br>9 analytical views</p>
      </div>
    </div>
    <div class="two-col">
      <div>
        <div class="callout" style="margin-bottom:14px">
          <strong>Why BigQuery?</strong> Serverless, columnar, globally accessible,
          pay-per-query — no infrastructure to provision for a 100k-row dataset.
        </div>
        <div class="callout gold">
          <strong>Why DuckDB?</strong> Embeds in Python with no cluster setup;
          reads/writes BigQuery via community extension; 10–100× faster than
          pandas for complex multi-table joins.
        </div>
      </div>
      <div>
        <div style="background:var(--card);border-radius:10px;padding:16px">
          <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:12px">Tech Stack</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px">
            <div><span class="pill blue">BigQuery</span> Warehouse</div>
            <div><span class="pill gold">DuckDB</span> Transform</div>
            <div><span class="pill green">Python 3.12</span> Orchestration</div>
            <div><span class="pill blue">Jupyter</span> Analysis</div>
            <div><span class="pill gold">Matplotlib</span> Visualisation</div>
            <div><span class="pill green">Reveal.js</span> Presentation</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 14 — NEXT STEPS ═════════════════════════ -->
<section>
  <div style="padding:40px 60px">
    <div class="slide-label">Section 05 · Roadmap</div>
    <h2 class="slide-title">Next <span>Steps</span></h2>
    <div class="two-col">
      <div class="timeline">
        <div class="tl-item">
          <div class="tl-dot"></div>
          <div class="when">Month 1–2</div>
          <h4>Immediate Wins</h4>
          <p>Deploy ETA recalibration · Set up proactive delivery notifications · Enable fraud scoring alerts</p>
        </div>
        <div class="tl-item">
          <div class="tl-dot" style="background:#3498db"></div>
          <div class="when" style="color:#3498db">Month 1–3</div>
          <h4>Retention Activation</h4>
          <p>Launch post-purchase email sequence · Build loyalty points programme · A/B test review-incentive coupons</p>
        </div>
        <div class="tl-item">
          <div class="tl-dot" style="background:#9b59b6"></div>
          <div class="when" style="color:#9b59b6">Month 3–9</div>
          <h4>Geographic Expansion</h4>
          <p>Subsidised seller onboarding in RJ, MG, RS, BA · Regional carrier partnerships · Freight cost reduction initiative</p>
        </div>
        <div class="tl-item">
          <div class="tl-dot" style="background:#2ecc71"></div>
          <div class="when" style="color:#2ecc71">Ongoing</div>
          <h4>Monitor &amp; Iterate</h4>
          <p>Weekly KPI dashboard · Monthly RFM re-scoring · Quarterly risk register review</p>
        </div>
      </div>
      <div>
        <div style="background:var(--card);border-radius:12px;padding:24px">
          <div style="font-size:13px;font-weight:700;color:#fff;margin-bottom:16px">Success Metrics</div>
          <table class="data-table" style="font-size:12px">
            <thead><tr><th>Metric</th><th>Current</th><th>Target</th></tr></thead>
            <tbody>
              <tr><td>Repeat buyer rate</td><td style="color:#e74c3c">{repeat_pct}</td><td style="color:#2ecc71">5%+</td></tr>
              <tr><td>Avg review score</td><td style="color:#f39c12">{avg_review}/5</td><td style="color:#2ecc71">4.2+/5</td></tr>
              <tr><td>Late delivery rate</td><td style="color:#e74c3c">6.5%</td><td style="color:#2ecc71">&lt;3%</td></tr>
              <tr><td>Seller states covered</td><td style="color:#f39c12">SP-dominated</td><td style="color:#2ecc71">+4 states</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════════════════════ SLIDE 15 — Q&A ═══════════════════════════════ -->
<section data-background-gradient="linear-gradient(135deg,#0a1520 0%,#1a2940 55%,#0f2033 100%)">
  <div class="qa-slide">
    <h1>Q&amp;A</h1>
    <p>Thank you. We're ready for your questions.</p>
    <div class="qa-links">
      <div class="qa-link-card">
        <div class="icon">📊</div>
        <strong>Full PDF Report</strong>
        <p>Olist_Analysis_Report.pdf<br>on Desktop</p>
      </div>
      <div class="qa-link-card">
        <div class="icon">🗂</div>
        <strong>Draw.io Diagrams</strong>
        <p>Olist_Diagrams.drawio<br>Pipeline + ERD</p>
      </div>
      <div class="qa-link-card">
        <div class="icon">📓</div>
        <strong>Jupyter Notebook</strong>
        <p>analysis/notebooks/<br>analysis.ipynb</p>
      </div>
      <div class="qa-link-card">
        <div class="icon">☁️</div>
        <strong>BigQuery Project</strong>
        <p>{BQ}<br>olist_raw · warehouse · marts</p>
      </div>
    </div>
    <div style="margin-top:40px;font-size:13px;color:rgba(240,244,248,0.35)">
      NTU DSAI Module 2 · Olist Brazilian E-Commerce Analysis · Sep 2016 – Oct 2018
    </div>
  </div>
</section>

</div><!-- /slides -->
</div><!-- /reveal -->

<script src="https://cdn.jsdelivr.net/npm/reveal.js@4.6.1/dist/reveal.js"></script>
<script>
{JS}
{CHART_JS}

Reveal.initialize({{
  hash: true,
  transition: 'slide',
  transitionSpeed: 'fast',
  controls: true,
  progress: true,
  slideNumber: 'c/t',
  touch: true,
  keyboard: true,
  mouseWheel: false,
  width: 1280,
  height: 720,
  margin: 0,
  minScale: 0.5,
  maxScale: 2.0,
}});
</script>
</body>
</html>""".replace("{JS}", JS).replace("{CHART_JS}", CHART_JS)


# ── Save & open ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    data = fetch()
    html = build_html(data)
    DEST.write_text(html, encoding="utf-8")
    print(f"\n✓  Presentation saved to: {DEST}")
    print("  Opening in default browser…")
    webbrowser.open(f"file://{DEST}")
