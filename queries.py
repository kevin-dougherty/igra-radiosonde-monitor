import duckdb
import pandas as pd
from pathlib import Path

DB_PATH = Path("data/igra.duckdb")
IMPACT_DATE = "2025-03-01"
IMPACT_TS   = pd.Timestamp(IMPACT_DATE, tz="UTC")

def get_connection():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.execute("SET memory_limit='400MB'")
    con.execute("SET threads=2")
    return con

CYCLE_HOURS = [0, 6, 12, 18]

def _cycle_to_hour(cycle) -> int | None:
    """Normalize a cycle value ('00Z', '00', 0, None) to an int hour or None."""
    if cycle is None:
        return None
    if isinstance(cycle, str):
        cycle = cycle.upper().replace("Z", "")
    return int(cycle)

def daily_launch_counts(cycle=None) -> pd.DataFrame:
    """
    Daily launch counts. If cycle is None, all synoptic hours are combined.
    Pass a cycle (0, 6, 12, or 18 — or '00Z' etc.) to filter to one launch time.
    """
    con = get_connection()
    hour = _cycle_to_hour(cycle)
    hour_filter = "AND EXTRACT(hour FROM timezone('UTC', time)) = ?" if hour is not None else ""
    params = [hour] if hour is not None else []
    return con.execute(f"""
        SELECT
            DATE_TRUNC('day', timezone('UTC', time)) AS date,
            COUNT(*) AS total_launches,
            COUNT(DISTINCT station) AS stations_reporting
        FROM soundings
        WHERE time >= '2025-01-01'
        {hour_filter}
        GROUP BY 1
        ORDER BY 1
    """, params).df()

