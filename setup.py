"""
setup.py
--------
One-command project bootstrap. Run this first before launching the dashboard.

What it does, in order:
  1. Creates the project directory structure
  2. Downloads the IGRA station list and filters to active US stations
     (including Puerto Rico and Guam), saves to conf/stations_us.txt
  3. Runs the ingest pipeline (Siphon → DuckDB) for those stations

Usage:
    # Ingest a specific month
    python setup.py --start-year 2024 --start-month 1

    # Ingest a full date range (recommended for budget cut analysis)
    python setup.py --start-year 2024 --start-month 1 --end-year 2025 --end-month 6

    # Default: Jan 2024 through current month
    python setup.py

    # Generate station list only, skip ingest
    python setup.py --skip-ingest

The budget cut date we're analyzing is early 2025, so starting from Jan 2024
gives you a full year of pre-cut baseline plus all post-cut data.
"""

import argparse
import sys
from datetime import datetime, UTC
from pathlib import Path

import duckdb
import pandas as pd


# ── Directory layout ──────────────────────────────────────────────────────────

ROOT     = Path(__file__).parent
CONF_DIR = ROOT / "conf"
DATA_DIR = ROOT / "data"
DB_PATH  = DATA_DIR / "igra.duckdb"
STATIONS_FILE = CONF_DIR / "stations_us.txt"

STATION_LIST_URL = (
    "https://www.ncei.noaa.gov/data/integrated-global-"
    "radiosonde-archive/doc/igra2-station-list.txt"
)

# Column spec for the IGRA station list (fixed-width)
COL_WIDTHS = [11, 9, 10, 7, 3, 30, 6, 5, 8]
COL_NAMES  = ["stnid", "lat", "lon", "elev", "state",
               "city", "start_year", "end_year", "data"]


# ── Step 1: directory structure ───────────────────────────────────────────────

def make_dirs() -> None:
    for d in [CONF_DIR, DATA_DIR]:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  ✓ {d}/")


# ── Step 2: station list ──────────────────────────────────────────────────────

