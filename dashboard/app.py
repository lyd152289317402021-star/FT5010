"""
FT5010 Strategy Dashboard (Redesigned)
=======================================
Two-tab dashboard:
  Tab 1 — 3H Demo Review : Shows the saved 3-hour live trading results
  Tab 2 — Live Trading   : Real-time monitoring with kill switch

Usage:
    cd dashboard
    python app.py
    # open http://127.0.0.1:8050
"""

import json, os, datetime, subprocess, sys
import dash
from dash import dcc, html, dash_table
from dash.dependencies import Input, Output, State
import plotly.graph_objects as go

# ---- Paths ----
BASE         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE   = os.path.join(BASE, "live_trading", "state.json")
DEMO_FILE    = os.path.join(BASE, "live_demo_evidence", "live_demo_summary.json")
ENGINE_DIR   = os.path.join(BASE, "live_trading")
FLAG_FILE    = os.path.join(ENGINE_DIR, "engine_starting.flag")   # created on Start, deleted by engine
PYTHON       = sys.executable
REFRESH_MS   = 1_000   # 1s refresh so runtime counter is smooth

# ---- OANDA credentials (hardcoded for dashboard direct-kill) ----
OANDA_TOKEN      = "042919c9d89351ae2d6141778420434d-cdd631c6497843cd515172ea976b029d"
OANDA_ACCOUNT_ID = "101-003-38799014-001"
OANDA_ENV        = "practice"

# ---- Design tokens ----
C = {
    "bg":       "#F7F8FA",
    "surface":  "#FFFFFF",
    "border":   "#EAECF0",
    "text":     "#111827",
    "muted":    "#6B7280",
    "accent":   "#0F62FE",
    "green":    "#0D9467",
    "green_bg": "#ECFDF5",
    "red":      "#DC2626",
    "red_bg":   "#FEF2F2",
    "amber":    "#D97706",
    "tab_active": "#0F62FE",
}

FONT      = "'IBM Plex Mono', 'Fira Code', 'Consolas', monospace"
FONT_SANS = "'DM Sans', 'Helvetica Neue', Arial, sans-serif"

CARD_STYLE = {
    "backgroundColor": C["surface"],
    "border": f"1px solid {C['border']}",
    "borderRadius": "10px",
    "padding": "20px 24px",
    "boxShadow": "0 1px 4px rgba(0,0,0,0.05)",
}

TABLE_STYLE_HEADER = {
    "backgroundColor": C["bg"],
    "fontWeight": "600",
    "fontSize": "11px",
    "color": C["muted"],
    "textTransform": "uppercase",
    "letterSpacing": "0.06em",
    "border": "none",
    "borderBottom": f"1px solid {C['border']}",
    "padding": "10px 14px",
    "fontFamily": FONT_SANS,
}
TABLE_STYLE_CELL = {
    "textAlign": "center",
    "padding": "9px 14px",
    "fontSize": "13px",
    "fontFamily": FONT,
    "color": C["text"],
    "border": "none",
    "borderBottom": f"1px solid {C['border']}",
    "backgroundColor": C["surface"],
}

PLOTLY_LAYOUT = dict(
    template="plotly_white",
    height=300,
    margin=dict(l=50, r=20, t=20, b=40),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family=FONT_SANS, size=12, color=C["muted"]),
    legend=dict(
        orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
        font=dict(size=11), bgcolor="rgba(0,0,0,0)", borderwidth=0,
    ),
)


# ---- Helpers ----