def station_names() -> pd.DataFrame:
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
            COUNT(*) AS total_launches,
            MIN(DATE_TRUNC('day', s.time)) AS first_launch,
            MAX(DATE_TRUNC('day', s.time)) AS last_launch,
            (COUNT(*) /
             NULLIF(DATEDIFF('day', MIN(s.time), MAX(s.time)) * 2.0, 0)) * 100 AS reporting_rate
        FROM soundings s
        LEFT JOIN station_meta m ON s.station = m.station_id
        GROUP BY s.station, m.display_name
        ORDER BY total_launches DESC
    """).df()

def station_reporting() -> pd.DataFrame:
    """
    Per-station reporting rate, graded against each station's own actual
    cycle roster rather than a flat 2/day assumption. Some stations
    (e.g. Barrow, AK) genuinely run all four synoptic cycles as routine
    operations, not just 00Z/12Z — scoring them against a 2/day target
    made healthy stations read as high as ~200%. A cycle counts toward a
    station's roster if it accounts for at least 10% of the days elapsed
    (a real, recurring cycle) rather than occasional/rare activity.
    """
    con = get_connection()
    return con.execute("""
        WITH days AS (
            SELECT GREATEST(DATEDIFF('day', '2025-01-01', CURRENT_DATE), 1) AS n
        ),
        cycle_counts AS (
            SELECT
                s.station,
                COUNT(*) FILTER (WHERE EXTRACT(hour FROM timezone('UTC', s.time)) = 0)  AS n00,
                COUNT(*) FILTER (WHERE EXTRACT(hour FROM timezone('UTC', s.time)) = 6)  AS n06,
                COUNT(*) FILTER (WHERE EXTRACT(hour FROM timezone('UTC', s.time)) = 12) AS n12,
                COUNT(*) FILTER (WHERE EXTRACT(hour FROM timezone('UTC', s.time)) = 18) AS n18,
                COUNT(*) AS total_launches,
                MIN(s.lat) AS lat,
                MIN(s.lon) AS lon,
                MAX(DATE_TRUNC('day', s.time)) AS last_launch
            FROM soundings s
            WHERE s.time >= '2025-01-01'
            GROUP BY s.station
        ),
        roster AS (
            SELECT
                c.*,
                (CASE WHEN n00 >= 0.10 * d.n THEN 1 ELSE 0 END +
                 CASE WHEN n06 >= 0.10 * d.n THEN 1 ELSE 0 END +
                 CASE WHEN n12 >= 0.10 * d.n THEN 1 ELSE 0 END +
                 CASE WHEN n18 >= 0.10 * d.n THEN 1 ELSE 0 END) AS roster_size,
                d.n AS days_elapsed
            FROM cycle_counts c
            CROSS JOIN days d
        )
        SELECT
            r.station,
            COALESCE(m.display_name, r.station) AS display_name,
            COALESCE(m.display_name, r.station) || ' (' || r.station || ')' AS label,
            r.lat,
            r.lon,
            r.total_launches,
            r.last_launch,
            (r.total_launches /
             NULLIF(GREATEST(r.roster_size, 1) * r.days_elapsed, 0)) * 100 AS reporting_rate
        FROM roster r
        LEFT JOIN station_meta m ON r.station = m.station_id
        ORDER BY reporting_rate DESC
    """).df()

def launch_delta(window_days: int = 60) -> pd.DataFrame:
    con = get_connection()
    return con.execute(f"""
        SELECT *, post_cut - pre_cut AS delta
        FROM (
            SELECT
                s.station,
                COALESCE(m.display_name, s.station) AS display_name,
                COALESCE(m.display_name, s.station) || ' (' || s.station || ')' AS label,
                COUNT(*) FILTER (
                    WHERE s.time >= '2025-03-01'::DATE - INTERVAL '{window_days} days'
                      AND s.time <  '2025-03-01'
                ) AS pre_cut,
                COUNT(*) FILTER (
                    WHERE s.time >= '2025-03-01'
                      AND s.time <  '2025-03-01'::DATE + INTERVAL '{window_days} days'
                ) AS post_cut
            FROM soundings s
            LEFT JOIN station_meta m ON s.station = m.station_id
            GROUP BY s.station, m.display_name
        ) sub
        ORDER BY delta
    """).df()

def most_recent_launches() -> pd.DataFrame:
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

def available_years() -> list[int]:
    con = get_connection()
    return sorted([
        int(r[0]) for r in
        con.execute("""
            SELECT DISTINCT EXTRACT(year FROM timezone('UTC', time))
            FROM soundings ORDER BY 1
        """).fetchall()
    ])

def db_date_range() -> tuple:
    con = get_connection()
    row = con.execute("""
        SELECT
            MIN(timezone('UTC', time))::DATE AS min_date,
            MAX(timezone('UTC', time))::DATE AS max_date
        FROM soundings
    """).fetchone()
    return row[0], row[1]

def launch_status_for_cycle(year: int, month: int, day: int, hour: int) -> pd.DataFrame:
    con = get_connection()

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

def station_reporting_by_cycle_window(cycle=None, window_days=None) -> pd.DataFrame:
    """
    Per-station reporting rate for a given synoptic cycle and rolling window,
    for use in the Reporting Rate map. Every station in station_meta is
    included even if it has zero matching launches (shows as 0%), and lat/lon
    are pulled from the station's full launch history so it still plots even
    when the selected cycle/window has no data for it.
    """
    con = get_connection()
    hour = _cycle_to_hour(cycle)
    expected_per_day = 1.0 if hour is not None else 2.0
    hour_filter = "AND EXTRACT(hour FROM timezone('UTC', s.time)) = ?" if hour is not None else ""
    params = [hour] if hour is not None else []
    start_expr = "'2025-01-01'" if window_days is None else f"CURRENT_DATE - INTERVAL '{window_days} days'"

    query = f"""
        SELECT
            m.station_id AS station,
            m.display_name AS display_name,
            m.display_name || ' (' || m.station_id || ')' AS label,
            loc.lat, loc.lon,
            COALESCE(c.total_launches, 0) AS total_launches,
            (COALESCE(c.total_launches, 0) /
             NULLIF(GREATEST(DATEDIFF('day', {start_expr}, CURRENT_DATE), 1) * {expected_per_day}, 0)
            ) * 100 AS reporting_rate
        FROM station_meta m
        LEFT JOIN (
            SELECT station, MIN(lat) AS lat, MIN(lon) AS lon
            FROM soundings GROUP BY station
        ) loc ON loc.station = m.station_id
        LEFT JOIN (
            SELECT station, COUNT(*) AS total_launches
            FROM soundings s
            WHERE timezone('UTC', s.time) >= {start_expr}
            {hour_filter}
            GROUP BY station
        ) c ON c.station = m.station_id
        ORDER BY reporting_rate DESC
    """
    return con.execute(query, params).df()

def network_reporting_by_cycle_window() -> pd.DataFrame:
    """
    Network-wide reporting rate for each synoptic cycle (00Z/06Z/12Z/18Z),
    across rolling windows: 3 month, 6 month, 1 year, and the full monitoring
    period (since 2025-01-01). Each cycle has one possible launch per station
    per day, so the denominator is (n_stations * days_in_window).
    """
    con = get_connection()
    windows = [("3 Month", 90), ("6 Month", 180), ("1 Year", 365), ("Total", None)]

    n_stations = con.execute("SELECT COUNT(*) FROM station_meta").fetchone()[0] or 1

    rows = []
    for cycle in CYCLE_HOURS:
        for label, days in windows:
            start_expr = "'2025-01-01'" if days is None else f"CURRENT_DATE - INTERVAL '{days} days'"
            launches, expected_days = con.execute(f"""
                SELECT
                    COUNT(*) FILTER (
                        WHERE timezone('UTC', time) >= {start_expr}
                          AND EXTRACT(hour FROM timezone('UTC', time)) = ?
                    ) AS launches,
                    GREATEST(DATEDIFF('day', {start_expr}, CURRENT_DATE), 1) AS expected_days
                FROM soundings
            """, [cycle]).fetchone()
            rate = (launches / (n_stations * expected_days)) * 100
            rows.append({
                "cycle": f"{cycle:02d}Z",
                "window": label,
                "reporting_rate": rate,
                "launches": launches,
            })

    return pd.DataFrame(rows)

def launch_delta_by_cycle() -> pd.DataFrame:
    """
    Network-wide avg daily launches before/after the impact date, split by
    synoptic cycle. Uses the full pre/post period mean — same methodology
    as the top-level KPI cards, just filtered per cycle.
    """
    rows = []
    for cycle in CYCLE_HOURS:
        df = daily_launch_counts(cycle=cycle)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        pre_avg  = df[df["date"] < IMPACT_TS]["total_launches"].mean()
        post_avg = df[df["date"] >= IMPACT_TS]["total_launches"].mean()
        pre_avg  = pre_avg  if pd.notna(pre_avg)  else 0
        post_avg = post_avg if pd.notna(post_avg) else 0
        pct = ((post_avg - pre_avg) / pre_avg * 100) if pre_avg else None
        rows.append({
            "cycle": f"{cycle:02d}Z",
            "pre_avg": pre_avg,
            "post_avg": post_avg,
            "pct_change": pct,
        })
    return pd.DataFrame(rows)

def main() -> None:
    print("Daily launch counts:")
    print(daily_launch_counts().head())
    print("\nStation summary:")
    print(station_summary().head())
    print("\nLaunch delta:")
    print(launch_delta().head(10))
    print("\nStation reporting:")
    print(station_reporting().head())

if __name__ == "__main__":
    main()
