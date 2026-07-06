import duckdb
import pandas as pd
from pathlib import Path

DB_PATH = Path("data/igra.duckdb")

def get_connection():
    return duckdb.connect(str(DB_PATH), read_only=True)

def daily_launch_counts() -> pd.DataFrame:
    con = get_connection()
    return con.execute("""
        SELECT
            DATE_TRUNC('day', time) AS date,
            COUNT(DISTINCT station || CHR(124) || CAST(time AS TEXT)) AS total_launches,
            COUNT(DISTINCT station) AS stations_reporting
        FROM soundings
        WHERE time >= '2025-01-01'
        GROUP BY date
        ORDER BY date
    """).df()

def sounding_timeseries() -> pd.DataFrame:
    """
    One row per sounding time (every 12 hours) across all stations.
    Used for the time series chart so we can see fluctuations at each launch cycle.
    """
    con = get_connection()
    return con.execute("""
        SELECT
            DATE_TRUNC('hour', time) AS launch_time,
            COUNT(DISTINCT station)  AS stations_reporting,
            COUNT(DISTINCT station || CHR(124) || CAST(time AS TEXT)) AS total_launches
        FROM soundings
        WHERE time >= '2025-01-01'
        GROUP BY launch_time
        ORDER BY launch_time
    """).df()

def station_names() -> pd.DataFrame:
    """Return station_id → display_name mapping from metadata table."""
    con = get_connection()
    try:
        return con.execute("""
            SELECT station_id, display_name, city, state
            FROM station_meta
        """).df()
    except Exception:
        return pd.DataFrame(columns=["station_id", "display_name", "city", "state"])

def station_summary() -> pd.DataFrame:
    con = get_connection()
    return con.execute("""
        SELECT
            s.station,
            COALESCE(m.display_name, s.station) AS display_name,
            COALESCE(m.display_name, s.station) || ' (' || s.station || ')' AS label,
            MIN(s.lat) AS lat,
            MIN(s.lon) AS lon,
            COUNT(DISTINCT s.station || CHR(124) || CAST(s.time AS TEXT)) AS total_launches,
            MIN(DATE_TRUNC('day', s.time)) AS first_launch,
            MAX(DATE_TRUNC('day', s.time)) AS last_launch,
            (COUNT(DISTINCT s.station || CHR(124) || CAST(s.time AS TEXT)) /
             NULLIF(DATEDIFF('day', MIN(s.time), MAX(s.time)) * 2.0, 0)) * 100 AS reporting_rate
        FROM soundings s
        LEFT JOIN station_meta m ON s.station = m.station_id
        GROUP BY s.station, m.display_name
        ORDER BY total_launches DESC
    """).df()

def station_reporting() -> pd.DataFrame:
    con = get_connection()
    return con.execute("""
        SELECT
            s.station,
            COALESCE(m.display_name, s.station) AS display_name,
            MIN(s.lat) AS lat,
            MIN(s.lon) AS lon,
            COUNT(DISTINCT s.station || CHR(124) || CAST(s.time AS TEXT)) AS total_launches,
            MAX(DATE_TRUNC('day', s.time)) AS last_launch,
            (COUNT(DISTINCT s.station || CHR(124) || CAST(s.time AS TEXT)) /
             NULLIF(DATEDIFF('day', '2025-01-01', CURRENT_DATE) * 2.0, 0)) * 100 AS reporting_rate
        FROM soundings s
        LEFT JOIN station_meta m ON s.station = m.station_id
        WHERE s.time >= '2025-01-01'
        GROUP BY s.station, m.display_name
        ORDER BY reporting_rate DESC
    """).df()

def launch_delta(window_days: int = 60) -> pd.DataFrame:
    """
    Compare launches window_days before vs after the budget cut date.
    Equal windows on both sides for a fair apples-to-apples comparison.
    """
    con = get_connection()
    return con.execute(f"""
        SELECT *,
               post_cut - pre_cut AS delta
        FROM (
            SELECT
                s.station,
                COALESCE(m.display_name, s.station) AS display_name,
                COALESCE(m.display_name, s.station) || ' (' || s.station || ')' AS label,
                COUNT(DISTINCT CAST(s.time AS TEXT)) FILTER (
                    WHERE s.time >= '2025-03-01'::DATE - INTERVAL '{window_days} days'
                      AND s.time <  '2025-03-01'
                ) AS pre_cut,
                COUNT(DISTINCT CAST(s.time AS TEXT)) FILTER (
                    WHERE s.time >= '2025-03-01'
                      AND s.time <  '2025-03-01'::DATE + INTERVAL '{window_days} days'
                ) AS post_cut
            FROM soundings s
            LEFT JOIN station_meta m ON s.station = m.station_id
            GROUP BY s.station, m.display_name
        ) sub
        ORDER BY delta
    """).df()

def available_years() -> list[int]:
    """Return sorted list of years with data in the database."""
    con = get_connection()
    return sorted([
        int(r[0]) for r in
        con.execute("""
            SELECT DISTINCT EXTRACT(year FROM timezone('UTC', time))
            FROM soundings ORDER BY 1
        """).fetchall()
    ])

def db_date_range() -> tuple:
    """Return (min_date, max_date) of data in the database as UTC dates."""
    con = get_connection()
    row = con.execute("""
        SELECT 
            MIN(timezone('UTC', time))::DATE AS min_date,
            MAX(timezone('UTC', time))::DATE AS max_date
        FROM soundings
    """).fetchone()
    return row[0], row[1]

def launch_status_for_cycle(year: int, month: int, day: int, hour: int) -> pd.DataFrame:
    """
    For a specific synoptic cycle (year/month/day/hour UTC),
    return each station with whether it launched or not.
    All stations in the database are included — missing = did not launch.
    """
    con = get_connection()

    # All stations with metadata
    all_stations = con.execute("""
        SELECT DISTINCT
            s.station,
            COALESCE(m.display_name, s.station) AS display_name,
            MIN(s.lat) AS lat,
            MIN(s.lon) AS lon
        FROM soundings s
        LEFT JOIN station_meta m ON s.station = m.station_id
        GROUP BY s.station, m.display_name
    """).df()

    # Convert time to UTC before extracting components
    # TIMESTAMPTZ values need AT TIME ZONE 'UTC' to get UTC parts
    launched = con.execute(f"""
        SELECT DISTINCT station
        FROM soundings
        WHERE EXTRACT(year  FROM timezone('UTC', time)) = {year}
          AND EXTRACT(month FROM timezone('UTC', time)) = {month}
          AND EXTRACT(day   FROM timezone('UTC', time)) = {day}
          AND EXTRACT(hour  FROM timezone('UTC', time)) = {hour}
    """).df()

    all_stations["launched"] = all_stations["station"].isin(launched["station"])
    return all_stations
    con = get_connection()
    return con.execute("""
        SELECT
            s.station,
            COALESCE(m.display_name, s.station) AS display_name,
            MIN(s.lat) AS lat,
            MIN(s.lon) AS lon,
            MAX(s.time) AS last_launch
        FROM soundings s
        LEFT JOIN station_meta m ON s.station = m.station_id
        GROUP BY s.station, m.display_name
    """).df()

def main() -> None:
    print("Daily launch counts:")
    print(daily_launch_counts().head())
    print("\nStation summary:")
    print(station_summary().head())
    print("\nStations with largest change (60d before vs after):")
    print(launch_delta().head(10))
    print("\nStation reporting rate since 2025-01-01:")
    print(station_reporting().head())

if __name__ == "__main__":
    main()