def read_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def read_demo():
    try:
        with open(DEMO_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def write_kill_request():
    state = read_state() or {}
    state["kill_requested"] = True
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def direct_close_all():
    """Close all OANDA positions directly without waiting for engine."""
    try:
        from oandapyV20 import API
        from oandapyV20.endpoints.positions import OpenPositions, PositionClose
        client = API(access_token=OANDA_TOKEN, environment=OANDA_ENV)
        r = OpenPositions(accountID=OANDA_ACCOUNT_ID)
        client.request(r)
        for p in r.response.get("positions", []):
            inst        = p["instrument"]
            long_units  = int(p["long"]["units"])
            short_units = int(p["short"]["units"])
            data = {}
            if long_units > 0:
                data["longUnits"] = "ALL"
            if short_units < 0:
                data["shortUnits"] = "ALL"
            if data:
                rc = PositionClose(accountID=OANDA_ACCOUNT_ID, instrument=inst, data=data)
                client.request(rc)
        print("Direct kill: all positions closed.")
    except Exception as e:
        print(f"Direct kill error: {e}")

def badge(text, color=C["accent"], bg=None):
    if bg is None:
        bg = color + "18"
    return html.Span(text, style={
        "display": "inline-block",
        "backgroundColor": bg,
        "color": color,
        "fontSize": "11px",
        "fontWeight": "600",
        "padding": "2px 10px",
        "borderRadius": "20px",
        "letterSpacing": "0.04em",
        "fontFamily": FONT_SANS,
    })

def kpi_card(label, value, sub=None, color=C["text"]):
    children = [
        html.Div(label, style={
            "fontSize": "11px", "fontWeight": "600",
            "color": C["muted"], "textTransform": "uppercase",
            "letterSpacing": "0.07em", "marginBottom": "8px",
            "fontFamily": FONT_SANS,
        }),
        html.Div(value, style={
            "fontSize": "26px", "fontWeight": "700",
            "color": color, "lineHeight": "1",
            "fontFamily": FONT, "letterSpacing": "-0.02em",
        }),
    ]
    if sub:
        children.append(html.Div(sub, style={
            "fontSize": "11px", "color": C["muted"],
            "marginTop": "5px", "fontFamily": FONT_SANS,
        }))
    return html.Div(children, style={
        **CARD_STYLE,
        "flex": "1", "minWidth": "130px", "margin": "4px",
    })

def section_label(text):
    return html.Div(text, style={
        "fontSize": "11px", "fontWeight": "700",
        "color": C["muted"], "textTransform": "uppercase",
        "letterSpacing": "0.08em", "fontFamily": FONT_SANS,
        "marginTop": "28px", "marginBottom": "12px",
    })

def event_row(time_str, msg, color=C["text"]):
    return html.Div(style={
        "display": "flex", "gap": "16px", "alignItems": "flex-start",
        "padding": "7px 0", "borderBottom": f"1px solid {C['border']}",
    }, children=[
        html.Span(time_str, style={
            "fontSize": "11px", "fontFamily": FONT,
            "color": C["muted"], "minWidth": "38px", "paddingTop": "1px",
        }),
        html.Span(msg, style={
            "fontSize": "12px", "fontFamily": FONT_SANS,
            "color": color, "lineHeight": "1.5",
        }),
    ])

def _fmt_runtime(start_t, end_t=None):
    if not start_t:
        return "—"
    try:
        start = datetime.datetime.fromisoformat(start_t)
        end   = datetime.datetime.fromisoformat(end_t) if end_t else datetime.datetime.utcnow()
        delta = end - start
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s   = divmod(rem, 60)
        if h > 0:
            return f"{h}h {m:02d}m"
        return f"{m}m {s:02d}s"
    except Exception:
        return "—"

def _make_empty_fig():
    layout = {**PLOTLY_LAYOUT}
    layout["xaxis"] = dict(visible=False)
    layout["yaxis"] = dict(visible=False)
    layout["annotations"] = [dict(
        text="Waiting for engine to start…",
        xref="paper", yref="paper", x=0.5, y=0.5,
        showarrow=False, font=dict(size=15, color=C["muted"]),
    )]
    return go.Figure().update_layout(**layout)


# ---- Startup cleanup ----
DEMO = read_demo()

# On dashboard start: clean up stale killed state and stale flag file
_st = read_state()
if _st and _st.get("killed"):
    try:
        os.remove(STATE_FILE)
    except FileNotFoundError:
        pass

# Also remove any leftover flag file from a previous crashed session
try:
    os.remove(FLAG_FILE)
except FileNotFoundError:
    pass


# ======================================================================
# Tab 1 — 3H Demo Review
# ======================================================================

def build_demo_tab():
    if DEMO is None:
        return html.Div("No demo data found.", style={
            "padding": "60px", "textAlign": "center",
            "color": C["muted"], "fontFamily": FONT_SANS,
        })

    nav_tl    = DEMO.get("nav_timeline", [])
    orders    = DEMO.get("orders", [])
    final_p   = DEMO.get("final_positions", [])
    rpnl      = DEMO.get("realized_pnl", {})
    total_rp  = DEMO.get("total_realized_pnl", 0)
    start_nav = DEMO.get("starting_nav", 100000)
    final_nav = start_nav + total_rp
    polls     = DEMO.get("polls", 0)

    # Max drawdown
    peak, max_dd = start_nav, 0
    for pt in nav_tl:
        n      = pt["nav"]
        peak   = max(peak, n)
        max_dd = min(max_dd, (n / peak - 1) * 100)

    # Win rate & profit factor
    wins   = sum(1 for v in rpnl.values() if v > 0)
    losses = sum(1 for v in rpnl.values() if v <= 0)
    total_trades  = len(rpnl)
    win_rate      = (wins / total_trades * 100) if total_trades else 0
    avg_gain      = sum(v for v in rpnl.values() if v > 0) / max(wins, 1)
    avg_loss      = abs(sum(v for v in rpnl.values() if v <= 0)) / max(losses, 1)
    profit_factor = avg_gain / avg_loss if avg_loss > 0 else float('inf')
    pf_str        = "∞" if profit_factor > 1e9 else f"{profit_factor:.1f}×"

    kpi_row = html.Div(style={
        "display": "flex", "gap": "0", "marginBottom": "20px", "flexWrap": "wrap",
    }, children=[
        kpi_card("Status",        "COMPLETED",          color=C["green"]),
        kpi_card("Final NAV",     f"${final_nav:,.0f}", sub=f"Started ${start_nav:,.0f}"),
        kpi_card("Session PnL",   f"${total_rp:+,.2f}", color=C["green"] if total_rp >= 0 else C["red"]),
        kpi_card("Max Drawdown",  f"{max_dd:.2f}%",     color=C["red"] if max_dd < -1 else C["text"]),
        kpi_card("Win Rate",      f"{win_rate:.0f}%",   sub=f"{wins}/{total_trades} pairs", color=C["green"]),
        kpi_card("Profit Factor", pf_str,               color=C["green"] if profit_factor >= 1 else C["red"]),
        kpi_card("Polls",         str(polls),           sub=f"{len(orders)} orders"),
    ])

    # Equity chart
    fig   = go.Figure()
    times = [f"2026-04-10T{pt['time']}" for pt in nav_tl]
    navs  = [pt["nav"] for pt in nav_tl]

    y_min   = min(navs + [start_nav])
    y_max   = max(navs + [start_nav])
    y_pad   = max((y_max - y_min) * 0.3, 50)
    y_range = [y_min - y_pad, y_max + y_pad]

    fig.add_trace(go.Scatter(
        x=times, y=[y_range[0]] * len(times),
        mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=times, y=navs, fill="tonexty",
        fillcolor="rgba(15,98,254,0.08)",
        line=dict(color=C["accent"], width=2), name="Strategy NAV",
    ))

    peak_v, dd_vals = start_nav, []
    for n in navs:
        peak_v = max(peak_v, n)
        dd_vals.append(peak_v)
    fig.add_trace(go.Scatter(
        x=times, y=dd_vals, mode="lines", name="Peak",
        line=dict(color=C["red"], width=1, dash="dot"), opacity=0.4,
    ))

    eurusd_start = next((float(o["close"]) for o in orders if o.get("pair") == "EURUSD"), None)
    if eurusd_start and len(navs) > 1:
        fig.add_trace(go.Scatter(
            x=[times[0], times[-1]], y=[start_nav, start_nav],
            mode="lines", name="EUR/USD B&H",
            line=dict(color=C["muted"], width=1.5, dash="dash"),
        ))

    fig.add_hline(y=start_nav, line_dash="dot", line_color=C["border"],
                  annotation_text="$100k", annotation_font_size=10,
                  annotation_font_color=C["muted"])
    fig.update_layout(**{**PLOTLY_LAYOUT, "yaxis": dict(
        showgrid=True, gridcolor=C["border"], zeroline=False,
        tickfont=dict(size=11), range=y_range,
        tickprefix="$", tickformat=",.0f",
    )})

    pos_table = dash_table.DataTable(
        columns=[
            {"name": "Pair",         "id": "pair"},
            {"name": "Side",         "id": "side"},
            {"name": "Units",        "id": "units"},
            {"name": "Realized PnL", "id": "rpnl"},
        ],
        data=[{
            "pair": p["pair"],
            "side": "Long" if int(p["units"].replace(",", "")) > 0 else "Short",
            "units": p["units"],
            "rpnl": f"${rpnl.get(p['pair'], 0):+,.2f}",
        } for p in final_p],
        style_cell=TABLE_STYLE_CELL,
        style_header=TABLE_STYLE_HEADER,
        style_data_conditional=[
            {"if": {"filter_query": "{side} = Long"},  "color": C["green"], "fontWeight": "600"},
            {"if": {"filter_query": "{side} = Short"}, "color": C["red"],   "fontWeight": "600"},
            {"if": {"filter_query": "{rpnl} contains '+'"}, "backgroundColor": C["green_bg"]},
        ],
        style_table={"borderRadius": "8px", "overflow": "hidden"},
    )

    equity_row = html.Div(style={"display": "flex", "gap": "16px", "flexWrap": "wrap"}, children=[
        html.Div(style={**CARD_STYLE, "flex": "2", "minWidth": "480px", "padding": "16px 20px"}, children=[
            section_label("Equity Curve (NAV)"),
            dcc.Graph(figure=fig, config={"displayModeBar": False}),
        ]),
        html.Div(style={**CARD_STYLE, "flex": "1", "minWidth": "280px", "padding": "16px 20px"}, children=[
            section_label("Final Positions"),
            pos_table,
            html.Div(f"Total Realized  ${total_rp:+,.2f}", style={
                "textAlign": "right", "marginTop": "12px",
                "fontSize": "13px", "fontFamily": FONT, "fontWeight": "700",
                "color": C["green"] if total_rp > 0 else C["red"],
            }),
        ]),
    ])

    trade_row = html.Div(style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginTop": "16px"}, children=[
        html.Div(style={**CARD_STYLE, "flex": "1", "minWidth": "340px", "padding": "16px 20px"}, children=[
            section_label("Orders Executed"),
            dash_table.DataTable(
                columns=[
                    {"name": "Pair",     "id": "pair"},
                    {"name": "Delta",    "id": "delta"},
                    {"name": "Forecast", "id": "forecast"},
                    {"name": "Close",    "id": "close"},
                ],
                data=orders,
                style_cell=TABLE_STYLE_CELL,
                style_header=TABLE_STYLE_HEADER,
                style_table={"borderRadius": "8px", "overflow": "hidden"},
            ),
        ]),
        html.Div(style={**CARD_STYLE, "flex": "1", "minWidth": "300px", "padding": "16px 20px"}, children=[
            section_label("Event Log"),
            html.Div([
                event_row("14:32", "Engine started — 7 pairs initialized"),
                event_row("14:32", "Entry signals triggered for all 7 instruments"),
                event_row("14:32", "8 market orders executed (7 entries + 1 AUDUSD rebalance)"),
                event_row("14:32–17:55", "168 polls, positions held (no-trade buffer active)"),
                event_row("17:57", "Kill switch triggered via dashboard"),
                event_row("17:57", f"All 7 positions closed  ·  PnL: ${total_rp:+,.2f}",
                          color=C["green"] if total_rp > 0 else C["red"]),
            ]),
        ]),
    ])

    return html.Div([kpi_row, equity_row, trade_row])


