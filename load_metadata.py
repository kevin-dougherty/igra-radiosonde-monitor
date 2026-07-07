"""
load_metadata.py
----------------
Downloads the IGRA station list and loads city/state metadata
into the station_meta table in DuckDB.

Run standalone:
    python load_metadata.py
    python load_metadata.py --db data/igra.duckdb

Called by GitHub Actions after each ingest run to keep
station names up to date.
"""

import argparse
from pathlib import Path

import duckdb
import pandas as pd

STATION_LIST_URL = (
    "https://www.ncei.noaa.gov/data/integrated-global-"
    "radiosonde-archive/doc/igra2-station-list.txt"
)

COL_WIDTHS = [11, 9, 10, 7, 3, 30, 6, 5, 8]
COL_NAMES  = ["stnid", "lat", "lon", "elev", "state",
               "city", "start_year", "end_year", "data"]


def load_metadata(db_path: str = "data/igra.duckdb") -> None:
    print("Downloading IGRA station list...")
    df = pd.read_fwf(
        STATION_LIST_URL,
        names=COL_NAMES,
        widths=COL_WIDTHS,
        skiprows=3,
    )

    # Filter to US stations including Puerto Rico and Guam
    df = df[df["stnid"].astype(str).str.startswith(("US", "RQM", "GQM"))].copy()

    # Clean city names
    df["city"] = (
        df["city"].astype(str)
        .str.split(";").str[0]
        .str.split("/").str[0]
        .str.strip()
        .str.title()
    )
    df["state"] = df["state"].astype(str).str.strip().replace("nan", "")

    # Build display name
    def make_display(row):
        city  = str(row["city"]).strip()
        state = str(row["state"]).strip()
        if state and state.lower() not in ("nan", "none", ""):
            return f"{city}, {state}"
        return city

    df["display_name"] = df.apply(make_display, axis=1)
    meta = df[["stnid", "city", "state", "display_name"]].rename(
        columns={"stnid": "station_id"}
    )

    # Write to DuckDB
    print(f"Loading {len(meta)} stations into {db_path}...")
    con = duckdb.connect(db_path)
    con.execute("DROP TABLE IF EXISTS station_meta")
    con.register("_meta", meta)
    con.execute("""
        CREATE TABLE station_meta AS
        SELECT station_id, city, state, display_name
        FROM _meta
    """)
    con.close()
    print(f"✓ station_meta table created with {len(meta)} rows.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load IGRA station metadata into DuckDB")
    parser.add_argument("--db", default="data/igra.duckdb",
                        help="Path to DuckDB file (default: data/igra.duckdb)")
    args = parser.parse_args()
    load_metadata(args.db)
