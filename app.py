"""
app.py
------
IGRA Radiosonde Dashboard — Dash web application.
Run: python app.py  →  http://127.0.0.1:8050
"""

import pandas as pd
import plotly.graph_objects as go
import dash
from dash import dcc, html, Input, Output
import dash_bootstrap_components as dbc

import queries

# ── Constants ─────────────────────────────────────────────────────────────────

BUDGET_CUT_DATE = "2025-03-01"
BUDGET_CUT_TS   = pd.Timestamp(BUDGET_CUT_DATE, tz="UTC")

C = {
    "bg":      "#0D1117",
    "surface": "#161B22",
    "border":  "#30363D",
    "text":    "#E6EDF3",
    "muted":   "#8B949E",
    "accent":  "#58A6FF",
    "green":   "#3FB950",
    "red":     "#F85149",
    "yellow":  "#D29922",
}

# ── App ───────────────────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
app.title = "IGRA Radiosonde Monitor"
server = app.server

# ── Helpers ───────────────────────────────────────────────────────────────────

def graph_card(fig_id: str, height: int = 420) -> html.Div:
    return html.Div(
        dcc.Graph(
            id=fig_id,
            config={"displayModeBar": True, "displaylogo": False,
                    "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
            style={"height": height},
        ),
        className="graph-card",
    )

def kpi_card(label: str, value_id: str) -> html.Div:
    return html.Div([
        html.Div(id=value_id, className="kpi-value"),
        html.Div(label, className="kpi-label"),
    ], className="kpi-card")

def empty_fig(msg: str = "No data") -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=msg, xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font=dict(color=C["muted"], size=15),
    )
    fig.update_layout(
        paper_bgcolor=C["bg"],
        plot_bgcolor=C["surface"],
        margin=dict(l=20, r=20, t=20, b=20),
    )
    return fig

def apply_dark_theme(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        paper_bgcolor=C["bg"],
        plot_bgcolor=C["surface"],
        font=dict(color=C["text"], family="Inter, Segoe UI, sans-serif", size=13),
        hoverlabel=dict(bgcolor=C["surface"], bordercolor=C["border"],
                        font_color=C["text"]),
    )
    fig.update_xaxes(gridcolor=C["border"], zerolinecolor=C["border"])
    fig.update_yaxes(gridcolor=C["border"], zerolinecolor=C["border"])
    return fig

# ── Layout ────────────────────────────────────────────────────────────────────
# All tab content lives in the layout permanently.
# Tabs show/hide via the tab-panel display callback below.