def generate_station_list(overwrite: bool = False) -> tuple[pd.DataFrame, list[str]]:
    """
    Download IGRA station inventory, filter to active US/PR/GU stations,
    write IDs to conf/stations_us.txt, and return (us_df, station_ids).
    us_df is already filtered to just the US stations in station_ids —
    callers should not need to re-filter it.
    """
    if STATIONS_FILE.exists() and not overwrite:
        print(f"  ↷  {STATIONS_FILE} already exists — loading existing list.")
        print(f"     (Pass --overwrite to regenerate.)")
        stations = [
            ln.strip()
            for ln in STATIONS_FILE.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        print(f"  ✓  {len(stations)} stations loaded from existing file.")
        # Still need station metadata — re-download silently, then filter
        # down to just the cached US station IDs (not the full global list)
        try:
            df_full = pd.read_fwf(
                STATION_LIST_URL, names=COL_NAMES,
                widths=COL_WIDTHS, skiprows=3,
            )
            us_df = df_full[df_full["stnid"].astype(str).isin(stations)]
        except Exception:
            us_df = pd.DataFrame(columns=COL_NAMES)
        return us_df, stations

    print(f"  Downloading station list from NCEI...")
    df_full = pd.read_fwf(
        STATION_LIST_URL,
        names=COL_NAMES,
        widths=COL_WIDTHS,
        skiprows=3,
    )

    mask = (
        df_full["stnid"].astype(str).str.startswith(("US", "RQM", "GQM"))
        & (df_full["end_year"] >= 2020)
    )
    us_df = df_full[mask]
    station_ids = us_df["stnid"].tolist()

    if not station_ids:
        print("  ✗  No US stations found — check the station list URL or filter.")
        sys.exit(1)

    lines = [
        "# US IGRA active stations (including Puerto Rico and Guam)",
        f"# Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%MZ')}",
        f"# Source: {STATION_LIST_URL}",
        f"# Count: {len(station_ids)}",
        "",
    ] + station_ids

    STATIONS_FILE.write_text("\n".join(lines) + "\n")
    print(f"  ✓  {len(station_ids)} active US stations → {STATIONS_FILE}")

    if "state" in us_df.columns:
        state_counts = us_df["state"].value_counts().head(10)
        print(f"\n  Top states by station count:")
        for state, count in state_counts.items():
            print(f"    {state or '??':>4}  {count}")

    return us_df, station_ids


# ── Step 3: station metadata → DuckDB ────────────────────────────────────────

def load_station_metadata(df_full: pd.DataFrame) -> None:
    """
    Clean the raw station list and load it into DuckDB as station_meta table.

    Columns: station_id, city, state, display_name
    display_name is a clean human-readable label like "Barrow, AK"
    """
    print("  Loading station metadata into DuckDB...")

    df = df_full.copy()
    df = df.rename(columns={"stnid": "station_id"})

    # Clean city names — remove semicolons, airport codes, trailing junk
    df["city"] = (
        df["city"]
        .astype(str)
        .str.split(";").str[0]          # take everything before first semicolon
        .str.split("/").str[0]          # take everything before first slash
        .str.strip()
        .str.title()                    # Title Case
    )
    df["state"] = df["state"].astype(str).str.strip().replace("nan", "")

    # Build display name
    def make_display(row):
        city  = str(row["city"]).strip()  if not pd.isna(row["city"])  else ""
        state = str(row["state"]).strip() if not pd.isna(row["state"]) else ""
        if state and state.lower() != "nan":
            return f"{city}, {state}"
        return city

    df["display_name"] = df.apply(make_display, axis=1)

    # Keep only columns we need
    meta = df[["station_id", "city", "state", "display_name"]].copy()
    meta = meta[meta["station_id"].astype(str).str.startswith(("US", "RQM", "GQM"))]

    # Write to DuckDB
    con = duckdb.connect(str(DB_PATH))
    con.execute("DROP TABLE IF EXISTS station_meta")
    con.register("_meta", meta)
    con.execute("""
        CREATE TABLE station_meta AS
        SELECT station_id, city, state, display_name
        FROM _meta
    """)
    con.close()
    print(f"  ✓  {len(meta)} stations loaded into station_meta table.")


# ── Step 4: ingest ────────────────────────────────────────────────────────────

def run_ingest(
    stations: list[str],
    start: datetime,
    end: datetime,
    workers: int,
    all_hours: bool,
) -> None:
    """
    Import and run the ingest pipeline from ingest.py.
    Keeping the ingest logic in its own module means it can also
    be run independently via:
        python ingest.py --stations-file conf/stations_us.txt --days 365
    """
    # Late import so setup.py works even if siphon/duckdb aren't installed yet
    try:
        from ingest import run
    except ImportError as e:
        print(f"\n  ✗  Could not import ingest.py: {e}")
        print("     Make sure all dependencies are installed:")
        print("     pip install -r requirements.txt")
        sys.exit(1)

    print(f"  Workers: {workers}  |  Hours: {'all' if all_hours else '00Z + 12Z only'}")
    print(f"  Database: {DB_PATH}\n")

    run(
        stations=stations,
        start=start,
        end=end,
        db_path=DB_PATH,
        workers=workers,
        all_hours=all_hours,
        cache_dir=ROOT / "data" / "zip_cache",
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    now = datetime.now(UTC)

    p = argparse.ArgumentParser(
        description="Bootstrap the IGRA radiosonde dashboard project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Jan 2024 through current month (recommended — full pre/post budget cut window)
  python setup.py --start-year 2024 --start-month 1

  # Specific range
  python setup.py --start-year 2024 --start-month 1 --end-year 2025 --end-month 6

  # Just one month (good for testing)
  python setup.py --start-year 2025 --start-month 3 --end-year 2025 --end-month 3

  # Station list only, no ingest
  python setup.py --skip-ingest
        """,
    )

    # Start date (required)
    p.add_argument(
        "--start-year", type=int, default=2024,
        help="Start year (default: 2024 — one year before budget cuts).",
    )
    p.add_argument(
        "--start-month", type=int, default=1, choices=range(1, 13),
        metavar="MONTH",
        help="Start month 1–12 (default: 1).",
    )

    # End date (defaults to current month)
    p.add_argument(
        "--end-year", type=int, default=now.year,
        help=f"End year (default: current year, {now.year}).",
    )
    p.add_argument(
        "--end-month", type=int, default=now.month, choices=range(1, 13),
        metavar="MONTH",
        help=f"End month 1–12 (default: current month, {now.month}).",
    )

    p.add_argument(
        "--workers", type=int, default=8,
        help="Parallel fetch workers (default: 8). Reduce if you hit rate limits.",
    )
    p.add_argument(
        "--all-hours", action="store_true",
        help="Ingest all launch hours (default: 00Z and 12Z synoptic only).",
    )
    p.add_argument(
        "--skip-ingest", action="store_true",
        help="Generate station list only; skip the data ingest step.",
    )
    p.add_argument(
        "--overwrite", action="store_true",
        help="Re-download and regenerate conf/stations_us.txt even if it exists.",
    )
    return p.parse_args()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    print("=" * 60)
    print("  IGRA Radiosonde Dashboard — Project Setup")
    print("=" * 60)

    # ── Build and validate the date window ───────────────────────────────────
    import calendar

    try:
        start = datetime(args.start_year, args.start_month, 1, tzinfo=UTC)
        # End = last moment of the end month
        last_day = calendar.monthrange(args.end_year, args.end_month)[1]
        end = datetime(args.end_year, args.end_month, last_day, 23, 59, 59, tzinfo=UTC)
    except ValueError as e:
        print(f"\n  ✗  Invalid date: {e}")
        sys.exit(1)

    if start > end:
        print(f"\n  ✗  Start ({args.start_year}-{args.start_month:02d}) is after "
              f"end ({args.end_year}-{args.end_month:02d}). Check your arguments.")
        sys.exit(1)

    now = datetime.now(UTC)
    if end > now:
        print(f"  ⚠  End date is in the future — capping to today.")
        end = now

    print(f"\n  Date window: {start.strftime('%B %Y')} → {end.strftime('%B %Y')}")
    print(f"  ({(end - start).days} days)")

    print("\n[1/3] Creating directory structure...")
    make_dirs()

    print("\n[2/4] Generating US station list...")
    df_full, stations = generate_station_list(overwrite=args.overwrite)

    print("\n[3/4] Loading station metadata into DuckDB...")
    load_station_metadata(df_full)

    if args.skip_ingest:
        print("\n  --skip-ingest flag set; skipping data ingest.")
        print("\nDone. Run the ingest separately when ready:")
        print(f"  python ingest.py --stations-file {STATIONS_FILE} "
              f"--start-year {start.year} --start-month {start.month} "
              f"--end-year {end.year} --end-month {end.month}")
        return

    print("\n[4/4] Running data ingest (this will take a while)...")
    print("      Tip: you can Ctrl+C and restart — the upsert is idempotent.")
    run_ingest(
        stations=stations,
        start=start,
        end=end,
        workers=args.workers,
        all_hours=args.all_hours,
    )

    print("\n" + "=" * 60)
    print("  Setup complete!")
    print(f"  Station list : {STATIONS_FILE}")
    print(f"  Database     : {DB_PATH}")
    print(f"\n  Launch the dashboard:")
    print(f"    python app.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
