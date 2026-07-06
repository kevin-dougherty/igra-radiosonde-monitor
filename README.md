# 📡 IGRA Radiosonde Monitor

> A portfolio-quality dashboard tracking NOAA IGRA v2.2 upper-air sounding data —
> with a focus on quantifying how 2025 federal budget cuts affected radiosonde
> launches and, by extension, numerical weather prediction.

---

## Project structure

```
igra_dashboard/
├── igra_parser.py      # Data ingestion: parses IGRA fixed-width format → DataFrames
├── igra_analytics.py   # Pure analytics: time series, gap detection, budget comparison
├── igra_viz.py         # Plotly figures library (dark-themed, reusable)
├── app.py              # Dash web dashboard (run this!)
├── download_data.py    # Bulk downloader for NCEI data files
├── quickstart.py       # Quick exploration script (no Dash required)
└── requirements.txt
```

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Download data

```bash
# All US stations (≈90 stations, ~100–200 MB zipped)
python download_data.py --country US --output ./data/y2d

# Or specific stations
python download_data.py \
  --stations USM00072201,USM00072210,USM00072220 \
  --output ./data/y2d
```

### 3. Explore with the quickstart script

```bash
python quickstart.py --data-dir ./data/y2d --station USM00072201
```
This writes standalone HTML figures to `./output/`.

### 4. Run the full dashboard

```bash
python app.py
```
Then open **http://127.0.0.1:8050** in your browser.

In the sidebar:
- Set **Data directory** to `./data/y2d`
- Leave station filter blank to load all, or paste comma-separated IDs
- Click **Load / Refresh**

---

## Key design decisions

### Why pandas over xarray?

| Consideration | Winner |
|---|---|
| Primary use case: station-level statistics, counts, trends | **pandas** |
| Multi-dimensional ops (gridded model output, satellite swaths) | xarray |
| Joining with metadata, groupby, resampling | **pandas** |
| Vertical profile analysis across all levels | xarray (optional) |

The parser returns two DataFrames:
- `headers_df` — one row per **sounding** (the # lines); great for fleet stats
- `levels_df` — one row per **pressure level**; use this if you want to study
  temperature, wind, or humidity profiles

If you need to do 3D analysis (e.g., temperature anomalies by pressure level
over time), convert `levels_df` to xarray like this:

```python
import xarray as xr
ds = levels_df.set_index(["sounding_idx", "PRESS_HPA"]).to_xarray()
```

### Why Dash over Streamlit / Panel?

Dash gives you fine-grained callback control, good Plotly integration, and
produces deployable Flask apps — more portfolio-friendly for showcasing to
employers who may ask "how would you scale this?".

---

## Dashboard features

| Tab | What it shows |
|---|---|
| **Time Series** | Daily launch counts + 7-day rolling average, stations reporting per day, hourly distribution |
| **Station Map** | Green = reported in past 2 days, Red = did not report; completeness heat map |
| **Station Rankings** | Top & bottom N stations by completeness; launch gap table |
| **Budget Impact** | Pre vs post budget cut comparison per station; stations that went silent |
| **Deep Dive** | Vertical temperature/wind profile for any individual sounding |

---

## Data format notes (IGRA v2.2)

### Header line (starts with `#`)

| Field | Columns | Notes |
|---|---|---|
| ID | 2–12 | Station identifier |
| YEAR/MONTH/DAY | 14–23 | Sounding date |
| HOUR | 25–26 | UTC; `99` = missing |
| NUMLEV | 33–36 | Number of data records that follow |
| LAT | 56–62 | Integer × 10,000 → decimal degrees |
| LON | 64–71 | Integer × 10,000 → decimal degrees |

### Data record (pressure level rows)

| Field | Units stored | Physical units |
|---|---|---|
| PRESS | Pa × 100 | Divide by 100 → hPa |
| TEMP | °C × 10 | Divide by 10 → °C |
| RH | % × 10 | Divide by 10 → % |
| WSPD | m/s × 10 | Divide by 10 → m/s |
| Missing | `-9999` or `-8888` | Replaced with `NaN` |

---

## Deployment

The app can be deployed to **Render**, **Railway**, or **Fly.io** with minimal changes:

```bash
# Render/Railway: set start command to:
gunicorn app:server

# Add gunicorn to requirements.txt:
echo "gunicorn>=21" >> requirements.txt
```

For production, replace the local `data_dir` workflow with an S3 bucket or
a PostgreSQL database pre-loaded with parsed launch records.

---

## Extending this project

Ideas to make it an even stronger portfolio piece:

- **Forecast impact quantification**: correlate launch frequency with
  GFS/NAM model error (requires NWP verification data from NCEI)
- **Anomaly detection**: flag statistically unusual drops in launch counts
  (z-score or CUSUM control chart)
- **Station-specific trend analysis**: linear regression of monthly
  launch counts per station since 2020
- **Cost analysis**: estimate cost-per-sounding and total savings vs.
  forecast accuracy degradation
- **Real-time updates**: schedule the downloader with cron/GitHub Actions
  and serve fresh data automatically

---

## Data source

IGRA v2.2 — Integrated Global Radiosonde Archive  
https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive

Format documentation:  
https://www.ncei.noaa.gov/pub/data/igra/data/igra2-data-format.txt