# ======================================================================
# Tab 2 — Live Trading
# ======================================================================

def build_live_tab():
    btn_base = {
        "fontSize": "12px", "fontWeight": "700", "fontFamily": FONT_SANS,
        "padding": "10px 24px", "border": "none", "borderRadius": "8px",
        "cursor": "pointer", "letterSpacing": "0.06em",
    }
    return html.Div([
        html.Div(style={
            **CARD_STYLE,
            "display": "flex", "alignItems": "center", "gap": "16px",
            "flexWrap": "wrap", "marginBottom": "16px",
        }, children=[
            html.Div(id="live-status-badge"),
            html.Div(style={"flex": "1"}),
            html.Button("▶  START ENGINE", id="start-btn", n_clicks=0, style={
                **btn_base, "backgroundColor": C["green"], "color": "white",
            }),
            html.Button("⏹  KILL SWITCH — CLOSE ALL", id="kill-btn", n_clicks=0, style={
                **btn_base, "backgroundColor": C["red"], "color": "white",
            }),
            html.Div(id="live-action-status", style={
                "fontSize": "11px", "color": C["muted"], "fontFamily": FONT_SANS,
            }),
        ]),

        html.Div(id="live-kpi", style={
            "display": "flex", "gap": "0", "marginBottom": "16px", "flexWrap": "wrap",
        }),

        html.Div(style={"display": "flex", "gap": "16px", "flexWrap": "wrap"}, children=[
            html.Div(style={**CARD_STYLE, "flex": "2", "minWidth": "480px", "padding": "16px 20px"}, children=[
                section_label("Equity Curve (NAV)"),
                dcc.Graph(id="live-equity-chart", config={"displayModeBar": False}),
            ]),
            html.Div(style={**CARD_STYLE, "flex": "1", "minWidth": "280px", "padding": "16px 20px"}, children=[
                section_label("Current Positions"),
                dash_table.DataTable(
                    id="live-positions",
                    columns=[
                        {"name": "Pair",        "id": "pair"},
                        {"name": "Side",        "id": "side"},
                        {"name": "Units",       "id": "units"},
                        {"name": "Forecast",    "id": "forecast"},
                        {"name": "Price",       "id": "price"},
                        {"name": "Unreal. PnL", "id": "upl"},
                    ],
                    style_cell=TABLE_STYLE_CELL,
                    style_header=TABLE_STYLE_HEADER,
                    style_data_conditional=[
                        {"if": {"filter_query": "{side} = Long"},  "color": C["green"], "fontWeight": "600"},
                        {"if": {"filter_query": "{side} = Short"}, "color": C["red"],   "fontWeight": "600"},
                    ],
                    style_table={"borderRadius": "8px", "overflow": "hidden"},
                ),
            ]),
        ]),

        section_label("Risk Metrics"),
        html.Div(id="live-risk-row", style={
            "display": "flex", "gap": "0", "marginBottom": "16px", "flexWrap": "wrap",
        }),

        html.Div(style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginTop": "4px"}, children=[
            html.Div(style={**CARD_STYLE, "flex": "1", "minWidth": "340px", "padding": "16px 20px"}, children=[
                section_label("Orders Executed"),
                dash_table.DataTable(
                    id="live-trades",
                    columns=[
                        {"name": "Time",     "id": "time"},
                        {"name": "Pair",     "id": "inst"},
                        {"name": "Delta",    "id": "delta"},
                        {"name": "Forecast", "id": "forecast"},
                        {"name": "Close",    "id": "close"},
                    ],
                    style_cell=TABLE_STYLE_CELL,
                    style_header=TABLE_STYLE_HEADER,
                    style_table={"borderRadius": "8px", "overflow": "hidden"},
                    page_size=10,
                ),
            ]),
            html.Div(style={**CARD_STYLE, "flex": "1", "minWidth": "300px", "padding": "16px 20px"}, children=[
                section_label("Event Log"),
                html.Div(id="live-event-log"),
            ]),
        ]),

        html.Div(id="live-status", style={
            "textAlign": "right", "color": C["muted"],
            "marginTop": "16px", "fontSize": "10px",
            "fontFamily": FONT, "letterSpacing": "0.02em",
        }),

        dcc.Interval(id="live-interval", interval=REFRESH_MS, n_intervals=0),
        dcc.Store(id="live-equity-history", data=[]),
    ])


