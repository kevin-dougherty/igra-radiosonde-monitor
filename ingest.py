"""
ingest.py
---------
IGRA ingest pipeline (direct download → DuckDB)

Fetches IGRA2 upper-air data for a list of stations and upserts into DuckDB.

Unlike using Siphon's IGRAUpperAir directly, this module chooses the right
NCEI data directory automatically:

  - data-y2d  : current + previous year only — small files, fast
  - data-por  : full period of record — large files, use only when you need
                historical data beyond what y2d covers

Decision logic:
  If start_date >= Jan 1 of (current_year - 1)  →  use data-y2d
  Otherwise                                      →  use data-por

This means for the typical use case (Jan 2024 → now), we use y2d and avoid
downloading decades of data we don't need.

CLI (standalone):
    python ingest.py --stations-file conf/stations_us.txt \\
        --start-year 2024 --start-month 1 \\
        --end-year 2025 --end-month 6 \\
        --db data/igra.duckdb --workers 8
"""

from __future__ import annotations

import argparse
import calendar
import concurrent.futures as cf
import io
import zipfile
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Iterable

import duckdb
import pandas as pd
import numpy as np
import requests
from tqdm import tqdm


# ── NCEI endpoints ────────────────────────────────────────────────────────────

BASE_URL_YTD = "https://www.ncei.noaa.gov/pub/data/igra/data/data-y2d"
BASE_URL_POR = "https://www.ncei.noaa.gov/pub/data/igra/data/data-por"

# Fixed-width column specs (0-indexed Python slices) from IGRA v2.2 format doc
HEADER_COLSPECS = [(1,12),(13,17),(18,20),(21,23),(24,26),(27,31),(32,36),(55,62),(63,71)]
HEADER_NAMES    = ["station","year","month","day","hour","reltime","numlev","lat_raw","lon_raw"]

DATA_COLSPECS = [(0,1),(1,2),(3,8),(9,15),(16,21),(22,27),(28,33),(34,39),(40,45),(46,51)]
DATA_NAMES    = ["lvltyp1","lvltyp2","etime","pressure","gph","t","rh","dpdp","wdir","wspd"]

MISSING = {-9999, -8888, -99999}


# ── Directory selection ───────────────────────────────────────────────────────

def _choose_source(start: datetime) -> tuple[str, str, str]:
    """
    Return (base_url, filename_suffix, label) for the appropriate NCEI directory.

    data-y2d : files named {station}-data-beg{year}.txt.zip
               Contains data from Jan 1 of the PREVIOUS year through today.
               So in 2026, the files are named -data-beg2025.txt.zip and
               cover all of 2025 and 2026 year-to-date.

    data-por : files named {station}-data.txt.zip
               Full period of record — use only for data older than y2d covers.
    """
    now = datetime.now(UTC)
    # y2d files go back to Jan 1 of last year
    ytd_cutoff = datetime(now.year - 1, 1, 1, tzinfo=UTC)
    # y2d files are named with the previous year (e.g. in 2026 → beg2025)
    beg_year = now.year - 1

    if start >= ytd_cutoff:
        suffix = f"-data-beg{beg_year}.txt.zip"
        return BASE_URL_YTD, suffix, f"data-y2d ({suffix})"
    else:
        return BASE_URL_POR, "-data.txt.zip", "data-por (full history — may be slow)"


# ── Download + parse ──────────────────────────────────────────────────────────

def _fetch_raw_text(
    station: str,
    base_url: str,
    suffix: str,
    timeout: int = 60,
    cache_dir: Path | None = None,
) -> str:
    """
    Download and decompress a station zip file, return raw text.

    If cache_dir is provided, saves the zip locally on first download
    and reads from disk on subsequent runs — avoids re-hitting NCEI.
    Retries once on timeout before giving up.
    """
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{station}{suffix}"
        if cached.exists():
            with zipfile.ZipFile(cached) as zf:
                return zf.read(zf.namelist()[0]).decode("latin-1")

    url = f"{base_url}/{station}{suffix}"
    for attempt in (1, 2):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            # Save to cache before decompressing
            if cache_dir is not None:
                cached.write_bytes(resp.content)
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                return zf.read(zf.namelist()[0]).decode("latin-1")
        except requests.Timeout:
            if attempt == 2:
                raise requests.Timeout(f"{station}: timed out after {timeout}s (both attempts)")
            import time; time.sleep(3)