app.layout = html.Div([

    # Header
    html.Div([
        html.Div([
            html.Span("📡", style={"fontSize": "1.4rem", "marginRight": "10px"}),
            html.Span("IGRA Radiosonde Monitor",
                      style={"fontSize": "1.2rem", "fontWeight": "700"}),
        ], style={"display": "flex", "alignItems": "center"}),
        html.Span("NCEI IGRA v2.2  •  2025 US Network Analysis",
                  style={"color": C["muted"], "fontSize": "0.8rem"}),
    ], style={
        "display": "flex", "justifyContent": "space-between", "alignItems": "center",
        "padding": "14px 28px",
        "background": C["surface"],
        "borderBottom": f"1px solid {C['border']}",
    }),

    html.Div([

        # Alert banner
        html.Div([
            "The data below shows the impact on the US radiosonde network since impacts occuring on ",
            html.Strong(BUDGET_CUT_DATE),
            ".",
        ], className="alert-banner"),

        # KPI row
        dbc.Row([
            dbc.Col(kpi_card("Stations Reporting",   "kpi-stations"), xs=6, md=3),
            dbc.Col(kpi_card("Avg Daily Launches",   "kpi-launches"), xs=6, md=3),
            dbc.Col(kpi_card("Launch Reduction",    "kpi-drop"),     xs=6, md=3),
            dbc.Col(kpi_card("Station No Longer Reporting",  "kpi-silent"),   xs=6, md=3),
        ], className="mb-4"),

        # Tabs
        dcc.Tabs(
            id="tabs", value="tab-timeseries",
            children=[
                dcc.Tab(label="📈 Time Series",    value="tab-timeseries",
                        className="nav-tab", selected_className="nav-tab--selected"),
                dcc.Tab(label="🗺  Station Map",   value="tab-map",
                        className="nav-tab", selected_className="nav-tab--selected"),
                dcc.Tab(label="✂  Budget Impact", value="tab-budget",
                        className="nav-tab", selected_className="nav-tab--selected"),
                dcc.Tab(label="📊 Rankings",       value="tab-rankings",
                        className="nav-tab", selected_className="nav-tab--selected"),
            ],
            style={"marginBottom": "20px"},
        ),

        # ── Tab panels — always in DOM, shown/hidden via callback ────────────

        # Time Series
        html.Div(id="panel-timeseries", children=[
            html.Div("Daily radiosonde launches across the US network",
                     className="section-header"),
            graph_card("fig-timeseries", height=460),
        ]),

        # Station Map
        html.Div(id="panel-map", children=[
            # Cycle selector
            html.Div("Select launch cycle", className="section-header"),
            dbc.Row([
                dbc.Col([
                    html.Label("Year", style={"fontSize": "0.78rem",
                               "color": C["muted"], "marginBottom": "4px",
                               "display": "block"}),
                    dcc.Dropdown(
                        id="map-year",
                        options=[],
                        value=None,
                        clearable=False,
                        style={"fontSize": "0.85rem"},
                    ),
                ], md=2),
                dbc.Col([
                    html.Label("Month", style={"fontSize": "0.78rem",
                               "color": C["muted"], "marginBottom": "4px",
                               "display": "block"}),
                    dcc.Dropdown(
                        id="map-month",
                        options=[
                            {"label": ["January","February","March","April",
                                       "May","June","July","August","September",
                                       "October","November","December"][m-1],
                             "value": m}
                            for m in range(1, 13)
                        ],
                        value=None,
                        clearable=False,
                        style={"fontSize": "0.85rem"},
                    ),
                ], md=2),
                dbc.Col([
                    html.Label("Day", style={"fontSize": "0.78rem",
                               "color": C["muted"], "marginBottom": "4px",
                               "display": "block"}),
                    dcc.Dropdown(
                        id="map-day",
                        options=[],
                        value=None,
                        clearable=False,
                        style={"fontSize": "0.85rem"},
                    ),
                ], md=2),
                dbc.Col([
                    html.Label("Cycle", style={"fontSize": "0.78rem",
                               "color": C["muted"], "marginBottom": "4px",
                               "display": "block"}),
                    dcc.Dropdown(
                        id="map-cycle",
                        options=[
                            {"label": "00Z", "value": 0},
                            {"label": "12Z", "value": 12},
                        ],
                        value=0,
                        clearable=False,
                        style={"fontSize": "0.85rem"},
                    ),
                ], md=2),
            ], className="mb-3"),

            # Maps
            dbc.Row([
                dbc.Col([
                    html.Div(id="map-status-header", className="section-header",
                             children="Launch Status"),
                    graph_card("fig-map-status", height=480),
                ], md=6),
                dbc.Col([
                    html.Div("Reporting Rate since 2025-01-01",
                             className="section-header"),
                    graph_card("fig-map-heat", height=480),
                ], md=6),
            ]),
        ], style={"display": "none"}),


        # Budget Impact
        html.Div(id="panel-budget", children=[
            html.Div(
                "60 days before vs 60 days after 2025-03-01 — equal windows",
                className="section-header",
            ),
            graph_card("fig-budget", height=600),
            html.Div(id="budget-summary",
                     style={"fontSize": "0.88rem", "color": C["muted"],
                            "marginTop": "10px"}),
        ], style={"display": "none"}),

        # Rankings
        html.Div(id="panel-rankings", children=[
            dbc.Row([
                dbc.Col([
                    html.Div("Top 10 — Most Reporting", className="section-header"),
                    html.Div(id="table-top10",
                             style={"fontSize": "0.82rem", "marginBottom": "28px"}),
                    html.Div("Bottom 10 — Least Reporting", className="section-header"),
                    html.Div(id="table-bottom10", style={"fontSize": "0.82rem"}),
                ], md=3),
                dbc.Col([
                    html.Div("All stations by reporting rate", className="section-header"),
                    graph_card("fig-rankings", height=600),
                ], md=9),
            ]),
        ], style={"display": "none"}),

    ], style={"padding": "24px 28px"}),

], style={"minHeight": "100vh", "background": C["bg"]})

# ── Callbacks ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("panel-timeseries", "style"),
    Output("panel-map",        "style"),
    Output("panel-budget",     "style"),
    Output("panel-rankings",   "style"),
    Input("tabs", "value"),
)
def show_panel(tab):
    """Show only the active tab panel, hide the rest."""
    panels = ["tab-timeseries", "tab-map", "tab-budget", "tab-rankings"]
    return [
        {"display": "block"} if tab == p else {"display": "none"}
        for p in panels
    ]


