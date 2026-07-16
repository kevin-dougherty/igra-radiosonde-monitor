"""
ingest.py
---------
IGRA ingest pipeline (direct download → DuckDB)

Two storage modes:
  --mode launches  (default) — one row per sounding, ~170k rows
                               fast queries, small database, ideal for dashboard
  --mode full      — one row per pressure level, ~17M rows
                     full vertical profile data for atmospheric analysis

CLI:
    # Dashboard mode (default)
    python ingest.py --stations-file conf/stations_us.txt \\
        --start-year 2025 --start-month 1 --db data/igra.duckdb

    # Full profile mode
    python ingest.py --stations-file conf/stations_us.txt \\
        --start-year 2025 --start-month 1 --db data/igra_full.duckdb --mode full
"""

from __future__ import annotations

import argparse
import calendar
import concurrent.futures as cf
import io
import time
import zipfile
from datetime import datetime, UTC
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

MISSING = {-9999, -8888, -99999}


# ── Directory selection ───────────────────────────────────────────────────────

# NCEI rotates the "beg{year}" suffix on the data-y2d files at some point in
# each year, but not on a fixed, predictable schedule (observed rotating to
# beg2026 on 2026-07-15, mid-year — not on Jan 1 as originally assumed).
# Rather than hardcode an offset that silently breaks every station at once
# when NCEI rotates again, probe for the live suffix against a station that's
# essentially guaranteed to exist (Key West), and cache the result per run.
_PROBE_STATION = "USM00072201"

def _resolve_ytd_suffix(timeout: int = 15) -> str:
    """Find the current live '-data-beg{year}.txt.zip' suffix by probing
    candidate years, newest first, with a lightweight HEAD request."""
    now = datetime.now(UTC)
    for beg_year in (now.year, now.year - 1, now.year - 2):
        suffix = f"-data-beg{beg_year}.txt.zip"
        url = f"{BASE_URL_YTD}/{_PROBE_STATION}{suffix}"
        try:
            resp = requests.head(url, timeout=timeout, allow_redirects=True)
            if resp.status_code == 200:
                return suffix
        except requests.RequestException:
            continue
    raise RuntimeError(
        "Could not find a live data-y2d suffix (tried "
        f"{now.year}, {now.year - 1}, {now.year - 2}). "
        "NCEI's directory layout or naming convention may have changed — "
        "check https://www.ncei.noaa.gov/pub/data/igra/data/data-y2d/ manually."
    )


def _choose_source(start: datetime) -> tuple[str, str, str]:
    now = datetime.now(UTC)
    ytd_cutoff = datetime(now.year - 1, 1, 1, tzinfo=UTC)
    if start >= ytd_cutoff:
        suffix = _resolve_ytd_suffix()
        return BASE_URL_YTD, suffix, f"data-y2d ({suffix})"
    else:
        return BASE_URL_POR, "-data.txt.zip", "data-por (full history — may be slow)"


# ── Download ──────────────────────────────────────────────────────────────────

def _fetch_raw_text(
    station: str,
    base_url: str,
    suffix: str,
    timeout: int = 60,
    cache_dir: Path | None = None,
) -> str:
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
            if cache_dir is not None:
                cached.write_bytes(resp.content)
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                return zf.read(zf.namelist()[0]).decode("latin-1")
        except requests.Timeout:
            if attempt == 2:
                raise requests.Timeout(f"{station}: timed out after {timeout}s")
            time.sleep(3)


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_launches(
    text: str,
    station: str,
    start: datetime,
    end: datetime,
    synoptic_only: bool,
) -> pd.DataFrame:
    """
    Parse IGRA text → one row per sounding (launch mode).
    Columns: station, time, lat, lon
    """
    rows = []

    for line in text.splitlines():
        if not line or line[0] != "#":
            continue
        try:
            yr  = int(line[13:17])
            mo  = int(line[18:20])
            dy  = int(line[21:23])
            hr  = int(line[24:26])
            lat = int(line[55:62].strip() or "-9999") / 10000
            lon = int(line[63:71].strip() or "-9999") / 10000
        except (ValueError, IndexError):
            continue

        hr_clean = hr if hr != 99 else 0
        try:
            dt = datetime(yr, mo, dy, hr_clean, tzinfo=UTC)
        except ValueError:
            continue

        if not (start <= dt <= end):
            continue
        if synoptic_only and hr_clean not in (0, 12):
            continue

        rows.append({"station": station, "time": dt, "lat": lat, "lon": lon})

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _parse_full(
    text: str,
    station: str,
    start: datetime,
    end: datetime,
    synoptic_only: bool,
) -> pd.DataFrame:
    """
    Parse IGRA text → one row per pressure level (full mode).
    Columns: station, time, lat, lon, pressure, gph, t, td, wspd, wdir
    """
    level_rows = []
    current_header = None
    current_numlev = 0
    level_count = 0

    for line in text.splitlines():
        if not line:
            continue

        if line[0] == "#":
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

            hr_clean = hr if hr != 99 else 0
            try:
                dt = datetime(yr, mo, dy, hr_clean, tzinfo=UTC)
            except ValueError:
                current_header = None
                continue

            if not (start <= dt <= end):
                current_header = None
                current_numlev = nlv
                level_count = 0
                continue

            if synoptic_only and hr_clean not in (0, 12):
                current_header = None
                current_numlev = nlv
                level_count = 0
                continue

            current_header = {"station": station, "time": dt, "lat": lat, "lon": lon}
            current_numlev = nlv
            level_count = 0

        else:
            if current_header is None or level_count >= current_numlev:
                level_count += 1
                continue

            try:
                def _i(s): return int(s) if s.strip() else -9999
                press = _i(line[9:15])
                gph   = _i(line[16:21])
                temp  = _i(line[22:27])
                dpdp  = _i(line[34:39])
                wdir  = _i(line[40:45])
                wspd  = _i(line[46:51])

                def _clean(v): return float(v) if v not in MISSING else np.nan

                temp_c = _clean(temp) / 10 if temp not in MISSING else np.nan
                dpdp_c = _clean(dpdp) / 10 if dpdp not in MISSING else np.nan
                td_c   = (temp_c - dpdp_c) if not (np.isnan(temp_c) or np.isnan(dpdp_c)) else np.nan

                level_rows.append({
                    **current_header,
                    "pressure": _clean(press) / 100 if press not in MISSING else np.nan,
                    "gph":      _clean(gph) if gph not in MISSING else np.nan,
                    "t":        temp_c,
                    "td":       td_c,
                    "wspd":     _clean(wspd) / 10 if wspd not in MISSING else np.nan,
                    "wdir":     _clean(wdir) if wdir not in MISSING else np.nan,
                })
            except (ValueError, IndexError):
                pass
            level_count += 1

    return pd.DataFrame(level_rows) if level_rows else pd.DataFrame()