# ======================================================================
# App Layout
# ======================================================================

GOOGLE_FONTS = html.Link(
    href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600;700&family=DM+Sans:wght@400;500;600;700&display=swap",
    rel="stylesheet"
)

app = dash.Dash(__name__)
app.title = "FT5010 Strategy Dashboard"

app.layout = html.Div(
    style={
        "fontFamily": FONT_SANS,
        "backgroundColor": C["bg"],
        "minHeight": "100vh",
        "padding": "32px 24px",
    },
    children=[
        GOOGLE_FONTS,

        html.Div(style={"maxWidth": "1280px", "margin": "0 auto 28px"}, children=[
            html.Div(style={"display": "flex", "alignItems": "flex-end", "gap": "14px",
                            "marginBottom": "4px"}, children=[
                html.H1("FT5010", style={
                    "fontSize": "28px", "fontWeight": "700",
                    "color": C["text"], "margin": "0",
                    "fontFamily": FONT, "letterSpacing": "-0.03em",
                }),
                html.Div("FX Trend Strategy Dashboard", style={
                    "fontSize": "16px", "color": C["muted"],
                    "fontFamily": FONT_SANS, "fontWeight": "400",
                    "marginBottom": "2px",
                }),
            ]),
            html.Div("Carver-style EWMAC Trend + Reversal + Vol Gate  ·  7 USD Majors", style={
                "fontSize": "12px", "color": C["muted"], "fontFamily": FONT_SANS,
            }),
        ]),

        html.Div(style={"maxWidth": "1280px", "margin": "0 auto"}, children=[
            dcc.Tabs(
                id="tabs", value="demo",
                style={"marginBottom": "20px"},
                colors={"border": C["border"], "primary": C["tab_active"], "background": C["bg"]},
                children=[
                    dcc.Tab(
                        label="3H Demo Review (Apr 10)", value="demo",
                        style={
                            "fontFamily": FONT_SANS, "fontSize": "13px",
                            "fontWeight": "500", "color": C["muted"],
                            "padding": "10px 20px", "backgroundColor": C["bg"],
                            "borderBottom": "2px solid transparent",
                        },
                        selected_style={
                            "fontFamily": FONT_SANS, "fontSize": "13px",
                            "fontWeight": "700", "color": C["text"],
                            "padding": "10px 20px", "backgroundColor": C["surface"],
                            "borderBottom": f"2px solid {C['tab_active']}",
                            "borderTop": "none", "borderLeft": "none", "borderRight": "none",
                        },
                    ),
                    dcc.Tab(
                        label="Live Trading", value="live",
                        style={
                            "fontFamily": FONT_SANS, "fontSize": "13px",
                            "fontWeight": "500", "color": C["muted"],
                            "padding": "10px 20px", "backgroundColor": C["bg"],
                            "borderBottom": "2px solid transparent",
                        },
                        selected_style={
                            "fontFamily": FONT_SANS, "fontSize": "13px",
                            "fontWeight": "700", "color": C["text"],
                            "padding": "10px 20px", "backgroundColor": C["surface"],
                            "borderBottom": f"2px solid {C['green']}",
                            "borderTop": "none", "borderLeft": "none", "borderRight": "none",
                        },
                    ),
                ],
            ),
            html.Div(id="tab-content"),
        ]),
    ]
)