def _parse_igra_text(
    text: str,
    station: str,
    start: datetime,
    end: datetime,
    synoptic_only: bool,
) -> pd.DataFrame:
    """
    Parse raw IGRA fixed-width text into a tidy level DataFrame,
    filtered to the requested date window.

    Returns columns: station, time, pressure, gph, t, td, wspd, wdir
    """
    header_rows = []
    level_rows  = []
    current_header = None
    current_numlev = 0
    level_count    = 0

    for line in text.splitlines():
        if not line:
            continue

        if line[0] == "#":
            # Parse header
            try:
                yr  = int(line[13:17])
                mo  = int(line[18:20])
                dy  = int(line[21:23])
                hr  = int(line[24:26])
                nlv = int(line[32:36])
                lat = int(line[55:62].strip() or "-9999") / 10000
                lon = int(line[63:71].strip() or "-9999") / 10000
            except (ValueError, IndexError):
                current_header = None
                continue

            # Build datetime; hour=99 means missing, treat as 00Z
            hr_clean = hr if hr != 99 else 0
            try:
                dt = datetime(yr, mo, dy, hr_clean, tzinfo=UTC)
            except ValueError:
                current_header = None
                continue

            # Filter to requested window
            if not (start <= dt <= end):
                current_header = None
                current_numlev = nlv
                level_count    = 0
                continue

            # Filter to synoptic hours if requested
            if synoptic_only and hr_clean not in (0, 12):
                current_header = None
                current_numlev = nlv
                level_count    = 0
                continue

            current_header = {"station": station, "time": dt, "lat": lat, "lon": lon}
            current_numlev = nlv
            level_count    = 0

        else:
            if current_header is None or level_count >= current_numlev:
                level_count += 1
                continue

            try:
                def _i(s): return int(s) if s.strip() else -9999

                press = _i(line[9:15])
                gph   = _i(line[16:21])
                temp  = _i(line[22:27])
                rh    = _i(line[28:33])
                dpdp  = _i(line[34:39])
                wdir  = _i(line[40:45])
                wspd  = _i(line[46:51])

                def _clean(v): return float(v) if v not in MISSING else np.nan

                # Dewpoint = temp - dewpoint depression
                temp_c  = _clean(temp)  / 10 if temp not in MISSING else np.nan
                dpdp_c  = _clean(dpdp)  / 10 if dpdp not in MISSING else np.nan
                td_c    = (temp_c - dpdp_c) if (temp_c is not np.nan and dpdp_c is not np.nan) else np.nan

                level_rows.append({
                    "station":  current_header["station"],
                    "time":     current_header["time"],
                    "lat":      current_header["lat"],
                    "lon":      current_header["lon"],
                    "pressure": _clean(press) / 100 if press not in MISSING else np.nan,  # Pa→hPa
                    "gph":      _clean(gph)   if gph   not in MISSING else np.nan,
                    "t":        temp_c,
                    "td":       td_c,
                    "wspd":     _clean(wspd)  / 10 if wspd not in MISSING else np.nan,    # tenths m/s → m/s
                    "wdir":     _clean(wdir)  if wdir  not in MISSING else np.nan,
                })
            except (ValueError, IndexError):
                pass

            level_count += 1

    if not level_rows:
        return pd.DataFrame()

    df = pd.DataFrame(level_rows)
    return (
        df.sort_values(["station", "time", "pressure"])
        .drop_duplicates(subset=["station", "time", "pressure"])
        .reset_index(drop=True)
    )