@app.callback(
    Output("kpi-stations", "children"),
    Output("kpi-launches", "children"),
    Output("kpi-drop",     "children"),
    Output("kpi-silent",   "children"),
    Input("tabs", "value"),
)
def update_kpis(_):
    try:
        daily   = queries.daily_launch_counts()
        summary = queries.station_summary()
        delta   = queries.launch_delta()

        active   = (summary["reporting_rate"] > 50).sum()
        daily["date"] = pd.to_datetime(daily["date"], utc=True)
        pre_avg  = daily[daily["date"] < BUDGET_CUT_TS]["total_launches"].mean()
        post_avg = daily[daily["date"] >= BUDGET_CUT_TS]["total_launches"].mean()
        drop_pct = ((post_avg - pre_avg) / pre_avg * 100) if pre_avg else 0
        grounded = (delta["post_cut"] == 0).sum()

        return (
            str(active),
            f"{pre_avg:.0f} → {post_avg:.0f}",
            f"{drop_pct:+.1f}%",
            str(grounded),
        )
    except Exception:
        return "—", "—", "—", "—"


@app.callback(
    Output("fig-timeseries", "figure"),
    Input("tabs", "value"),
)
def update_timeseries(_):
    try:
        df = queries.daily_launch_counts()
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df[df["date"] < df["date"].max()]
        df["rolling_7d"] = df["total_launches"].rolling(7, center=True, min_periods=1).mean()

        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=df["date"],
            y=df["total_launches"],
            mode="lines",
            name="Daily launches",
            line=dict(color=C["accent"], width=1),
            opacity=0.25,
            hovertemplate="%{x|%b %d, %Y}: <b>%{y}</b> launches<extra></extra>",
        ))

        fig.add_trace(go.Scatter(
            x=df["date"],
            y=df["rolling_7d"],
            mode="lines",
            name="7-day avg",
            line=dict(color=C["accent"], width=3),
            hovertemplate="%{x|%b %d, %Y}: <b>%{y:.0f}</b> (7d avg)<extra></extra>",
        ))

        xmin = df["date"].min()
        xmax = df["date"].max()

        if xmin <= BUDGET_CUT_TS <= xmax:
            fig.add_vline(
                x=BUDGET_CUT_TS.timestamp() * 1000,
                line=dict(color=C["red"], width=2, dash="dash"),
                annotation=dict(
                    text="⚠ Impact Date",
                    font=dict(color=C["red"], size=11),
                    bgcolor=C["surface"],
                    bordercolor=C["red"],
                    borderwidth=1,
                    yref="paper", y=0.98,
                ),
            )
            fig.add_vrect(
                x0=BUDGET_CUT_TS,
                x1=xmax,
                fillcolor=C["red"],
                opacity=0.05,
                layer="below",
                line_width=0,
            )

        fig.update_layout(
            title=dict(text="US Radiosonde Launches 2025 - Present",
                       font=dict(size=17, weight=700), x=0),
            yaxis=dict(
                title="Launches per day",
                titlefont=dict(color=C["accent"]),
                tickfont=dict(color=C["accent"]),
                gridcolor=C["border"],
            ),
            xaxis=dict(gridcolor=C["border"]),
            legend=dict(bgcolor=C["surface"], bordercolor=C["border"],
                        borderwidth=1, orientation="h", y=1.08, x=0),
            hovermode="x unified",
            hoverlabel=dict(bgcolor=C["surface"], bordercolor=C["border"],
                            font_color=C["text"]),
            paper_bgcolor=C["bg"],
            plot_bgcolor=C["surface"],
            font=dict(color=C["text"], family="Inter, Segoe UI, sans-serif", size=13),
            margin=dict(l=60, r=20, t=80, b=50),
        )
        return fig

    except Exception as e:
        return empty_fig(f"Error: {e}")


@app.callback(
    Output("map-year",  "options"),
    Output("map-year",  "value"),
    Output("map-month", "value"),
    Output("map-cycle", "value"),
    Input("tabs", "value"),
)
def init_map_dropdowns(tab):
    """Initialize dropdowns to most recent date in the database."""
    _, max_date = queries.db_date_range()
    years = queries.available_years()
    year_options = [{"label": str(y), "value": y} for y in years]
    return year_options, max_date.year, max_date.month, 0


@app.callback(
    Output("map-month", "options"),
    Input("map-year", "value"),
)
def update_month_options(year):
    """Cap months at max_date.month for the most recent year."""
    if not year:
        return []
    month_names = ["January","February","March","April","May","June",
                   "July","August","September","October","November","December"]
    _, max_date = queries.db_date_range()
    max_month = max_date.month if int(year) == max_date.year else 12
    return [{"label": month_names[m-1], "value": m}
            for m in range(1, max_month + 1)]