def fetch_station(
    station: str,
    base_url: str,
    suffix: str,
    start: datetime,
    end: datetime,
    synoptic_only: bool,
    mode: str = "launches",
    timeout: int = 60,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    text = _fetch_raw_text(station, base_url, suffix, timeout=timeout, cache_dir=cache_dir)
    if mode == "full":
        return _parse_full(text, station, start, end, synoptic_only)
    return _parse_launches(text, station, start, end, synoptic_only)


# ── DuckDB upsert ─────────────────────────────────────────────────────────────

def upsert_launches(db_path: Path, df: pd.DataFrame) -> None:
    """Upsert launch-mode rows (one per sounding)."""
    if df.empty:
        return

    df = df.dropna(subset=["station", "time"])
    con = duckdb.connect(str(db_path))

    # Migrate old full schema to launches schema if needed
    tables = con.execute("SHOW TABLES").fetchdf()
    if "soundings" in tables["name"].values:
        cols = con.execute("DESCRIBE soundings").fetchdf()["column_name"].tolist()
        if "pressure" in cols:
            print("  ↻  Migrating from full schema to launches schema...")
            con.execute("DROP TABLE soundings")

    con.execute("""
        CREATE TABLE IF NOT EXISTS soundings (
            station TEXT    NOT NULL,
            time    TIMESTAMPTZ NOT NULL,
            lat     DOUBLE,
            lon     DOUBLE,
            PRIMARY KEY (station, time)
        )
    """)
    con.register("_df", df)
    con.execute("""
        INSERT OR IGNORE INTO soundings (station, time, lat, lon)
        SELECT station, time, lat, lon FROM _df
    """)
    con.close()


def upsert_full(db_path: Path, df: pd.DataFrame) -> None:
    """Upsert full-mode rows (one per pressure level)."""
    if df.empty:
        return

    df = df.dropna(subset=["station", "time", "pressure"])
    con = duckdb.connect(str(db_path))
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
    con.execute("""
        INSERT OR IGNORE INTO soundings
        SELECT station, time, pressure, gph, t, td, wspd, wdir, lat, lon
        FROM _df
    """)
    con.close()


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run(
    stations: Iterable[str],
    start: datetime,
    end: datetime,
    db_path: Path,
    workers: int = 8,
    all_hours: bool = False,
    mode: str = "launches",
    cache_dir: Path | None = None,
) -> None:
    station_list = list(stations)
    base_url, suffix, url_label = _choose_source(start)

    print(f"  Mode   : {mode}")
    print(f"  Source : {url_label}")
    if cache_dir:
        print(f"  Cache  : {cache_dir}")

    synoptic_only = not all_hours
    pieces: list[pd.DataFrame] = []

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(
                fetch_station, s, base_url, suffix, start, end,
                synoptic_only, mode, 60, cache_dir
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
                    tqdm.write(f"  ⚠  {station}: no file found")
                else:
                    tqdm.write(f"  ✗  {station}: HTTP {e.response.status_code}")
            except requests.Timeout:
                tqdm.write(f"  ✗  {station}: timed out — skipping")
            except Exception as e:
                tqdm.write(f"  ✗  {station}: {e}")

    if not pieces:
        print("No data fetched from any station — aborting without writing to the database.")
        raise SystemExit(1)

    df_all = pd.concat(pieces, ignore_index=True)
    print(f"\n  Parsed {len(df_all):,} rows from {df_all['station'].nunique()} stations")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "full":
        upsert_full(db_path, df_all)
    else:
        upsert_launches(db_path, df_all)
    print(f"  Written to {db_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    now = datetime.now(UTC)
    p = argparse.ArgumentParser(description="IGRA ingest → DuckDB")
    p.add_argument("--stations-file", required=True)
    p.add_argument("--db", default="data/igra.duckdb")
    p.add_argument("--start-year",  type=int, default=2025)
    p.add_argument("--start-month", type=int, default=1, choices=range(1,13), metavar="MONTH")
    p.add_argument("--end-year",    type=int, default=now.year)
    p.add_argument("--end-month",   type=int, default=now.month, choices=range(1,13), metavar="MONTH")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--all-hours", action="store_true")
    p.add_argument("--cache-dir", default="data/zip_cache")
    p.add_argument(
        "--mode", choices=["launches", "full"], default="launches",
        help="'launches' = one row per sounding (default, fast, dashboard-ready). "
             "'full' = one row per pressure level (17M rows, for profile analysis)."
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

    run(stations, start, end, Path(args.db), args.workers,
        args.all_hours, args.mode, cache_dir)