def fetch_station(
    station: str,
    base_url: str,
    suffix: str,
    start: datetime,
    end: datetime,
    synoptic_only: bool,
    timeout: int = 60,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """Fetch + parse one station. Returns level DataFrame."""
    text = _fetch_raw_text(station, base_url, suffix, timeout=timeout, cache_dir=cache_dir)
    return _parse_igra_text(text, station, start, end, synoptic_only)


# ── DuckDB upsert ─────────────────────────────────────────────────────────────

def upsert_soundings(db_path: Path, df: pd.DataFrame) -> None:
    """
    Upsert a batch of level rows into the soundings table.

    Uses INSERT OR IGNORE on the (station, time, pressure) natural key
    so re-running the ingest is safe and idempotent.
    """
    if df.empty:
        return

    # Drop rows missing any part of the primary key — they can't be inserted
    # and are not useful (pressure=NaN means we can't identify the level)
    df = df.dropna(subset=["station", "time", "pressure"])

    con = duckdb.connect(str(db_path))

    # Check if soundings table exists but lacks a primary key (legacy schema).
    # If so, recreate it with the PK so INSERT OR IGNORE works correctly.
    tables = con.execute("SHOW TABLES").fetchdf()
    if "soundings" in tables["name"].values:
        try:
            con.execute("INSERT OR IGNORE INTO soundings SELECT * FROM soundings WHERE 1=0")
        except duckdb.BinderException:
            # Old table has no PK — migrate it
            print("  ↻  Migrating soundings table to add primary key...")
            con.execute("ALTER TABLE soundings RENAME TO soundings_old")
            con.execute("""
                CREATE TABLE soundings (
                    station  TEXT    NOT NULL,
                    time     TIMESTAMPTZ NOT NULL,
                    pressure DOUBLE  NOT NULL,
                    gph      DOUBLE,
                    t        DOUBLE,
                    td       DOUBLE,
                    wspd     DOUBLE,
                    wdir     DOUBLE,
                    lat      DOUBLE,
                    lon      DOUBLE,
                    PRIMARY KEY (station, time, pressure)
                )
            """)
            con.execute("INSERT OR IGNORE INTO soundings SELECT * FROM soundings_old")
            con.execute("DROP TABLE soundings_old")
            print("  ✓  Migration complete.")

    # Create table fresh if it doesn't exist yet
    con.execute("""
        CREATE TABLE IF NOT EXISTS soundings (
            station  TEXT    NOT NULL,
            time     TIMESTAMPTZ NOT NULL,
            pressure DOUBLE  NOT NULL,
            gph      DOUBLE,
            t        DOUBLE,
            td       DOUBLE,
            wspd     DOUBLE,
            wdir     DOUBLE,
            lat      DOUBLE,
            lon      DOUBLE,
            PRIMARY KEY (station, time, pressure)
        )
    """)

    con.register("_df", df)

    # INSERT OR IGNORE skips rows that already exist (idempotent re-runs)
    con.execute("""
        INSERT OR IGNORE INTO soundings
            (station, time, pressure, gph, t, td, wspd, wdir, lat, lon)
        SELECT station, time, pressure, gph, t, td, wspd, wdir, lat, lon
        FROM _df
    """)
    con.close()


def upsert_stations(db_path: Path, df: pd.DataFrame) -> None:
    """
    Build / refresh the stations dimension table from level data.
    One row per station with the most recent lat/lon on record.
    """
    if df.empty or "station" not in df.columns:
        return

    meta = (
        df.sort_values("time")
        .groupby("station")
        .last()[["lat", "lon"]]
        .reset_index()
    )

    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE IF NOT EXISTS stations (station TEXT PRIMARY KEY, lat DOUBLE, lon DOUBLE)")
    con.register("_meta", meta)
    con.execute("INSERT OR REPLACE INTO stations SELECT station, lat, lon FROM _meta")
    con.close()


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run(
    stations: Iterable[str],
    start: datetime,
    end: datetime,
    db_path: Path,
    workers: int = 8,
    all_hours: bool = False,
    cache_dir: Path | None = None,
) -> None:
    """
    Fetch and upsert a date window for multiple stations concurrently.

    Args:
        stations   : Iterable of IGRA2 station IDs
        start      : UTC start datetime (timezone-aware, first of a month)
        end        : UTC end datetime (timezone-aware, last of a month)
        db_path    : DuckDB file path (created if missing)
        workers    : Threadpool size
        all_hours  : If False (default), keep only 00Z and 12Z soundings
        cache_dir  : If set, zip files are cached here and reused on re-runs
    """
    station_list = list(stations)
    base_url, suffix, url_label = _choose_source(start)

    print(f"  Source : {url_label}")
    print(f"  URL    : {base_url}")
    if cache_dir:
        print(f"  Cache  : {cache_dir}")

    synoptic_only = not all_hours
    pieces: list[pd.DataFrame] = []

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(
                fetch_station, s, base_url, suffix, start, end,
                synoptic_only, 60, cache_dir
            ): s
            for s in station_list
        }
        bar = tqdm(cf.as_completed(futs), total=len(futs), desc="Fetching", unit="stn")
        for fut in bar:
            station = futs[fut]
            bar.set_postfix({"last": station}, refresh=True)
            try:
                df = fut.result()
                if not df.empty:
                    pieces.append(df)
                    bar.set_postfix({"last": station, "rows": f"{len(df):,}"}, refresh=True)
            except requests.HTTPError as e:
                if e.response.status_code == 404:
                    tqdm.write(f"  ⚠  {station}: no file found (station may be inactive)")
                else:
                    tqdm.write(f"  ✗  {station}: HTTP {e.response.status_code}")
            except requests.Timeout:
                tqdm.write(f"  ✗  {station}: timed out — skipping")
            except Exception as e:
                tqdm.write(f"  ✗  {station}: {e}")

    if not pieces:
        print("No data fetched — check station IDs and date range.")
        return

    df_all = pd.concat(pieces, ignore_index=True)
    print(f"\n  Parsed {len(df_all):,} level rows from {df_all['station'].nunique()} stations")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    upsert_soundings(db_path, df_all)
    upsert_stations(db_path, df_all)
    print(f"  Written to {db_path}")


# ── CLI (standalone use) ──────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    now = datetime.now(UTC)
    p = argparse.ArgumentParser(description="IGRA ingest → DuckDB")
    p.add_argument("--stations-file", required=True,
                   help="Text file with one IGRA2 station ID per line")
    p.add_argument("--db", default="data/igra.duckdb")
    p.add_argument("--start-year",  type=int, default=2024)
    p.add_argument("--start-month", type=int, default=1, choices=range(1,13), metavar="MONTH")
    p.add_argument("--end-year",    type=int, default=now.year)
    p.add_argument("--end-month",   type=int, default=now.month, choices=range(1,13), metavar="MONTH")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--all-hours", action="store_true")
    p.add_argument(
        "--cache-dir", default="data/zip_cache",
        help="Directory to cache downloaded zip files (default: data/zip_cache). "
             "Set to empty string to disable caching."
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    with open(args.stations_file) as f:
        stations = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

    start = datetime(args.start_year, args.start_month, 1, tzinfo=UTC)
    last_day = calendar.monthrange(args.end_year, args.end_month)[1]
    end = datetime(args.end_year, args.end_month, last_day, 23, 59, 59, tzinfo=UTC)

    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    run(stations, start, end, Path(args.db), args.workers, args.all_hours, cache_dir)