@app.callback(
    Output("map-day", "options"),
    Output("map-day", "value"),
    Input("map-year",  "value"),
    Input("map-month", "value"),
)
def update_day_options(year, month):
    """Cap days at max_date.day for the most recent year+month."""
    import calendar
    if not year or not month:
        return [], None
    _, max_date = queries.db_date_range()
    n_days = calendar.monthrange(int(year), int(month))[1]
    if int(year) == max_date.year and int(month) == max_date.month:
        n_days = min(n_days, max_date.day)
    options = [{"label": str(d), "value": d} for d in range(1, n_days + 1)]
    return options, n_days


@app.callback(
    Output("fig-map-status",   "figure"),
    Output("fig-map-heat",     "figure"),
    Output("map-status-header","children"),
    Input("tabs",      "value"),
    Input("map-year",  "value"),
    Input("map-month", "value"),
    Input("map-day",   "value"),
    Input("map-cycle", "value"),
)
def update_maps(tab, year, month, day, cycle):
    if tab != "tab-map":
        return empty_fig(), empty_fig(), "Launch Status"

    geo_layout = dict(
        scope="usa",
        projection_type="albers usa",
        showland=True,
        landcolor=C["surface"],
        showocean=True,
        oceancolor=C["bg"],
        showlakes=False,
        bgcolor=C["bg"],
    )

    try:
        # ── Status map with cycle selector ───────────────────────────────
        if all(v is not None for v in [year, month, day, cycle]):
            df = queries.launch_status_for_cycle(
                int(year), int(month), int(day), int(cycle)
            )
            cycle_label = f"{'00Z' if int(cycle) == 0 else '12Z'}"
            month_name = ["January","February","March","April","May","June",
                          "July","August","September","October","November",
                          "December"][int(month)-1]
            header = f"{month_name} {int(day)}, {int(year)} — {cycle_label}"
        else:
            df = queries.most_recent_launches()
            df["launched"] = True
            header = "Most Recent Launch Status"

        status_fig = go.Figure()
        for launched, label, color in [
            (True,  "Launched", C["green"]),
            (False, "Did Not Launch", C["red"]),
        ]:
            sub = df[df["launched"] == launched]
            if sub.empty:
                continue
            status_fig.add_trace(go.Scattergeo(
                lat=sub["lat"],
                lon=sub["lon"],
                mode="markers",
                marker=dict(size=8, color=color),
                name=label,
                text=sub["display_name"],
                hovertemplate="<b>%{text}</b><extra></extra>",
            ))

        status_fig.update_layout(
            geo=geo_layout,
            paper_bgcolor=C["bg"],
            font=dict(color=C["text"]),
            height=480,
            title=dict(text=header, font=dict(size=14, weight=700)),
            legend=dict(bgcolor=C["surface"], bordercolor=C["border"],
                        borderwidth=1, font=dict(color=C["text"])),
            margin=dict(l=0, r=0, t=50, b=0),
        )

        # ── Reporting rate heatmap ────────────────────────────────────────
        comp = queries.station_reporting()
        heat_fig = go.Figure(go.Scattergeo(
            lat=comp["lat"],
            lon=comp["lon"],
            mode="markers",
            marker=dict(
                size=8,
                color=comp["reporting_rate"],
                colorscale=[[0, C["red"]], [0.5, C["yellow"]], [1.0, C["green"]]],
                cmin=0, cmax=100,
                colorbar=dict(
                    title=dict(text="Reporting Rate %",
                               font=dict(color=C["text"])),
                    tickfont=dict(color=C["text"]),
                ),
            ),
            text=comp["display_name"],
            customdata=comp[["reporting_rate", "total_launches"]].values,
            hovertemplate=(
                "<b>%{text}</b><br>"
                "Reporting rate: %{customdata[0]:.1f}%<br>"
                "Total launches: %{customdata[1]}<extra></extra>"
            ),
        ))
        heat_fig.update_layout(
            geo=geo_layout,
            paper_bgcolor=C["bg"],
            font=dict(color=C["text"]),
            height=480,
            title=dict(text="Launch Reporting Rate since 2025-01-01",
                       font=dict(size=14, weight=700)),
            margin=dict(l=0, r=0, t=50, b=0),
        )

        return status_fig, heat_fig, header

    except Exception as e:
        return empty_fig(f"Error: {e}"), empty_fig(f"Error: {e}"), "Error"