# ======================================================================
# Callbacks
# ======================================================================

@app.callback(Output("tab-content", "children"), Input("tabs", "value"))
def render_tab(tab):
    if tab == "demo":
        return build_demo_tab()
    return build_live_tab()


@app.callback(
    [Output("live-kpi",            "children"),
     Output("live-equity-chart",   "figure"),
     Output("live-positions",      "data"),
     Output("live-risk-row",       "children"),
     Output("live-trades",         "data"),
     Output("live-event-log",      "children"),
     Output("live-status",         "children"),
     Output("live-equity-history", "data"),
     Output("live-status-badge",   "children")],
    [Input("live-interval", "n_intervals")],
    [State("live-equity-history", "data")],
)
def update_live(n, eq_hist):
    empty_fig = _make_empty_fig()

    state = read_state()
    if state is None:
        # Show STARTING only if the flag file exists (set by Start button)
        is_starting  = os.path.exists(FLAG_FILE)
        status_text  = "STARTING…" if is_starting else "OFFLINE"
        status_color = C["amber"]  if is_starting else C["muted"]
        msg = "Engine starting…" if is_starting else "Engine not started."
        return (
            [kpi_card("Status", status_text, color=status_color)],
            empty_fig, [], [], [],
            html.Div(msg, style={"fontSize": "12px", "color": C["muted"], "fontFamily": FONT_SANS}),
            "", eq_hist or [], badge(f"● {status_text}", status_color),
        )

    nav       = state.get("nav", 0)
    upl       = state.get("unrealized_pl", 0)
    dd        = state.get("drawdown_pct", 0)
    killed    = state.get("killed", False)
    pos       = state.get("positions", {})
    sigs      = state.get("signals", {})
    prices    = state.get("prices", {})
    trades    = state.get("trades", [])
    ts        = state.get("time", "")
    start_t   = state.get("start_time", "")
    start_nav = state.get("start_nav", nav)
    pnl       = nav - start_nav

    status_color = C["red"] if killed else C["green"]
    status_text  = "KILLED" if killed else "RUNNING"
    status_badge = badge(f"● {status_text}", status_color)

    kpis = [
        kpi_card("Status",         status_text,     color=status_color),
        kpi_card("NAV",            f"${nav:,.0f}"),
        kpi_card("Session PnL",    f"${pnl:+,.0f}", color=C["green"] if pnl >= 0 else C["red"]),
        kpi_card("Unrealized PnL", f"${upl:+,.0f}", color=C["green"] if upl >= 0 else C["red"]),
        kpi_card("Drawdown",       f"{dd:.2f}%",    color=C["red"] if dd < -2 else C["text"]),
    ]

    eq_hist = eq_hist or []
    if not killed:
        eq_hist.append({"time": ts, "nav": nav})
    eq_hist = eq_hist[-2000:]

    if eq_hist:
        live_navs  = [e["nav"]  for e in eq_hist]
        live_times = [e["time"] for e in eq_hist]
        y_min   = min(live_navs + [start_nav])
        y_max   = max(live_navs + [start_nav])
        y_pad   = max((y_max - y_min) * 0.3, 50)
        y_range = [y_min - y_pad, y_max + y_pad]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=live_times, y=[y_range[0]] * len(live_times),
            mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=live_times, y=live_navs,
            fill="tonexty", fillcolor="rgba(15,98,254,0.08)",
            line=dict(color=C["accent"], width=2), name="Strategy NAV",
        ))
        fig.add_trace(go.Scatter(
            x=[live_times[0], live_times[-1]], y=[start_nav, start_nav],
            mode="lines", name="Benchmark (Start NAV)",
            line=dict(color=C["muted"], width=1.5, dash="dash"),
        ))
        fig.add_hline(y=start_nav, line_dash="dot", line_color=C["border"],
                      annotation_text=f"${start_nav:,.0f}", annotation_font_size=10,
                      annotation_font_color=C["muted"])
        fig.update_layout(**{**PLOTLY_LAYOUT, "yaxis": dict(
            showgrid=True, gridcolor=C["border"], zeroline=False,
            tickfont=dict(size=11), range=y_range,
            tickprefix="$", tickformat=",.0f",
        )})
    else:
        fig = empty_fig

    pos_rows = []
    for inst in sorted(pos.keys()):
        u = pos[inst]
        if u == 0:
            continue
        pos_rows.append({
            "pair":     inst.replace("_", ""),
            "side":     "Long" if u > 0 else "Short",
            "units":    f"{u:,}",
            "forecast": f"{sigs.get(inst, 0):+.2f}",
            "price":    f"{prices.get(inst, 0):.5f}",
            "upl":      f"${upl / max(len(pos), 1):+,.0f}",
        })

    n_pos    = len([v for v in pos.values() if v != 0])
    n_orders = len(trades)
    risk = [
        kpi_card("Max Drawdown", f"{dd:.2f}%",  color=C["red"] if dd < -2 else C["text"]),
        kpi_card("Positions",    str(n_pos)),
        kpi_card("Runtime",      _fmt_runtime(start_t, ts if killed else None)),
        kpi_card("Total Orders", str(n_orders)),
    ]

    trade_rows = [{
        "time":     t.get("time", "")[:16].replace("T", " "),
        "inst":     t.get("inst", "").replace("_", ""),
        "delta":    f"{t.get('delta', 0):+,}",
        "close":    f"{t.get('close', 0):.5f}",
        "forecast": f"{t.get('forecast', 0):+.2f}",
    } for t in reversed(trades[-20:])]

    events = []
    if start_t:
        events.append(event_row(start_t[11:16], "Engine started"))
    if trades:
        events.append(event_row(trades[0].get("time", "")[11:16],
                                f"{n_orders} market orders executed"))
    if n_pos > 0:
        events.append(event_row("—", f"Holding {n_pos} positions (no-trade buffer active)"))
    if killed:
        events.append(event_row(ts[11:16], "Kill switch — all positions closed", color=C["green"]))

    return (
        kpis, fig, pos_rows, risk, trade_rows,
        html.Div(events),
        f"Last update: {ts}   ·   Started: {start_t}",
        eq_hist, status_badge,
    )


