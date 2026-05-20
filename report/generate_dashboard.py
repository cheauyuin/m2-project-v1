"""
Olist Brazilian E-Commerce — Interactive Analysis Dashboard
Modelled on DSAI_M2_Project's brazil_delivery_revenue_dashboard.html
  - Left panel  : filters, metric toggle, KPI summary, legend
  - Leaflet map : bubble markers by state, 4 view modes
  - Charts      : revenue trend · RFM segments · delivery scatter · category perf
Starts a local HTTP server and opens at http://localhost:8005/Olist_Dashboard.html
"""
import http.server, json, os, pathlib, socketserver, threading, webbrowser
from google.cloud import bigquery
import pandas as pd

BQ   = "dsai-module-2-project-496708"
client = bigquery.Client(project=BQ)
q    = lambda sql: client.query(sql).to_dataframe()

DESKTOP = pathlib.Path.home() / "Desktop"
DEST    = DESKTOP / "Olist_Dashboard.html"
PORT    = 8005

# ── Brazil state centroids ─────────────────────────────────────────────────────
STATE_COORDS = {
    'AC':[-9.02,-70.81],'AL':[-9.57,-36.78],'AM':[-4.00,-61.99],
    'AP':[0.90,-52.00], 'BA':[-12.57,-41.70],'CE':[-5.50,-39.32],
    'DF':[-15.78,-47.93],'ES':[-19.19,-40.34],'GO':[-15.83,-49.98],
    'MA':[-4.96,-45.27],'MG':[-18.10,-44.38],'MS':[-20.51,-54.54],
    'MT':[-12.64,-55.42],'PA':[-3.79,-52.48],'PB':[-7.06,-36.55],
    'PE':[-8.81,-36.95],'PI':[-7.72,-42.73],'PR':[-24.89,-51.55],
    'RJ':[-22.25,-42.66],'RN':[-5.84,-36.53],'RO':[-10.83,-63.34],
    'RR':[2.07,-61.40], 'RS':[-30.03,-53.23],'SC':[-27.45,-50.95],
    'SE':[-10.57,-37.45],'SP':[-22.19,-48.79],'TO':[-10.18,-48.33],
}
STATE_NAMES = {
    'AC':'Acre','AL':'Alagoas','AM':'Amazonas','AP':'Amapá','BA':'Bahia',
    'CE':'Ceará','DF':'Distrito Federal','ES':'Espírito Santo','GO':'Goiás',
    'MA':'Maranhão','MG':'Minas Gerais','MS':'Mato Grosso do Sul',
    'MT':'Mato Grosso','PA':'Pará','PB':'Paraíba','PE':'Pernambuco',
    'PI':'Piauí','PR':'Paraná','RJ':'Rio de Janeiro','RN':'Rio Grande do Norte',
    'RO':'Rondônia','RR':'Roraima','RS':'Rio Grande do Sul',
    'SC':'Santa Catarina','SE':'Sergipe','SP':'São Paulo','TO':'Tocantins',
}

# ── BigQuery fetch ─────────────────────────────────────────────────────────────
def fetch():
    print("Fetching data from BigQuery…")

    kpi = q(f"""
        SELECT
            COUNT(DISTINCT order_id)                                      AS total_orders,
            COUNT(DISTINCT customer_key)                                   AS unique_customers,
            ROUND(SUM(total_payment), 0)                                   AS total_revenue,
            ROUND(AVG(total_payment), 2)                                   AS avg_order_value,
            ROUND(AVG(avg_review_score), 2)                                AS avg_review_score,
            SUM(CASE WHEN delivery_delay_days > 0 THEN 1 ELSE 0 END)      AS late_orders
        FROM `{BQ}.olist_warehouse.fact_orders`
        WHERE order_status NOT IN ('canceled','unavailable')
    """).iloc[0]

    repeat = q(f"""
        SELECT ROUND(100.0 * SUM(CASE WHEN frequency>1 THEN 1 ELSE 0 END)/COUNT(*),1) AS repeat_pct,
               ROUND(AVG(CASE WHEN frequency>1 THEN monetary END),2) AS avg_repeat,
               ROUND(AVG(CASE WHEN frequency=1 THEN monetary END),2) AS avg_onetime
        FROM `{BQ}.olist_marts.customer_rfm`
    """).iloc[0]

    by_state = q(f"""
        SELECT customer_state AS state, total_orders, avg_delay_days,
               late_count, late_pct, avg_review_score,
               avg_freight_value
        FROM `{BQ}.olist_marts.delivery_by_state`
    """)

    sellers = q(f"""
        SELECT state, COUNT(DISTINCT seller_key) AS seller_count
        FROM `{BQ}.olist_warehouse.dim_sellers`
        GROUP BY state
    """)

    monthly = q(f"""
        SELECT year, month, month_name, total_orders, total_revenue,
               avg_order_value, avg_review_score, late_deliveries
        FROM `{BQ}.olist_marts.monthly_sales`
        ORDER BY year, month
    """)

    rfm = q(f"""
        SELECT segment, COUNT(*) AS n,
               ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER(),1) AS pct,
               ROUND(AVG(monetary),2) AS avg_spend
        FROM `{BQ}.olist_marts.customer_rfm`
        GROUP BY segment ORDER BY n DESC
    """)

    cats = q(f"""
        SELECT category, total_revenue, units_sold,
               avg_review_score, avg_freight_ratio
        FROM `{BQ}.olist_marts.category_performance`
        ORDER BY total_revenue DESC LIMIT 10
    """)

    print("  Done.")
    return dict(kpi=kpi, repeat=repeat, by_state=by_state, sellers=sellers,
                monthly=monthly, rfm=rfm, cats=cats)