@app.callback(
    Output("fig-budget",     "figure"),
    Output("budget-summary", "children"),
    Input("tabs", "value"),
)
def update_budget(tab):
    if tab != "tab-budget":
        return empty_fig(), ""
    try:
        delta = queries.launch_delta(window_days=60)
        delta = delta.sort_values("delta")
        colors = [C["red"] if d < 0 else C["green"] for d in delta["delta"]]

        fig = go.Figure(go.Bar(
            x=delta["delta"],
            y=delta["label"],
            orientation="h",
            marker_color=colors,
            customdata=delta[["pre_cut", "post_cut"]].values,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Pre-cut (60d): %{customdata[0]}<br>"
                "Post-cut (60d): %{customdata[1]}<br>"
                "Delta: %{x}<extra></extra>"
            ),
        ))
        fig.update_layout(
            title=dict(text="Launch Change: 60 Days Before vs After 2025-03-01",
                       font=dict(size=16, weight=700)),
            xaxis_title="Change in launches",
            xaxis=dict(zeroline=True, zerolinecolor=C["muted"], zerolinewidth=1,
                       gridcolor=C["border"]),
            yaxis=dict(gridcolor=C["border"]),
            paper_bgcolor=C["bg"],
            plot_bgcolor=C["surface"],
            font=dict(color=C["text"], family="Inter, Segoe UI, sans-serif", size=13),
            margin=dict(l=260, r=20, t=60, b=50),
            height=max(500, len(delta) * 22),
        )

        drops  = (delta["delta"] < 0).sum()
        silent = (delta["post_cut"] == 0).sum()
        gained = (delta["delta"] > 0).sum()
        summary = (
            f"60 days before vs 60 days after {BUDGET_CUT_DATE}: "
            f"{drops} stations reduced launches, {gained} increased, "
            f"{silent} went completely silent."
        )
        return fig, summary

    except Exception as e:
        return empty_fig(f"Error: {e}"), ""


def _make_station_table(df: pd.DataFrame, ascending: bool) -> html.Table:
    top = df.sort_values("reporting_rate", ascending=ascending).head(10)
    rows = []
    for _, r in top.iterrows():
        pct = r["reporting_rate"]
        color = C["green"] if pct >= 85 else C["yellow"] if pct >= 50 else C["red"]
        name = r.get("label", r.get("display_name", r["station"]))
        rows.append(html.Tr([
            html.Td(name,
                    style={"padding": "3px 8px", "fontSize": "0.75rem",
                           "color": C["muted"]}),
            html.Td(f"{pct:.1f}%",
                    style={"padding": "3px 8px", "color": color,
                           "fontWeight": "600", "textAlign": "right"}),
        ]))
    return html.Table(
        [html.Thead(html.Tr([
            html.Th("Station",
                    style={"padding": "4px 8px", "color": C["muted"],
                           "fontSize": "0.72rem"}),
            html.Th("Reporting Rate",
                    style={"padding": "4px 8px", "color": C["muted"],
                           "fontSize": "0.72rem", "textAlign": "right"}),
        ]))] + [html.Tbody(rows)],
        style={"width": "100%", "borderCollapse": "collapse"},
    )


@app.callback(
    Output("fig-rankings",   "figure"),
    Output("table-top10",    "children"),
    Output("table-bottom10", "children"),
    Input("tabs", "value"),
)
def update_rankings(tab):
    if tab != "tab-rankings":
        return empty_fig(), "", ""
    try:
        summary = queries.station_summary().sort_values("reporting_rate")
        colors = [
            C["red"] if p < 50 else C["yellow"] if p < 85 else C["green"]
            for p in summary["reporting_rate"]
        ]
        fig = go.Figure(go.Bar(
            x=summary["reporting_rate"],
            y=summary["label"],
            orientation="h",
            marker_color=colors,
            hovertemplate="<b>%{y}</b><br>Reporting rate: %{x:.1f}%<extra></extra>",
        ))
        fig.update_layout(
            title=dict(text="Station Reporting Rate Ranking",
                       font=dict(size=16, weight=700)),
            xaxis_title="Reporting Rate %",
            xaxis=dict(range=[0, 105], gridcolor=C["border"]),
            yaxis=dict(gridcolor=C["border"]),
            paper_bgcolor=C["bg"],
            plot_bgcolor=C["surface"],
            font=dict(color=C["text"], family="Inter, Segoe UI, sans-serif", size=13),
            margin=dict(l=260, r=20, t=60, b=50),
            height=max(500, len(summary) * 22),
        )
        top_table    = _make_station_table(summary, ascending=False)
        bottom_table = _make_station_table(summary, ascending=True)
        return fig, top_table, bottom_table

    except Exception as e:
        return empty_fig(f"Error: {e}"), "", ""


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=8050)
