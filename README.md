# 📡 IGRA Radiosonde Monitor

> An interactive dashboard tracking the impact on the US upper-air radiosonde network since early 2025, built with real NOAA IGRA v2.2 data.

See live dashboard here: [https://igra-radiosonde-monitor.onrender.com/](https://igra-radiosonde-monitor.onrender.com/)

---

## What this project is about

Radiosondes are launched twice daily at ~90 stations across the United States. The data they collect (temperature, humidity, wind speed and direction at every pressure level from the surface to the stratosphere) is the backbone of numerical weather prediction. Without it, forecast accuracy can degrade significantly.

Since early 2025, there has been a decline in radiosonde launches across the network. This dashboard quantifies that decline using publicly available data from NOAA's Integrated Global Radiosonde Archive (IGRA v2.2).

---

## Dashboard features

| Tab | What it shows |
|---|---|
| **Time Series** | Daily launch counts with 7-day rolling average and a shaded region marking the network impact date |
| **Station Map** | Green/red launch status by synoptic cycle (00Z or 12Z), selectable by date; reporting rate heatmap |
| **Station Impact** | 60-day comparison per station before and after March 2025, sorted by largest decline |
| **Rankings** | All stations ranked by reporting rate with top 10 / bottom 10 tables |

**KPI cards** at the top show fleet-level stats at a glance: stations reporting, average daily launches (pre → post), launch reduction %, and stations no longer reporting.

---

## Project structure

```
igra-radiosonde-monitor/
├── app.py                        # Dash web dashboard — run this
├── queries.py                    # DuckDB SQL query layer
├── ingest.py                     # Data pipeline: NCEI → DuckDB
├── load_metadata.py              # Load station metadata
├── setup.py                      # One-command bootstrap script
├── start.sh                      # Render start script — pulls DB from R2, then launches gunicorn
├── .github/
│   └── workflows/
│       └── ingest.yaml           # Scheduled ingest (01:30Z / 13:30Z) + Render restart
├── assets/
│   └── style.css                 # Dashboard styling
├── conf/
│   └── stations_us.txt           # Station list
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

## Data modes: launches vs. full profile

`ingest.py` supports two ingest modes via `--mode`:

```bash
# One row per sounding (default) — fast, dashboard-ready, ~79K rows
python ingest.py --stations-file conf/stations_us.txt --db data/igra.duckdb --mode launches

# One row per pressure level per sounding — full vertical profile, ~17M rows
python ingest.py --stations-file conf/stations_us.txt --db data/igra.duckdb --mode full
```

| Mode | Rows | Use case |
|---|---|---|
| `launches` *(default)* | ~79K (one per sounding) | Everything this dashboard needs: launch counts, reporting rates, station status |
| `full` | ~17M (one per pressure level) | Vertical profile analysis — temperature/humidity/wind by altitude, not currently surfaced in the dashboard but available for future work |

> **Note:** `setup.py` always bootstraps in `launches` mode. To pull full vertical-profile data, run `ingest.py` directly with `--mode full` after the initial setup.

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

DuckDB is an in-process analytical database — no server required, extremely fast for aggregation queries over millions of rows. In `launches` mode (the dashboard's default) the `soundings` table holds one row per sounding; switching to `full` mode scales that up to ~17M rows, one per pressure level per sounding, with dashboard queries still running in milliseconds.

### Why Dash over Streamlit?

Dash apps are Flask under the hood, making them straightforward to deploy on any Python host with gunicorn. The callback architecture also gives precise control over what re-renders when, which matters for a multi-tab dashboard with multiple interdependent filters.

---

## Key SQL patterns used

**Daily launch counts:**
```sql
SELECT
    DATE_TRUNC('day', timezone('UTC', time)) AS date,
    COUNT(*) AS total_launches,
    COUNT(DISTINCT station) AS stations_reporting
FROM soundings
WHERE time >= '2025-01-01'
GROUP BY 1
ORDER BY 1
```

**Station impact — equal 60-day windows before/after the network impact date:**
```sql
SELECT
    station,
    COUNT(*) FILTER (
        WHERE time >= '2025-03-01'::DATE - INTERVAL '60 days'
          AND time <  '2025-03-01'
    ) AS pre_cut,
    COUNT(*) FILTER (
        WHERE time >= '2025-03-01'
          AND time <  '2025-03-01'::DATE + INTERVAL '60 days'
    ) AS post_cut
FROM soundings
GROUP BY station
ORDER BY post_cut - pre_cut
```

**Station reporting rate — fixed window anchored to the monitoring start date, so every station is measured on the same scale:**
```sql
SELECT
    station,
    COUNT(*) / NULLIF(DATEDIFF('day', '2025-01-01', CURRENT_DATE) * 2.0, 0) * 100 AS reporting_rate
FROM soundings
WHERE time >= '2025-01-01'
GROUP BY station
```

---

## Data source

**IGRA v2.2** — Integrated Global Radiosonde Archive
https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive

Data is fetched from the data-y2d directory (year-to-date files), updated daily by NCEI.

---

## Deployment

The app runs on a scheduled ingest job that updates the database twice daily (after 00Z and 12Z synoptic hours), then restarts the live dashboard so it picks up the fresh data:

- **Web host:** Render (Flask/gunicorn)
- **Database storage:** Cloudflare R2 (S3-compatible, zero egress fees)
- **Scheduled ingest:** GitHub Actions cron (free for public repos), which uploads the refreshed database to R2 and then calls Render's restart API so the running service re-downloads it — no full rebuild needed

```bash
# Start command for Render
gunicorn app:server
```

---

## Author

Kevin Dougherty

Built as a portfolio project demonstrating data engineering, SQL, and interactive visualization skills.