# ── Build HTML ─────────────────────────────────────────────────────────────────
def build_html(d):
    kpi     = d["kpi"]
    rep     = d["repeat"]
    monthly = d["monthly"]
    rfm     = d["rfm"]
    cats    = d["cats"]

    # Merge sellers into by_state
    by_state = d["by_state"].merge(d["sellers"], on="state", how="left")
    by_state["seller_count"] = by_state["seller_count"].fillna(0).astype(int)
    by_state["cs_ratio"] = (
        by_state["total_orders"] / by_state["seller_count"].clip(lower=1)
    ).round(1)
    by_state["state_name"] = by_state["state"].map(STATE_NAMES).fillna(by_state["state"])
    by_state["lat"] = by_state["state"].map(lambda s: STATE_COORDS.get(s, [0,0])[0])
    by_state["lng"] = by_state["state"].map(lambda s: STATE_COORDS.get(s, [0,0])[1])

    # Serialize inline data
    data_json = json.dumps({
        "kpi": {
            "total_orders":     int(kpi.total_orders),
            "unique_customers": int(kpi.unique_customers),
            "total_revenue":    float(kpi.total_revenue),
            "avg_order_value":  float(kpi.avg_order_value),
            "avg_review_score": float(kpi.avg_review_score),
            "late_orders":      int(kpi.late_orders),
            "repeat_pct":       float(rep.repeat_pct),
            "avg_repeat":       float(rep.avg_repeat),
            "avg_onetime":      float(rep.avg_onetime),
        },
        "byState": [
            {
                "state":            r.state,
                "name":             r.state_name,
                "lat":              r.lat,
                "lng":              r.lng,
                "total_orders":     int(r.total_orders),
                "avg_review_score": round(float(r.avg_review_score), 2),
                "late_pct":         round(float(r.late_pct), 1),
                "avg_delay_days":   round(float(r.avg_delay_days), 1),
                "avg_freight_value":round(float(r.avg_freight_value), 2),
                "seller_count":     int(r.seller_count),
                "cs_ratio":         float(r.cs_ratio),
            }
            for _, r in by_state.iterrows()
        ],
        "monthly": [
            {
                "label":       f"{int(r.year)}-{str(int(r.month)).zfill(2)}",
                "month_name":  str(r.month_name),
                "revenue":     round(float(r.total_revenue), 2),
                "orders":      int(r.total_orders),
                "avg_value":   round(float(r.avg_order_value), 2),
                "avg_review":  round(float(r.avg_review_score), 2),
                "late":        int(r.late_deliveries),
            }
            for _, r in monthly.iterrows()
        ],
        "rfm": [
            {"segment": r.segment, "count": int(r.n),
             "pct": float(r.pct), "avg_spend": float(r.avg_spend)}
            for _, r in rfm.iterrows()
        ],
        "categories": [
            {
                "category":      r.category.replace("_", " ").title(),
                "revenue":       round(float(r.total_revenue), 2),
                "units":         int(r.units_sold),
                "review":        round(float(r.avg_review_score), 2),
                "freight_ratio": round(float(r.avg_freight_ratio), 3),
            }
            for _, r in cats.iterrows()
        ],
    }, ensure_ascii=False)

    html = HTML_TEMPLATE.replace("/*INLINE_DATA*/", f"const D = {data_json};")
    return html


# ── HTML template ──────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Olist E-Commerce Analysis Dashboard</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js" charset="utf-8"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;background:#f0f4f8;color:#1e293b;min-height:100vh}