@app.callback(
    [Output("live-action-status",  "children"),
     Output("live-equity-history", "data",     allow_duplicate=True),
     Output("live-kpi",            "children", allow_duplicate=True),
     Output("live-positions",      "data",     allow_duplicate=True),
     Output("live-risk-row",       "children", allow_duplicate=True),
     Output("live-trades",         "data",     allow_duplicate=True),
     Output("live-event-log",      "children", allow_duplicate=True),
     Output("live-status",         "children", allow_duplicate=True),
     Output("live-status-badge",   "children", allow_duplicate=True)],
    [Input("start-btn", "n_clicks"), Input("kill-btn", "n_clicks")],
    prevent_initial_call=True,
)
def handle_buttons(start_clicks, kill_clicks):
    ctx = dash.callback_context
    if not ctx.triggered:
        return [""] + [dash.no_update] * 8
    btn = ctx.triggered[0]["prop_id"].split(".")[0]

    if btn == "start-btn" and start_clicks:
        # Clean up old state
        try:
            os.remove(STATE_FILE)
        except FileNotFoundError:
            pass
        # Create flag file so dashboard knows engine is starting
        with open(FLAG_FILE, "w") as f:
            f.write(datetime.datetime.utcnow().isoformat())
        # Launch engine
        subprocess.Popen(
            [PYTHON, "main.py"],
            cwd=ENGINE_DIR,
            stdout=open(os.path.join(ENGINE_DIR, "engine.log"), "w"),
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        return (
            f"Engine started at {datetime.datetime.utcnow().strftime('%H:%M:%S')} UTC",
            [],
            [kpi_card("Status", "STARTING…", color=C["amber"])],
            [], [], [],
            html.Div("Engine starting…", style={"fontSize": "12px", "color": C["muted"], "fontFamily": FONT_SANS}),
            "",
            badge("● STARTING…", C["amber"]),
        )

    if btn == "kill-btn" and kill_clicks:
        write_kill_request()
        direct_close_all()
        # Update state immediately so dashboard reflects killed status
        state = read_state() or {}
        state["killed"]        = True
        state["kill_requested"] = False
        state["reason"]        = "dashboard"
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        # Clean up flag file if still present
        try:
            os.remove(FLAG_FILE)
        except FileNotFoundError:
            pass
        return [f"Kill signal sent at {datetime.datetime.utcnow().strftime('%H:%M:%S')} UTC"] + [dash.no_update] * 8

    return [""] + [dash.no_update] * 8


if __name__ == "__main__":
    print("Dashboard: http://127.0.0.1:8050")
    app.run(debug=False, port=8050)
