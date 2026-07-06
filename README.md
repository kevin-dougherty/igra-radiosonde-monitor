# 📡 IGRA Radiosonde Monitor

> An interactive dashboard tracking the impact of 2025 federal budget cuts on the US upper-air radiosonde network, built with real NOAA IGRA v2.2 data.

---

## What this project is about

Radiosondes are launched twice daily at ~90 stations across the United States. The data they collect (temperature, humidity, wind speed and direction at every pressure level from the surface to the stratosphere) is the backbone of numerical weather prediction. Without it, forecast accuracy can degrade significantly.

In early 2025, federal impacts reduced staffing at National Weather Service offices, resulting in a measurable decline in radiosonde launches across the network. This dashboard quantifies that decline using publicly available data from NOAA's Integrated Global Radiosonde Archive (IGRA v2.2).

**Key finding:** Daily launches dropped from ~170 to ~122 after March 2025 — a 28% reduction — with several stations going completely silent.

---

## Dashboard features

| Tab | What it shows |
|---|---|
| **Time Series** | Daily launch counts with 7-day rolling average, budget cut annotation, and shaded post-cut region |
| **Station Map** | Green/red launch status by synoptic cycle (00Z or 12Z), selectable by date; reporting rate heatmap |
| **Budget Impact** | 60-day pre vs post cut comparison per station, sorted by largest decline |
| **Rankings** | All stations ranked by reporting rate with top 10 / bottom 10 tables |

**KPI cards** at the top show fleet-level stats at a glance: stations reporting, average daily launches (pre → post), launch reduction %, and stations no longer reporting.

---

## Project structure

```
igra_dashboard/
├── app.py            # Dash web dashboard — run this
├── queries.py        # DuckDB SQL query layer
├── ingest.py         # Data pipeline: NCEI → DuckDB
├── setup.py          # One-command bootstrap script
├── assets/
│   └── style.css     # Dashboard styling
└── requirements.txt
```

---

## Quick start

### 1. Clone and install dependencies

```bash
git clone https://github.com/kevin-dougherty/igra-radiosonde-monitor.git
cd igra-radiosonde-monitor

python -m venv igra_env
source igra_env/bin/activate
pip install -r requirements.txt
```

### 2. Bootstrap the database

```bash
python setup.py --start-year 2025 --start-month 1
```

This will:
1. Download the IGRA station list and filter to ~98 active US stations
2. Load station metadata (city, state names) into DuckDB
3. Fetch year-to-date data from NCEI and populate `data/igra.duckdb`

The first run downloads ~465MB of zip files (cached locally for fast re-runs).
Subsequent runs use the cache and complete in under a minute.

### 3. Run the dashboard

```bash
python app.py
```

Open **http://127.0.0.1:8050** in your browser.

---

## Tech stack

| Layer | Technology |
|---|---|
| Data source | NOAA NCEI IGRA v2.2 |
| Database | DuckDB |
| Query layer | SQL via DuckDB Python API |
| Dashboard | Dash 2.18 + Plotly 5.24 |
| Styling | Dash Bootstrap Components + custom CSS |
| Maps | Plotly Scattergeo (Albers USA projection) |

### Why DuckDB?

DuckDB is an in-process analytical database — no server required, extremely fast for aggregation queries over millions of rows. The soundings table has ~17M rows (one per pressure level per sounding). All dashboard queries run in milliseconds.

### Why Dash over Streamlit?

Dash apps are Flask under the hood, making them straightforward to deploy on any Python host with gunicorn. The callback architecture also gives precise control over what re-renders when, which matters for a multi-tab dashboard with multiple interdependent filters.

---

## Key SQL patterns used

**Daily launch counts:**
```sql
SELECT
    DATE_TRUNC('day', time) AS date,
    COUNT(DISTINCT station || '|' || CAST(time AS TEXT)) AS total_launches,
    COUNT(DISTINCT station) AS stations_reporting
FROM soundings
WHERE time >= '2025-01-01'
GROUP BY date
ORDER BY date
```

**Pre vs post budget cut comparison (equal 60-day windows):**
```sql
SELECT station,
    COUNT(DISTINCT CAST(time AS TEXT))
        FILTER (WHERE time >= '2025-01-01' AND time < '2025-03-01') AS pre_cut,
    COUNT(DISTINCT CAST(time AS TEXT))
        FILTER (WHERE time >= '2025-03-01' AND time < '2025-05-01') AS post_cut
FROM soundings
GROUP BY station
ORDER BY post_cut - pre_cut
```

---

## Data source

**IGRA v2.2** — Integrated Global Radiosonde Archive
https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive

Data is fetched from the data-y2d directory (year-to-date files), updated daily by NCEI.

---

## Deployment

The app is designed to be deployed with a scheduled ingest job that updates the database twice daily (after 00Z and 12Z synoptic hours):

- **Web host:** Render (Flask/gunicorn)
- **Database storage:** Cloudflare R2 (S3-compatible, zero egress fees)
- **Scheduled ingest:** GitHub Actions cron (free for public repos)

```bash
# Start command for Render
gunicorn app:server
```

---

## Author

Kevin Dougherty
Built as a portfolio project demonstrating data engineering, SQL, and interactive visualization skills.