/* ── HEADER ── */
.hdr{background:linear-gradient(135deg,#0f1f3d 0%,#1e3a5f 60%,#0f2640 100%);
  color:#fff;padding:22px 32px;box-shadow:0 2px 8px rgba(0,0,0,0.25);
  border-bottom:3px solid #f39c12;display:flex;align-items:center;justify-content:space-between}
.hdr-left h1{font-size:26px;font-weight:700;letter-spacing:-0.3px}
.hdr-left p{font-size:13px;opacity:0.75;margin-top:3px}
.kpi-strip{display:flex;gap:24px}
.kpi-chip{text-align:center}
.kpi-chip .v{font-size:20px;font-weight:800;color:#f39c12}
.kpi-chip .l{font-size:10px;color:rgba(255,255,255,0.6);text-transform:uppercase;letter-spacing:0.8px;margin-top:2px}

/* ── LAYOUT ── */
.dash{display:grid;grid-template-columns:300px 1fr;gap:14px;padding:14px;max-width:1700px;margin:0 auto}

/* ── LEFT PANEL ── */
.panel{background:#fff;border-radius:6px;padding:18px;
  box-shadow:0 1px 4px rgba(0,0,0,0.1);border:1px solid #e2e8f0;
  position:sticky;top:14px;height:fit-content;max-height:calc(100vh - 100px);overflow-y:auto}
.panel-section{margin-bottom:22px}
.panel-title{font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;
  letter-spacing:0.8px;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #f1f5f9}

/* view mode buttons */
.mode-btn{width:100%;text-align:left;padding:9px 12px;margin-bottom:6px;
  border-radius:5px;border:1px solid #e2e8f0;background:#f8fafc;cursor:pointer;
  font-size:12px;font-weight:500;color:#475569;transition:all 0.15s;display:flex;align-items:center;gap:8px}
.mode-btn .dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.mode-btn:hover{background:#f0f4f8;border-color:#cbd5e1}
.mode-btn.active{background:#0f1f3d;color:#fff;border-color:#0f1f3d}
.mode-btn.active .dot{box-shadow:0 0 0 2px rgba(255,255,255,0.4)}

/* state checkboxes */
.state-list{max-height:180px;overflow-y:auto;border:1px solid #e2e8f0;
  border-radius:5px;padding:10px;background:#f8fafc}
.state-list::-webkit-scrollbar{width:5px}
.state-list::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:3px}
.state-item{display:flex;align-items:center;margin-bottom:7px;padding:5px 7px;
  border-radius:4px;transition:background 0.1s;cursor:pointer}
.state-item:hover{background:#e2e8f0}
.state-item input{margin-right:8px;cursor:pointer;accent-color:#0f1f3d}
.state-item label{font-size:12px;color:#374151;cursor:pointer}
.state-item label span{color:#94a3b8;font-size:11px;margin-left:4px}
.btn-ctrl{padding:7px 12px;border-radius:5px;border:1px solid #e2e8f0;
  background:#f8fafc;font-size:11px;font-weight:600;cursor:pointer;
  color:#475569;transition:all 0.15s;margin-right:6px}
.btn-ctrl:hover{background:#e2e8f0}
.btn-ctrl.primary{background:#0f1f3d;color:#fff;border-color:#0f1f3d}
.btn-ctrl.primary:hover{background:#1e3a5f}

/* legend */
.legend-item{display:flex;align-items:center;gap:8px;font-size:11px;
  color:#475569;margin-bottom:7px}
.legend-dot{width:12px;height:12px;border-radius:50%;flex-shrink:0}
.legend-rect{width:18px;height:10px;border-radius:2px;flex-shrink:0}

/* data info */
.data-info{font-size:11px;color:#94a3b8;line-height:1.7}

/* ── CONTENT AREA ── */
.content{display:grid;grid-template-rows:auto auto;gap:14px}

/* map card */
.map-card{background:#fff;border-radius:6px;padding:18px;
  box-shadow:0 1px 4px rgba(0,0,0,0.1);border:1px solid #e2e8f0}
.map-card-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.map-card-hdr h3{font-size:15px;font-weight:700;color:#0f1f3d}
.map-card-hdr p{font-size:12px;color:#64748b}
#map{height:480px;border-radius:5px;border:1px solid #e2e8f0}

/* chart grid */
.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.chart-card{background:#fff;border-radius:6px;padding:18px;
  box-shadow:0 1px 4px rgba(0,0,0,0.1);border:1px solid #e2e8f0}
.chart-card h3{font-size:14px;font-weight:700;color:#0f1f3d;margin-bottom:4px}
.chart-card p{font-size:11px;color:#64748b;margin-bottom:10px}
.chart-div{width:100%;height:280px}

/* aggregation badge */
.agg-badge{position:absolute;bottom:10px;left:10px;z-index:1000;
  background:#fff;padding:5px 10px;border-radius:4px;
  font-size:11px;font-weight:600;color:#475569;
  box-shadow:0 1px 4px rgba(0,0,0,0.15);border:1px solid #e2e8f0}

/* tile selector */
.tile-select{font-size:11px;padding:5px 8px;border:1px solid #e2e8f0;
  border-radius:4px;background:#f8fafc;color:#475569;cursor:pointer}

/* highlight callout box on map */
.map-info{background:#fff;padding:10px 14px;border-radius:6px;
  box-shadow:0 2px 8px rgba(0,0,0,0.15);font-size:12px;
  line-height:1.8;min-width:160px}
.map-info b{color:#0f1f3d;font-size:13px}
.map-info .metric{color:#f39c12;font-weight:700}
</style>
</head>
<body>

<!-- HEADER -->
<div class="hdr">
  <div class="hdr-left">
    <h1>🛒 Olist E-Commerce Analysis Dashboard</h1>
    <p>Brazilian E-Commerce · Sep 2016 – Oct 2018 · Powered by BigQuery + DuckDB</p>
  </div>
  <div class="kpi-strip" id="kpi-strip"><!-- populated by JS --></div>
</div>

<!-- MAIN LAYOUT -->
<div class="dash">

  <!-- LEFT PANEL -->
  <aside class="panel">

    <!-- Map view modes -->
    <div class="panel-section">
      <div class="panel-title">Map View</div>
      <button class="mode-btn active" data-mode="orders" onclick="setMode(this)">
        <span class="dot" style="background:#1d4ed8"></span>Customer Volume
      </button>
      <button class="mode-btn" data-mode="delivery" onclick="setMode(this)">
        <span class="dot" style="background:#16a34a"></span>Delivery Performance
      </button>
      <button class="mode-btn" data-mode="review" onclick="setMode(this)">
        <span class="dot" style="background:#f59e0b"></span>Review Score
      </button>
      <button class="mode-btn" data-mode="opportunity" onclick="setMode(this)">
        <span class="dot" style="background:#7c3aed"></span>Expansion Opportunity
      </button>
    </div>

    <!-- State filter -->
    <div class="panel-section">
      <div class="panel-title">State Filter</div>
      <div style="margin-bottom:10px;display:flex;flex-wrap:wrap;gap:6px">
        <button class="btn-ctrl primary" onclick="selectAll()">Select All</button>
        <button class="btn-ctrl" onclick="clearAll()">Clear</button>
      </div>
      <div class="state-list" id="state-list"><!-- populated by JS --></div>
    </div>

    <!-- Map legend -->
    <div class="panel-section">
      <div class="panel-title">Legend</div>
      <div id="legend"><!-- populated by JS --></div>
    </div>

    <!-- Tile style selector -->
    <div class="panel-section">
      <div class="panel-title">Map Style</div>
      <select class="tile-select" id="tile-select" onchange="changeTile(this.value)">
        <option value="voyager">CartoDB Voyager</option>
        <option value="dark">CartoDB Dark</option>
        <option value="positron">CartoDB Positron</option>
        <option value="osm">OpenStreetMap</option>
      </select>
    </div>

    <!-- Data info -->
    <div class="panel-section">
      <div class="panel-title">Data Source</div>
      <div class="data-info" id="data-info"><!-- populated by JS --></div>
    </div>

  </aside>

  <!-- CONTENT AREA -->
  <div class="content">

    <!-- MAP -->
    <div class="map-card">
      <div class="map-card-hdr">
        <div>
          <h3 id="map-title">Customer Volume by State</h3>
          <p id="map-subtitle">Bubble size and colour represent order volume per state. Click a state for details.</p>
        </div>
        <p style="font-size:11px;color:#94a3b8" id="state-count-label">Showing all states</p>
      </div>
      <div style="position:relative">
        <div id="map"></div>
        <div class="agg-badge" id="agg-badge">State Level</div>
      </div>
    </div>

    <!-- CHARTS GRID -->
    <div class="chart-grid">

      <div class="chart-card">
        <h3>Revenue & Order Trend</h3>
        <p>Monthly revenue (R$) and order volume — Sep 2016 to Oct 2018</p>
        <div class="chart-div" id="chart-revenue"></div>
      </div>

      <div class="chart-card">
        <h3>Customer Segments (RFM)</h3>
        <p>Recency–Frequency–Monetary segmentation of all customers</p>
        <div class="chart-div" id="chart-rfm"></div>
      </div>

      <div class="chart-card">
        <h3>Delivery Performance by State</h3>
        <p>Avg delivery delay vs review score. Bubble size = order volume.</p>
        <div class="chart-div" id="chart-delivery"></div>
      </div>

      <div class="chart-card">
        <h3>Top 10 Categories by Revenue</h3>
        <p>Bar colour indicates average review score (green = high, red = low)</p>
        <div class="chart-div" id="chart-cats"></div>
      </div>

    </div>
  </div>
</div>

<script>
/*INLINE_DATA*/

// ── Tile styles ───────────────────────────────────────────────────────────────
const TILES = {
  voyager:  { url:'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
              attr:'© OpenStreetMap © CARTO' },
  dark:     { url:'https://{s}.basemaps.cartocdn.com/dark_matter/{z}/{x}/{y}{r}.png',
              attr:'© OpenStreetMap © CARTO' },
  positron: { url:'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
              attr:'© OpenStreetMap © CARTO' },
  osm:      { url:'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
              attr:'© OpenStreetMap' },
};

// ── State ─────────────────────────────────────────────────────────────────────
let leafletMap, tileLayer, markersLayer, infoControl, legendControl;
let currentMode     = 'orders';
let selectedStates  = new Set(D.byState.map(s => s.state));
let currentTile     = 'voyager';

// ── Colour helpers ────────────────────────────────────────────────────────────
const maxOrders  = Math.max(...D.byState.map(s => s.total_orders));
const maxFreight = Math.max(...D.byState.map(s => s.avg_freight_value));
const maxRatio   = Math.max(...D.byState.map(s => s.cs_ratio));

function ordersColor(v) {
  const t = v / maxOrders;
  if (t < 0.10) return '#bfdbfe';
  if (t < 0.30) return '#3b82f6';
  if (t < 0.60) return '#1d4ed8';
  return '#1e3a8a';
}
function deliveryColor(late) {
  if (late < 4)  return '#16a34a';
  if (late < 8)  return '#84cc16';
  if (late < 12) return '#f59e0b';
  if (late < 16) return '#ef4444';
  return '#991b1b';
}
function reviewColor(r) {
  if (r >= 4.3) return '#16a34a';
  if (r >= 4.0) return '#84cc16';
  if (r >= 3.7) return '#f59e0b';
  if (r >= 3.4) return '#ef4444';
  return '#991b1b';
}
function opportunityColor(ratio) {
  const t = ratio / maxRatio;
  if (t < 0.15) return '#ddd6fe';
  if (t < 0.35) return '#a78bfa';
  if (t < 0.60) return '#7c3aed';
  return '#4c1d95';
}
function getColor(s) {
  if (currentMode === 'orders')      return ordersColor(s.total_orders);
  if (currentMode === 'delivery')    return deliveryColor(s.late_pct);
  if (currentMode === 'review')      return reviewColor(s.avg_review_score);
  if (currentMode === 'opportunity') return opportunityColor(s.cs_ratio);
  return '#64748b';
}
function getRadius(s) {
  const base = 8;
  if (currentMode === 'orders')      return base + (s.total_orders / maxOrders) * 26;
  if (currentMode === 'delivery')    return base + (s.late_pct / 20) * 22;
  if (currentMode === 'review')      return base + ((5 - s.avg_review_score) / 2) * 20;
  if (currentMode === 'opportunity') return base + (s.cs_ratio / maxRatio) * 26;
  return base;
}

// ── Map init ──────────────────────────────────────────────────────────────────
function initMap() {
  leafletMap = L.map('map', { zoomControl: true }).setView([-15.8, -47.9], 4);
  const t = TILES[currentTile];
  tileLayer = L.tileLayer(t.url, { attribution: t.attr, subdomains: 'abcd', maxZoom: 19 });
  tileLayer.addTo(leafletMap);

  // info control
  infoControl = L.control({ position: 'topright' });
  infoControl.onAdd = function() {
    this._div = L.DomUtil.create('div', 'map-info');
    this._div.innerHTML = '<b>Hover over a state</b>';
    return this._div;
  };
  infoControl.update = function(s) {
    if (!s) { this._div.innerHTML = '<b>Hover over a state</b>'; return; }
    this._div.innerHTML =
      `<b>${s.name} (${s.state})</b><br>` +
      `Orders: <span class="metric">${s.total_orders.toLocaleString()}</span><br>` +
      `Avg Review: <span class="metric">${s.avg_review_score.toFixed(2)}/5</span><br>` +
      `Late Deliveries: <span class="metric">${s.late_pct.toFixed(1)}%</span><br>` +
      `Avg Delay: <span class="metric">${s.avg_delay_days.toFixed(1)} d</span><br>` +
      `Avg Freight: <span class="metric">R$ ${s.avg_freight_value.toFixed(2)}</span><br>` +
      `Sellers: <span class="metric">${s.seller_count.toLocaleString()}</span><br>` +
      `Customer/Seller: <span class="metric">${s.cs_ratio.toFixed(0)}×</span>`;
  };
  infoControl.addTo(leafletMap);

  drawMarkers();
  updateLegend();
}

// ── Draw markers ──────────────────────────────────────────────────────────────
function drawMarkers() {
  if (markersLayer) { leafletMap.removeLayer(markersLayer); markersLayer = null; }
  const visible = D.byState.filter(s => selectedStates.has(s.state));
  const markers = visible.map(s => {
    const m = L.circleMarker([s.lat, s.lng], {
      radius:      getRadius(s),
      fillColor:   getColor(s),
      color:       'rgba(255,255,255,0.7)',
      weight:      1.5,
      opacity:     1,
      fillOpacity: 0.85,
    });
    m.on('mouseover', () => { infoControl.update(s); m.setStyle({ weight: 3, color: '#0f1f3d' }); });
    m.on('mouseout',  () => { infoControl.update(null); m.setStyle({ weight: 1.5, color: 'rgba(255,255,255,0.7)' }); });
    m.on('click', () => toggleState(s.state));
    m.bindTooltip(
      `<b>${s.name}</b><br>` +
      `${s.total_orders.toLocaleString()} orders &nbsp;|&nbsp; ` +
      `${s.avg_review_score.toFixed(2)}★ &nbsp;|&nbsp; ` +
      `${s.late_pct.toFixed(1)}% late`,
      { sticky: true }
    );
    return m;
  });
  markersLayer = L.layerGroup(markers).addTo(leafletMap);
  document.getElementById('state-count-label').textContent =
    `Showing ${visible.length} of ${D.byState.length} states`;
}

// ── Mode toggle ───────────────────────────────────────────────────────────────
const MODE_META = {
  orders:      { title: 'Customer Volume by State',
                 sub:   'Bubble size and colour = order volume. Darker/larger = more orders.' },
  delivery:    { title: 'Delivery Performance by State',
                 sub:   'Colour = late delivery rate. Green < 4%, Red > 16%.' },
  review:      { title: 'Customer Satisfaction by State',
                 sub:   'Colour = avg review score. Green ≥ 4.3★, Red < 3.4★.' },
  opportunity: { title: 'Geographic Expansion Opportunity',
                 sub:   'Colour = customer-to-seller ratio. Purple = underserved markets.' },
};

function setMode(btn) {
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  currentMode = btn.dataset.mode;
  document.getElementById('map-title').textContent    = MODE_META[currentMode].title;
  document.getElementById('map-subtitle').textContent = MODE_META[currentMode].sub;
  drawMarkers();
  updateLegend();
}

// ── Legend ────────────────────────────────────────────────────────────────────
const LEGENDS = {
  orders: [
    { color:'#bfdbfe', label:'< 10% of max (low)' },
    { color:'#3b82f6', label:'10–30%' },
    { color:'#1d4ed8', label:'30–60%' },
    { color:'#1e3a8a', label:'> 60% (high volume)' },
  ],
  delivery: [
    { color:'#16a34a', label:'< 4% late (excellent)' },
    { color:'#84cc16', label:'4–8%' },
    { color:'#f59e0b', label:'8–12%' },
    { color:'#ef4444', label:'12–16%' },
    { color:'#991b1b', label:'> 16% late (critical)' },
  ],
  review: [
    { color:'#16a34a', label:'≥ 4.3★ (excellent)' },
    { color:'#84cc16', label:'4.0–4.3★' },
    { color:'#f59e0b', label:'3.7–4.0★' },
    { color:'#ef4444', label:'3.4–3.7★' },
    { color:'#991b1b', label:'< 3.4★ (poor)' },
  ],
  opportunity: [
    { color:'#ddd6fe', label:'Low ratio (well served)' },
    { color:'#a78bfa', label:'Moderate' },
    { color:'#7c3aed', label:'High ratio' },
    { color:'#4c1d95', label:'Very high (underserved)' },
  ],
};

function updateLegend() {
  const items = LEGENDS[currentMode];
  const el = document.getElementById('legend');
  el.innerHTML = items.map(i =>
    `<div class="legend-item">
       <span class="legend-dot" style="background:${i.color}"></span>
       ${i.label}
     </div>`
  ).join('');
}

// ── State filter ──────────────────────────────────────────────────────────────
function buildStateList() {
  const sorted = [...D.byState].sort((a, b) => b.total_orders - a.total_orders);
  const el = document.getElementById('state-list');
  el.innerHTML = sorted.map(s =>
    `<div class="state-item">
       <input type="checkbox" id="cb-${s.state}" value="${s.state}" checked
              onchange="toggleState('${s.state}')">
       <label for="cb-${s.state}">
         ${s.name}
         <span>(${s.total_orders.toLocaleString()})</span>
       </label>
     </div>`
  ).join('');
}

function toggleState(code) {
  if (selectedStates.has(code)) {
    if (selectedStates.size === 1) return;
    selectedStates.delete(code);
  } else {
    selectedStates.add(code);
  }
  const cb = document.getElementById(`cb-${code}`);
  if (cb) cb.checked = selectedStates.has(code);
  drawMarkers();
  updateCharts();
}

function selectAll() {
  D.byState.forEach(s => {
    selectedStates.add(s.state);
    const cb = document.getElementById(`cb-${s.state}`);
    if (cb) cb.checked = true;
  });
  drawMarkers();
  updateCharts();
}

function clearAll() {
  const first = [...selectedStates][0];
  selectedStates.clear();
  if (first) selectedStates.add(first);
  D.byState.forEach(s => {
    const cb = document.getElementById(`cb-${s.state}`);
    if (cb) cb.checked = selectedStates.has(s.state);
  });
  drawMarkers();
  updateCharts();
}

// ── Tile style ────────────────────────────────────────────────────────────────
function changeTile(name) {
  if (tileLayer) { leafletMap.removeLayer(tileLayer); }
  currentTile = name;
  const t = TILES[name];
  tileLayer = L.tileLayer(t.url, { attribution: t.attr, subdomains: 'abcd', maxZoom: 19 });
  tileLayer.addTo(leafletMap);
}

// ── KPI strip ─────────────────────────────────────────────────────────────────
function buildKpis() {
  const k = D.kpi;
  const chips = [
    { v: k.total_orders.toLocaleString(),       l: 'Total Orders' },
    { v: `R$ ${(k.total_revenue/1e6).toFixed(2)}M`, l: 'Revenue' },
    { v: `${k.avg_review_score.toFixed(2)} / 5`, l: 'Avg Review' },
    { v: `${k.repeat_pct.toFixed(1)}%`,          l: 'Repeat Rate' },
    { v: `R$ ${k.avg_order_value.toFixed(0)}`,   l: 'Avg Order' },
    { v: k.late_orders.toLocaleString(),          l: 'Late Deliveries' },
  ];
  document.getElementById('kpi-strip').innerHTML = chips.map(c =>
    `<div class="kpi-chip"><div class="v">${c.v}</div><div class="l">${c.l}</div></div>`
  ).join('');
}

// ── Plotly charts ─────────────────────────────────────────────────────────────
const PLOTLY_LAYOUT_BASE = {
  margin: { t: 10, l: 55, r: 20, b: 45 },
  paper_bgcolor: 'white',
  plot_bgcolor:  'white',
  font: { family: 'Segoe UI, sans-serif', size: 11, color: '#475569' },
  xaxis: { gridcolor: '#f1f5f9', linecolor: '#e2e8f0', tickfont: { size: 10 } },
  yaxis: { gridcolor: '#f1f5f9', linecolor: '#e2e8f0', tickfont: { size: 10 } },
  legend: { font: { size: 10 } },
};
const PLOTLY_CONFIG = { responsive: true, displayModeBar: false };

function buildRevenueChart() {
  const labels = D.monthly.map(m => m.label);
  const rev    = D.monthly.map(m => m.revenue);
  const orders = D.monthly.map(m => m.orders);

  Plotly.newPlot('chart-revenue', [
    {
      x: labels, y: rev, name: 'Revenue (R$)',
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#1d4ed8', width: 2.5 },
      fill: 'tozeroy', fillcolor: 'rgba(29,78,216,0.07)',
      marker: { size: 4, color: '#1d4ed8' },
      hovertemplate: '%{x}<br>R$ %{y:,.0f}<extra>Revenue</extra>',
      yaxis: 'y',
    },
    {
      x: labels, y: orders, name: 'Orders',
      type: 'scatter', mode: 'lines',
      line: { color: '#f39c12', width: 2, dash: 'dot' },
      marker: { size: 3 },
      hovertemplate: '%{x}<br>%{y:,d} orders<extra>Orders</extra>',
      yaxis: 'y2',
    },
  ], {
    ...PLOTLY_LAYOUT_BASE,
    margin: { t: 10, l: 60, r: 55, b: 40 },
    yaxis: { ...PLOTLY_LAYOUT_BASE.yaxis, title: { text: 'Revenue (R$)', font: { size: 10 }, standoff: 8 },
             tickformat: ',.0f' },
    yaxis2: { title: { text: 'Orders', font: { size: 10 }, standoff: 8 },
              overlaying: 'y', side: 'right', gridcolor: 'transparent',
              tickfont: { size: 10 }, tickformat: ',d' },
    legend: { orientation: 'h', y: -0.15, font: { size: 10 } },
    hovermode: 'x unified',
  }, PLOTLY_CONFIG);
}

function buildRfmChart() {
  const PALETTE = ['#1d4ed8','#f39c12','#16a34a','#dc2626','#7c3aed','#0891b2','#d97706','#374151'];
  const labels = D.rfm.map(r => r.segment);
  const vals   = D.rfm.map(r => r.count);
  Plotly.newPlot('chart-rfm', [{
    labels, values: vals, type: 'pie', hole: 0.55,
    marker: { colors: PALETTE.slice(0, labels.length) },
    textinfo: 'label+percent',
    textfont: { size: 10 },
    hovertemplate: '<b>%{label}</b><br>%{value:,d} customers<br>%{percent}<extra></extra>',
  }], {
    ...PLOTLY_LAYOUT_BASE,
    margin: { t: 10, l: 10, r: 10, b: 10 },
    showlegend: false,
    annotations: [{
      text: `${D.kpi.unique_customers.toLocaleString()}<br>customers`,
      x: 0.5, y: 0.5, xanchor: 'center', yanchor: 'middle',
      showarrow: false, font: { size: 11, color: '#0f1f3d', family: 'Segoe UI' },
    }],
  }, PLOTLY_CONFIG);
}

function buildDeliveryScatter(stateFilter) {
  const data = stateFilter
    ? D.byState.filter(s => stateFilter.has(s.state))
    : D.byState;

  const maxOrd = Math.max(...data.map(s => s.total_orders));
  Plotly.newPlot('chart-delivery', [{
    x:    data.map(s => s.avg_delay_days),
    y:    data.map(s => s.avg_review_score),
    mode: 'markers+text',
    type: 'scatter',
    text: data.map(s => s.state),
    textposition: 'top center',
    textfont: { size: 9, color: '#64748b' },
    marker: {
      size:    data.map(s => 10 + (s.total_orders / maxOrd) * 28),
      color:   data.map(s => s.late_pct),
      colorscale: [[0,'#16a34a'],[0.5,'#f59e0b'],[1,'#dc2626']],
      showscale: true,
      colorbar: { title: '% Late', titlefont: { size: 9 }, tickfont: { size: 9 },
                  len: 0.6, thickness: 10, x: 1.01 },
      line:    { color: 'rgba(255,255,255,0.7)', width: 1 },
      opacity: 0.85,
    },
    hovertemplate:
      '<b>%{text}</b><br>' +
      'Avg delay: %{x:.1f}d<br>' +
      'Avg review: %{y:.2f}★<extra></extra>',
  }], {
    ...PLOTLY_LAYOUT_BASE,
    margin: { t: 10, l: 55, r: 65, b: 45 },
    xaxis: { ...PLOTLY_LAYOUT_BASE.xaxis, title: { text: 'Avg Delivery Delay (days)', font: { size: 10 }, standoff: 8 } },
    yaxis: { ...PLOTLY_LAYOUT_BASE.yaxis, title: { text: 'Avg Review Score', font: { size: 10 }, standoff: 8 } },
    shapes: [
      { type:'line', x0:0, x1:0, y0:1, y1:5, line:{ color:'#64748b', dash:'dot', width:1 } },
    ],
    annotations: [{
      x: 0.02, y: 4.4, xref: 'paper', yref: 'y', showarrow: false,
      text: '← Early delivery', font: { size: 9, color: '#94a3b8' },
    }],
  }, PLOTLY_CONFIG);
}

function buildCatChart() {
  const cats    = [...D.categories].reverse();
  const colors  = cats.map(c => {
    const r = c.review;
    if (r >= 4.3) return '#16a34a';
    if (r >= 4.0) return '#84cc16';
    if (r >= 3.7) return '#f59e0b';
    return '#ef4444';
  });
  Plotly.newPlot('chart-cats', [{
    y:           cats.map(c => c.category),
    x:           cats.map(c => c.revenue / 1000),
    type:        'bar',
    orientation: 'h',
    marker:      { color: colors, opacity: 0.88 },
    text:        cats.map(c => `${c.review.toFixed(1)}★`),
    textposition:'outside',
    textfont:    { size: 9, color: '#475569' },
    hovertemplate:
      '<b>%{y}</b><br>Revenue: R$ %{x:.0f}K<br>' +
      'Units: %{customdata[0]:,d}<br>Review: %{customdata[1]:.2f}★<extra></extra>',
    customdata: cats.map(c => [c.units, c.review]),
  }], {
    ...PLOTLY_LAYOUT_BASE,
    margin: { t: 10, l: 160, r: 60, b: 40 },
    xaxis: { ...PLOTLY_LAYOUT_BASE.xaxis, title: { text: 'Revenue (R$ thousands)', font: { size: 10 }, standoff: 8 } },
    yaxis: { ...PLOTLY_LAYOUT_BASE.yaxis, tickfont: { size: 10 } },
  }, PLOTLY_CONFIG);
}

function updateCharts() {
  buildDeliveryScatter(selectedStates);
}

// ── Data info panel ───────────────────────────────────────────────────────────
function buildDataInfo() {
  const k = D.kpi;
  document.getElementById('data-info').innerHTML =
    `Source: BigQuery · dsai-module-2-project-496708<br>` +
    `Warehouse: olist_warehouse (star schema)<br>` +
    `Marts: olist_marts (9 analytical tables)<br>` +
    `Period: Sep 2016 – Oct 2018<br>` +
    `Orders: ${k.total_orders.toLocaleString()} · ` +
    `Customers: ${k.unique_customers.toLocaleString()}`;
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  buildKpis();
  buildStateList();
  buildDataInfo();
  initMap();
  buildRevenueChart();
  buildRfmChart();
  buildDeliveryScatter(null);
  buildCatChart();
});
</script>
</body>
</html>
"""


# ── Server + launch ────────────────────────────────────────────────────────────
def start_server():
    os.chdir(str(DESKTOP))

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header('Access-Control-Allow-Origin', '*')
            super().end_headers()
        def log_message(self, *args):
            pass  # suppress access log spam

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        url = f"http://localhost:{PORT}/Olist_Dashboard.html"
        print(f"\n✓  Dashboard running at {url}")
        print("  Features:")
        print("   🗺️  Brazil map — 4 view modes (volume / delivery / review / expansion)")
        print("   📊  Revenue trend, RFM segments, delivery scatter, category chart")
        print("   🔍  State filter — click state on map or use sidebar checkboxes")
        print("   🎨  4 map tile styles")
        print("\n  Press Ctrl+C to stop\n")
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Server stopped.")


if __name__ == "__main__":
    data = fetch()
    html = build_html(data)
    DEST.write_text(html, encoding="utf-8")
    print(f"  HTML saved → {DEST}")
    start_server()
