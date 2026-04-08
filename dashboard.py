"""
FT5010 Forex Momentum Strategy — Interactive Dashboard
=======================================================
Usage:  streamlit run dashboard.py
"""

import warnings
warnings.filterwarnings("ignore")

import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from copy import deepcopy

try:
    import yfinance as yf
except ImportError:
    st.error("Please install yfinance: `pip install yfinance`")
    st.stop()

# ─────────────────────────────────────────────────────────────────
#  PAGE CONFIG
# ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="FT5010 Forex Strategy Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────
PAIRS = [
    "EURUSD=X", "GBPUSD=X", "JPY=X", "CHF=X",
    "AUDUSD=X", "CAD=X", "NZDUSD=X",
]
PAIR_NAMES = {
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD",
    "JPY=X":    "USD/JPY", "CHF=X":    "USD/CHF",
    "AUDUSD=X": "AUD/USD", "CAD=X":    "USD/CAD",
    "NZDUSD=X": "NZD/USD",
}
USD_BASE_PAIRS = {"JPY=X", "CHF=X", "CAD=X"}
HOURS_PER_YEAR_FX = 24 * 5 * 52  # 6240


# ═════════════════════════════════════════════════════════════════
#  SECTION 1 — DATA CLASSES
# ═════════════════════════════════════════════════════════════════
@dataclass
class Trade:
    pair:        str
    direction:   int
    entry_time:  pd.Timestamp
    entry_price: float
    exit_time:   pd.Timestamp
    exit_price:  float
    units:       float
    pnl_pips:    float = 0.0
    pnl_usd:     float = 0.0
    exit_reason: str = ""


@dataclass
class Position:
    pair:          str
    direction:     int
    entry_time:    pd.Timestamp
    entry_price:   float
    units:         float
    stop_loss:     float
    atr_at_entry:  float
    trailing_stop: float = 0.0


@dataclass
class Portfolio:
    cash:         float
    positions:    Dict[str, Optional[Position]] = field(default_factory=dict)
    trade_log:    List[Trade] = field(default_factory=list)
    equity_curve: List[Tuple] = field(default_factory=list)

    def equity(self, prices: Dict[str, float]) -> float:
        total = self.cash
        for pair, pos in self.positions.items():
            if pos is not None and pair in prices:
                total += _unrealised_pnl(pos, prices[pair])
        return total


# ═════════════════════════════════════════════════════════════════
#  SECTION 2 — HELPER FUNCTIONS (from notebook)
# ═════════════════════════════════════════════════════════════════
def _pip_size(pair: str) -> float:
    return 0.01 if "JPY" in pair.upper() or pair in ("JPY=X",) else 0.0001


def _spread_cost(pair: str, direction: int, spread_pips: float) -> float:
    return direction * spread_pips * _pip_size(pair)


def _pnl_usd(pair, pnl_pips, pip, units, exit_price):
    raw_pnl = pnl_pips * pip * units
    if pair in USD_BASE_PAIRS and exit_price > 0:
        return raw_pnl / exit_price
    return raw_pnl


def _unrealised_pnl(pos: Position, current_price: float) -> float:
    pip = _pip_size(pos.pair)
    pnl_pips = (current_price - pos.entry_price) * pos.direction / pip
    return _pnl_usd(pos.pair, pnl_pips, pip, pos.units, current_price)


def _compute_units(equity, risk_frac, atr, stop_pips, pip, leverage, price, pair):
    risk_usd = equity * risk_frac
    stop_dist = stop_pips * pip
    if stop_dist == 0:
        return 0
    units = risk_usd / stop_dist
    if pair in USD_BASE_PAIRS:
        units = units * price
    max_units = (equity * leverage) / price
    return min(units, max_units)


# ═════════════════════════════════════════════════════════════════
#  SECTION 3 — DATA FETCHING
# ═════════════════════════════════════════════════════════════════
@st.cache_data(ttl=3600, show_spinner="Fetching forex data...")
def fetch_forex_data(pairs: tuple, start: str, end: str, interval: str) -> Dict[str, pd.DataFrame]:
    data = {}
    for pair in pairs:
        try:
            df = yf.download(pair, start=start, end=end, interval=interval, progress=False)
            if df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            for col in ["Open", "High", "Low", "Close"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df.dropna(subset=["Close"], inplace=True)
            if not df.empty:
                data[pair] = df
        except Exception:
            continue
    return data


# ═════════════════════════════════════════════════════════════════
#  SECTION 4 — INDICATORS
# ═════════════════════════════════════════════════════════════════
def compute_rsi(series, window=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=window - 1, min_periods=window).mean()
    avg_loss = loss.ewm(com=window - 1, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_atr(df, window=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=window, min_periods=window).mean()


def compute_adx(df, window=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    tr = pd.concat([
        high - low, (high - close.shift()).abs(), (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_s = tr.ewm(com=window - 1, min_periods=window).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(com=window - 1, min_periods=window).mean() / atr_s
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(com=window - 1, min_periods=window).mean() / atr_s
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.ewm(com=window - 1, min_periods=window).mean()


def add_indicators(df, fast, slow, trend_win, rsi_window, adx_window,
                   adx_threshold, rsi_long_thresh, rsi_short_thresh,
                   rsi_exit_long, rsi_exit_short,
                   session_start, session_end):
    df = df.copy()
    close = df["Close"]

    df["ema_fast"]  = close.ewm(span=fast, min_periods=fast).mean()
    df["ema_slow"]  = close.ewm(span=slow, min_periods=slow).mean()
    df["ema_trend"] = close.ewm(span=trend_win, min_periods=trend_win).mean()
    df["rsi"]       = compute_rsi(close, rsi_window)
    df["atr"]       = compute_atr(df)
    df["adx"]       = compute_adx(df, adx_window)

    df["regime"]    = np.where(df["ema_fast"] > df["ema_slow"], 1, -1)
    df["crossover"] = df["regime"].diff().fillna(0).ne(0)

    if hasattr(df.index, "hour"):
        df["in_session"] = (df.index.hour >= session_start) & (df.index.hour < session_end)
    else:
        df["in_session"] = True

    trend_long  = close > df["ema_trend"]
    trend_short = close < df["ema_trend"]
    rsi_long    = df["rsi"] >= rsi_long_thresh
    rsi_short   = df["rsi"] <= rsi_short_thresh
    adx_ok      = df["adx"] >= adx_threshold
    session_ok  = df["in_session"]

    long_ok  = trend_long  & rsi_long  & adx_ok & session_ok
    short_ok = trend_short & rsi_short & adx_ok & session_ok

    df["signal_update"] = np.nan
    df.loc[df["crossover"] & (df["regime"] ==  1) & long_ok,  "signal_update"] =  1.0
    df.loc[df["crossover"] & (df["regime"] == -1) & short_ok, "signal_update"] = -1.0
    unconfirmed = df["crossover"] & ~(
        ((df["regime"] == 1) & long_ok) | ((df["regime"] == -1) & short_ok)
    )
    df.loc[unconfirmed, "signal_update"] = 0.0

    df.loc[df["signal_update"].isna() & (df["rsi"] >= rsi_exit_long),  "signal_update"] = 0.0
    df.loc[df["signal_update"].isna() & (df["rsi"] <= rsi_exit_short), "signal_update"] = 0.0

    df["signal"] = df["signal_update"].ffill().fillna(0).astype(int)
    df.dropna(subset=["ema_fast", "ema_slow", "ema_trend", "rsi", "atr", "adx"], inplace=True)
    return df


# ═════════════════════════════════════════════════════════════════
#  SECTION 5 — BACKTEST ENGINE
# ═════════════════════════════════════════════════════════════════
def run_backtest(data, initial_capital, risk_per_trade, spread_pips,
                 atr_stop_mult, trail_atr_mult, leverage):
    portfolio = Portfolio(cash=initial_capital, positions={p: None for p in data})
    all_times = sorted(set(ts for df in data.values() for ts in df.index))

    for ts in all_times:
        current_prices = {
            pair: df.at[ts, "Close"]
            for pair, df in data.items() if ts in df.index
        }
        current_equity = portfolio.equity(current_prices)

        for pair, df in data.items():
            if ts not in df.index:
                continue
            row = df.loc[ts]
            price, atr = row["Close"], row["atr"]
            pip = _pip_size(pair)
            atr_pips = atr / pip
            stop_pips = atr_pips * atr_stop_mult
            new_signal = int(row["signal"])

            bar_low, bar_high = row["Low"], row["High"]
            pos = portfolio.positions[pair]

            # Stop-loss check
            if pos is not None:
                hit_stop = (
                    (pos.direction == 1 and bar_low <= pos.stop_loss) or
                    (pos.direction == -1 and bar_high >= pos.stop_loss)
                )
                if hit_stop:
                    exit_price = pos.stop_loss
                    pnl_p = (exit_price - pos.entry_price) * pos.direction / pip
                    pnl_u = _pnl_usd(pair, pnl_p, pip, pos.units, exit_price)
                    portfolio.cash += pnl_u
                    portfolio.trade_log.append(Trade(
                        pair=pair, direction=pos.direction,
                        entry_time=pos.entry_time, entry_price=pos.entry_price,
                        exit_time=ts, exit_price=exit_price,
                        units=pos.units, pnl_pips=pnl_p, pnl_usd=pnl_u,
                        exit_reason="stop_loss",
                    ))
                    portfolio.positions[pair] = None
                    pos = None

            # Trailing stop update
            pos = portfolio.positions[pair]
            if pos is not None:
                trail_dist = atr * trail_atr_mult
                if pos.direction == 1:
                    new_trail = price - trail_dist
                    if new_trail > pos.trailing_stop:
                        pos.trailing_stop = new_trail
                        if pos.trailing_stop > pos.stop_loss:
                            pos.stop_loss = pos.trailing_stop
                elif pos.direction == -1:
                    new_trail = price + trail_dist
                    if pos.trailing_stop == 0 or new_trail < pos.trailing_stop:
                        pos.trailing_stop = new_trail
                        if pos.trailing_stop < pos.stop_loss:
                            pos.stop_loss = pos.trailing_stop

            # Signal change
            current_dir = pos.direction if pos else 0
            if new_signal != current_dir:
                if pos is not None:
                    exit_price = price - _spread_cost(pair, pos.direction, spread_pips)
                    pnl_p = (exit_price - pos.entry_price) * pos.direction / pip
                    pnl_u = _pnl_usd(pair, pnl_p, pip, pos.units, exit_price)
                    portfolio.cash += pnl_u
                    portfolio.trade_log.append(Trade(
                        pair=pair, direction=pos.direction,
                        entry_time=pos.entry_time, entry_price=pos.entry_price,
                        exit_time=ts, exit_price=exit_price,
                        units=pos.units, pnl_pips=pnl_p, pnl_usd=pnl_u,
                        exit_reason="signal",
                    ))
                    portfolio.positions[pair] = None

                if new_signal != 0 and current_equity > 0:
                    entry_price = price + _spread_cost(pair, new_signal, spread_pips)
                    stop_loss = entry_price - new_signal * stop_pips * pip
                    units = _compute_units(
                        current_equity, risk_per_trade, atr,
                        stop_pips, pip, leverage, entry_price, pair,
                    )
                    if units > 0:
                        init_trail = entry_price - new_signal * atr * trail_atr_mult
                        portfolio.positions[pair] = Position(
                            pair=pair, direction=new_signal,
                            entry_time=ts, entry_price=entry_price,
                            units=units, stop_loss=stop_loss,
                            atr_at_entry=atr, trailing_stop=init_trail,
                        )

        portfolio.equity_curve.append((ts, portfolio.equity(current_prices)))

    # Close remaining positions at last price
    for pair, pos in portfolio.positions.items():
        if pos is not None and pair in data:
            df = data[pair]
            last_ts = df.index[-1]
            price = df.at[last_ts, "Close"]
            pip = _pip_size(pair)
            pnl_p = (price - pos.entry_price) * pos.direction / pip
            pnl_u = _pnl_usd(pair, pnl_p, pip, pos.units, price)
            portfolio.cash += pnl_u
            portfolio.trade_log.append(Trade(
                pair=pair, direction=pos.direction,
                entry_time=pos.entry_time, entry_price=pos.entry_price,
                exit_time=last_ts, exit_price=price,
                units=pos.units, pnl_pips=pnl_p, pnl_usd=pnl_u,
                exit_reason="end_of_data",
            ))
            portfolio.positions[pair] = None

    return portfolio


# ═════════════════════════════════════════════════════════════════
#  SECTION 6 — METRICS
# ═════════════════════════════════════════════════════════════════
def compute_metrics(portfolio: Portfolio, label: str, risk_free_rate: float) -> pd.Series:
    trades = portfolio.trade_log
    eq_df = pd.DataFrame(portfolio.equity_curve, columns=["time", "equity"])
    eq_df.set_index("time", inplace=True)
    eq_df.sort_index(inplace=True)

    if eq_df.empty or len(trades) == 0:
        return pd.Series({"Error": "No data"})

    equity = eq_df["equity"]
    returns = equity.pct_change().dropna()
    n_hours = len(equity)
    n_years = n_hours / HOURS_PER_YEAR_FX if n_hours > 0 else 1

    total_return = (equity.iloc[-1] / equity.iloc[0]) - 1
    ann_return = (1 + total_return) ** (1 / max(n_years, 0.1)) - 1
    ann_vol = returns.std() * np.sqrt(HOURS_PER_YEAR_FX)
    sharpe = (ann_return - risk_free_rate) / ann_vol if ann_vol != 0 else 0

    roll_max = equity.cummax()
    drawdown = (equity - roll_max) / roll_max
    max_dd = drawdown.min()
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    pnls = [t.pnl_usd for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls) if pnls else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.inf

    return pd.Series({
        "Label":               label,
        "Total Return (%)":    round(total_return * 100, 2),
        "Ann. Return (%)":     round(ann_return * 100, 2),
        "Ann. Volatility (%)": round(ann_vol * 100, 2),
        "Sharpe Ratio":        round(sharpe, 3),
        "Max Drawdown (%)":    round(max_dd * 100, 2),
        "Calmar Ratio":        round(calmar, 3),
        "Win Rate (%)":        round(win_rate * 100, 2),
        "Profit Factor":       round(profit_factor, 3),
        "Avg Win (USD)":       round(np.mean(wins), 2) if wins else 0,
        "Avg Loss (USD)":      round(np.mean(losses), 2) if losses else 0,
        "Total Trades":        len(trades),
        "Avg PnL / Trade":     round(np.mean(pnls), 2) if pnls else 0,
        "Final Equity (USD)":  round(equity.iloc[-1], 2),
    })


def per_pair_metrics(portfolio: Portfolio) -> pd.DataFrame:
    rows = []
    for pair in PAIRS:
        pair_trades = [t for t in portfolio.trade_log if t.pair == pair]
        if not pair_trades:
            continue
        pnls = [t.pnl_usd for t in pair_trades]
        wins = [p for p in pnls if p > 0]
        rows.append({
            "Pair":            PAIR_NAMES.get(pair, pair),
            "Trades":          len(pnls),
            "Win Rate (%)":    round(len(wins) / len(pnls) * 100, 1),
            "Total PnL (USD)": round(sum(pnls), 2),
            "Avg PnL (USD)":   round(np.mean(pnls), 2),
            "Best (USD)":      round(max(pnls), 2),
            "Worst (USD)":     round(min(pnls), 2),
        })
    return pd.DataFrame(rows).sort_values("Total PnL (USD)", ascending=False)


# ═════════════════════════════════════════════════════════════════
#  SECTION 7 — BENCHMARK
# ═════════════════════════════════════════════════════════════════
def benchmark_buy_and_hold(price_series, pair, initial_capital):
    s = price_series.dropna().astype(float)
    if pair in USD_BASE_PAIRS:
        s = 1.0 / s
    return (s / s.iloc[0] * initial_capital).rename("equity")


def benchmark_risk_free(index, initial_capital, annual_rate):
    n = len(index)
    r_hourly = (1 + annual_rate) ** (1 / HOURS_PER_YEAR_FX) - 1
    growth = (1 + r_hourly) ** np.arange(n)
    return pd.Series(initial_capital * growth, index=index, name="equity")


# ═════════════════════════════════════════════════════════════════
#  SECTION 8 — PLOTLY CHARTS
# ═════════════════════════════════════════════════════════════════
def plot_equity_and_drawdown(portfolio, title):
    eq_df = pd.DataFrame(portfolio.equity_curve, columns=["time", "equity"])
    eq_df.set_index("time", inplace=True)
    equity = eq_df["equity"]
    roll_max = equity.cummax()
    drawdown = (equity - roll_max) / roll_max * 100

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.7, 0.3], vertical_spacing=0.06)

    fig.add_trace(go.Scatter(
        x=equity.index, y=equity.values, name="Equity",
        line=dict(color="#00d97e", width=1.5),
    ), row=1, col=1)
    fig.add_hline(y=equity.iloc[0], line_dash="dash", line_color="gray",
                  opacity=0.5, row=1, col=1)

    fig.add_trace(go.Scatter(
        x=drawdown.index, y=drawdown.values, name="Drawdown",
        fill="tozeroy", line=dict(color="#e63757", width=1),
        fillcolor="rgba(230,55,87,0.3)",
    ), row=2, col=1)

    fig.update_layout(
        title=title, height=450, template="plotly_dark",
        legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center"),
        margin=dict(l=50, r=20, t=60, b=30),
    )
    fig.update_yaxes(title_text="Equity (USD)", row=1, col=1)
    fig.update_yaxes(title_text="Drawdown %", row=2, col=1)
    return fig


def plot_trade_pnl(portfolio, title):
    pnls = [t.pnl_usd for t in portfolio.trade_log]
    if not pnls:
        return go.Figure()
    cum_pnl = np.cumsum(pnls)
    colors = ["#00d97e" if p > 0 else "#e63757" for p in pnls]

    fig = make_subplots(rows=1, cols=2, subplot_titles=["Per-Trade PnL", "Cumulative PnL"])

    fig.add_trace(go.Bar(
        x=list(range(len(pnls))), y=pnls,
        marker_color=colors, name="Trade PnL", showlegend=False,
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=list(range(len(cum_pnl))), y=cum_pnl.tolist(),
        line=dict(color="#39afd1", width=2), name="Cumulative", showlegend=False,
        fill="tozeroy", fillcolor="rgba(57,175,209,0.15)",
    ), row=1, col=2)

    fig.update_layout(
        title=title, height=350, template="plotly_dark",
        margin=dict(l=50, r=20, t=60, b=30),
    )
    return fig


def plot_pair_bar(portfolio, title):
    df = per_pair_metrics(portfolio)
    if df.empty:
        return go.Figure()
    colors = ["#00d97e" if p > 0 else "#e63757" for p in df["Total PnL (USD)"]]
    fig = go.Figure(go.Bar(
        x=df["Pair"], y=df["Total PnL (USD)"], marker_color=colors,
    ))
    fig.update_layout(
        title=title, height=320, template="plotly_dark",
        yaxis_title="PnL (USD)", margin=dict(l=50, r=20, t=50, b=30),
    )
    return fig


def plot_benchmark_comparison(strat_eq, bh_eq, rf_eq, bm_pair_name):
    norm_s = strat_eq / strat_eq.iloc[0] * 100
    norm_b = bh_eq / bh_eq.iloc[0] * 100
    norm_r = rf_eq / rf_eq.iloc[0] * 100

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=norm_s.index, y=norm_s.values, name="Strategy",
                             line=dict(color="#00d97e", width=2)))
    fig.add_trace(go.Scatter(x=norm_b.index, y=norm_b.values, name=f"{bm_pair_name} B&H",
                             line=dict(color="#f6c343", width=1.5, dash="dash")))
    fig.add_trace(go.Scatter(x=norm_r.index, y=norm_r.values, name="Risk-Free",
                             line=dict(color="#39afd1", width=1.5, dash="dot")))
    fig.add_hline(y=100, line_dash="dash", line_color="gray", opacity=0.4)
    fig.update_layout(
        title="Strategy vs Benchmarks (normalised to 100)",
        height=400, template="plotly_dark",
        yaxis_title="Normalised Value",
        legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center"),
        margin=dict(l=50, r=20, t=60, b=30),
    )
    return fig


def plot_monthly_returns(portfolio):
    eq_df = pd.DataFrame(portfolio.equity_curve, columns=["time", "equity"])
    eq_df.set_index("time", inplace=True)
    eq_df.sort_index(inplace=True)
    monthly = eq_df["equity"].resample("ME").last().pct_change().dropna()
    if monthly.empty:
        return go.Figure()

    colors = ["#00d97e" if r > 0 else "#e63757" for r in monthly.values]
    labels = [d.strftime("%Y-%m") for d in monthly.index]

    fig = go.Figure(go.Bar(x=labels, y=(monthly.values * 100), marker_color=colors))
    fig.update_layout(
        title="Monthly Returns (%)", height=300, template="plotly_dark",
        yaxis_title="Return %", margin=dict(l=50, r=20, t=50, b=30),
    )
    return fig


def plot_exit_reason_pie(portfolio):
    reasons = [t.exit_reason for t in portfolio.trade_log]
    if not reasons:
        return go.Figure()
    reason_counts = pd.Series(reasons).value_counts()
    fig = go.Figure(go.Pie(
        labels=reason_counts.index, values=reason_counts.values,
        marker=dict(colors=["#00d97e", "#e63757", "#f6c343", "#39afd1"]),
        hole=0.4,
    ))
    fig.update_layout(
        title="Exit Reasons", height=300, template="plotly_dark",
        margin=dict(l=20, r=20, t=50, b=20),
    )
    return fig


# ═════════════════════════════════════════════════════════════════
#  SECTION 9 — SIDEBAR
# ═════════════════════════════════════════════════════════════════
st.sidebar.title("Strategy Parameters")

with st.sidebar.expander("Data Settings", expanded=True):
    lookback_days = st.slider("Lookback (days)", 180, 730, 700, step=30)
    train_ratio = st.slider("Train Ratio", 0.5, 0.9, 0.7, step=0.05)
    selected_pairs = st.multiselect("Pairs", PAIRS, default=PAIRS,
                                    format_func=lambda x: PAIR_NAMES.get(x, x))

with st.sidebar.expander("EMA & Trend", expanded=True):
    fast_window  = st.number_input("Fast EMA", 5, 100, 20)
    slow_window  = st.number_input("Slow EMA", 10, 300, 50)
    trend_window = st.number_input("Trend EMA", 50, 500, 200)

with st.sidebar.expander("RSI & ADX Filters"):
    rsi_window       = st.number_input("RSI Window", 5, 50, 14)
    rsi_long_thresh  = st.number_input("RSI Long Entry", 30, 70, 55)
    rsi_short_thresh = st.number_input("RSI Short Entry", 30, 70, 45)
    rsi_exit_long    = st.number_input("RSI Exit Long", 60, 90, 75)
    rsi_exit_short   = st.number_input("RSI Exit Short", 10, 40, 25)
    adx_window       = st.number_input("ADX Window", 5, 50, 14)
    adx_threshold    = st.number_input("ADX Threshold", 10, 50, 25)

with st.sidebar.expander("Session Filter"):
    session_start = st.number_input("Session Start (UTC)", 0, 23, 7)
    session_end   = st.number_input("Session End (UTC)", 0, 23, 17)

with st.sidebar.expander("Risk Management"):
    initial_capital = st.number_input("Initial Capital ($)", 1000, 1_000_000, 10_000, step=1000)
    risk_per_trade  = st.slider("Risk per Trade (%)", 0.1, 5.0, 1.0, step=0.1) / 100
    spread_pips     = st.number_input("Spread (pips)", 0.0, 10.0, 2.0, step=0.5)
    atr_stop_mult   = st.slider("ATR Stop Multiplier", 1.0, 5.0, 2.5, step=0.1)
    trail_atr_mult  = st.slider("Trail ATR Multiplier", 1.0, 5.0, 2.0, step=0.1)
    leverage        = st.number_input("Leverage", 1, 200, 50, step=10)
    risk_free_rate  = st.slider("Risk-Free Rate (%)", 0.0, 10.0, 4.0, step=0.5) / 100

run_btn = st.sidebar.button("Run Backtest", type="primary", use_container_width=True)


# ═════════════════════════════════════════════════════════════════
#  SECTION 10 — MAIN DASHBOARD
# ═════════════════════════════════════════════════════════════════
st.title("FT5010 Forex Momentum Strategy Dashboard")
st.caption("Dual EMA Crossover + RSI/ADX Filter | Event-Based Backtester")

if not run_btn and "portfolio_train" not in st.session_state:
    st.info("Configure parameters in the sidebar and click **Run Backtest** to begin.")
    st.stop()

if run_btn:
    if not selected_pairs:
        st.error("Please select at least one pair.")
        st.stop()

    today = datetime.today()
    start_date = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    # — Fetch data —
    raw_data = fetch_forex_data(tuple(selected_pairs), start_date, end_date, "1h")
    if not raw_data:
        st.error("No data fetched. Try different pairs or date range.")
        st.stop()

    # — Indicator params —
    ind_params = dict(
        fast=fast_window, slow=slow_window, trend_win=trend_window,
        rsi_window=rsi_window, adx_window=adx_window,
        adx_threshold=adx_threshold,
        rsi_long_thresh=rsi_long_thresh, rsi_short_thresh=rsi_short_thresh,
        rsi_exit_long=rsi_exit_long, rsi_exit_short=rsi_exit_short,
        session_start=session_start, session_end=session_end,
    )

    # — Split & compute indicators (compute on full, then split) —
    train_data, test_data = {}, {}
    for pair, df in raw_data.items():
        full = add_indicators(df, **ind_params)
        split_idx = int(len(full) * train_ratio)
        train_data[pair] = full.iloc[:split_idx]
        test_data[pair] = full.iloc[split_idx:]

    bt_params = dict(
        initial_capital=initial_capital, risk_per_trade=risk_per_trade,
        spread_pips=spread_pips, atr_stop_mult=atr_stop_mult,
        trail_atr_mult=trail_atr_mult, leverage=leverage,
    )

    with st.spinner("Running backtest..."):
        portfolio_train = run_backtest(train_data, **bt_params)
        portfolio_test  = run_backtest(test_data, **bt_params)

    st.session_state["portfolio_train"] = portfolio_train
    st.session_state["portfolio_test"]  = portfolio_test
    st.session_state["test_data"]       = test_data
    st.session_state["risk_free_rate"]  = risk_free_rate
    st.session_state["initial_capital"] = initial_capital

# — Retrieve from session —
portfolio_train = st.session_state["portfolio_train"]
portfolio_test  = st.session_state["portfolio_test"]
test_data       = st.session_state["test_data"]
rfr             = st.session_state["risk_free_rate"]
cap             = st.session_state["initial_capital"]

metrics_train = compute_metrics(portfolio_train, "Train", rfr)
metrics_test  = compute_metrics(portfolio_test, "Test", rfr)

# ─────────────────────────────────────────────────────────────────
#  TAB LAYOUT
# ─────────────────────────────────────────────────────────────────
tab_overview, tab_train, tab_test, tab_benchmark, tab_trades = st.tabs([
    "Overview", "Train Set", "Test Set", "Benchmark", "Trade Log",
])

# ── TAB: Overview ────────────────────────────────────────────────
with tab_overview:
    st.subheader("Key Metrics — Test Set")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total Return", f"{metrics_test.get('Total Return (%)', 0)}%")
    c2.metric("Sharpe Ratio", f"{metrics_test.get('Sharpe Ratio', 0)}")
    c3.metric("Max Drawdown", f"{metrics_test.get('Max Drawdown (%)', 0)}%")
    c4.metric("Win Rate", f"{metrics_test.get('Win Rate (%)', 0)}%")
    c5.metric("Profit Factor", f"{metrics_test.get('Profit Factor', 0)}")
    c6.metric("Total Trades", f"{int(metrics_test.get('Total Trades', 0))}")

    st.divider()

    st.subheader("Train vs Test Comparison")
    comp_df = pd.DataFrame([metrics_train, metrics_test]).set_index("Label").T
    st.dataframe(comp_df, use_container_width=True)

    col_l, col_r = st.columns(2)
    with col_l:
        st.plotly_chart(plot_monthly_returns(portfolio_test), use_container_width=True)
    with col_r:
        st.plotly_chart(plot_exit_reason_pie(portfolio_test), use_container_width=True)

# ── TAB: Train Set ──────────────────────────────────────────────
with tab_train:
    st.plotly_chart(plot_equity_and_drawdown(portfolio_train, "Train — Equity & Drawdown"),
                    use_container_width=True)
    st.plotly_chart(plot_trade_pnl(portfolio_train, "Train — Trade PnL"),
                    use_container_width=True)
    st.plotly_chart(plot_pair_bar(portfolio_train, "Train — PnL by Pair"),
                    use_container_width=True)

    st.subheader("Per-Pair Breakdown")
    st.dataframe(per_pair_metrics(portfolio_train), use_container_width=True, hide_index=True)

# ── TAB: Test Set ───────────────────────────────────────────────
with tab_test:
    st.plotly_chart(plot_equity_and_drawdown(portfolio_test, "Test — Equity & Drawdown"),
                    use_container_width=True)
    st.plotly_chart(plot_trade_pnl(portfolio_test, "Test — Trade PnL"),
                    use_container_width=True)
    st.plotly_chart(plot_pair_bar(portfolio_test, "Test — PnL by Pair"),
                    use_container_width=True)

    st.subheader("Per-Pair Breakdown")
    st.dataframe(per_pair_metrics(portfolio_test), use_container_width=True, hide_index=True)

# ── TAB: Benchmark ──────────────────────────────────────────────
with tab_benchmark:
    eq_df_test = pd.DataFrame(portfolio_test.equity_curve, columns=["time", "equity"])
    eq_df_test.set_index("time", inplace=True)
    strat_eq = eq_df_test["equity"].sort_index()

    bm_pair = "EURUSD=X" if "EURUSD=X" in test_data else next(iter(test_data))
    bm_prices = test_data[bm_pair]["Close"].reindex(strat_eq.index).ffill().dropna()
    bh_eq = benchmark_buy_and_hold(bm_prices, bm_pair, cap)

    common_idx = strat_eq.index.intersection(bh_eq.index)
    strat_eq = strat_eq.reindex(common_idx).ffill()
    bh_eq = bh_eq.reindex(common_idx).ffill()
    rf_eq = benchmark_risk_free(common_idx, cap, rfr)

    bm_name = PAIR_NAMES.get(bm_pair, bm_pair)
    st.plotly_chart(
        plot_benchmark_comparison(strat_eq, bh_eq, rf_eq, bm_name),
        use_container_width=True,
    )

    st.subheader("Benchmark Metrics")
    from functools import partial

    def _bm_metrics(equity, label):
        returns = equity.pct_change().dropna()
        n = len(equity)
        n_y = n / HOURS_PER_YEAR_FX if n > 0 else 1
        total_ret = (equity.iloc[-1] / equity.iloc[0]) - 1
        ann_ret = (1 + total_ret) ** (1 / max(n_y, 0.1)) - 1
        ann_vol = returns.std() * np.sqrt(HOURS_PER_YEAR_FX)
        sharpe = (ann_ret - rfr) / ann_vol if ann_vol != 0 else 0
        roll_max = equity.cummax()
        max_dd = ((equity - roll_max) / roll_max).min()
        calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
        return pd.Series({
            "Label": label,
            "Total Return (%)": round(total_ret * 100, 2),
            "Ann. Return (%)": round(ann_ret * 100, 2),
            "Sharpe Ratio": round(sharpe, 3),
            "Max Drawdown (%)": round(max_dd * 100, 2),
            "Calmar Ratio": round(calmar, 3),
            "Final Equity (USD)": round(equity.iloc[-1], 2),
        })

    bm_comp = pd.DataFrame([
        _bm_metrics(strat_eq, "Strategy"),
        _bm_metrics(bh_eq, f"{bm_name} B&H"),
        _bm_metrics(rf_eq, f"Risk-Free ({rfr*100:.1f}%)"),
    ]).set_index("Label").T
    st.dataframe(bm_comp, use_container_width=True)

# ── TAB: Trade Log ──────────────────────────────────────────────
with tab_trades:
    all_trades = portfolio_test.trade_log
    if all_trades:
        rows = []
        for t in all_trades:
            rows.append({
                "Pair": PAIR_NAMES.get(t.pair, t.pair),
                "Direction": "Long" if t.direction == 1 else "Short",
                "Entry Time": t.entry_time,
                "Entry Price": round(t.entry_price, 5),
                "Exit Time": t.exit_time,
                "Exit Price": round(t.exit_price, 5),
                "PnL (pips)": round(t.pnl_pips, 1),
                "PnL (USD)": round(t.pnl_usd, 2),
                "Exit Reason": t.exit_reason,
            })
        trade_df = pd.DataFrame(rows)

        # Filters
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            pair_filter = st.multiselect("Filter by Pair", trade_df["Pair"].unique(),
                                         default=trade_df["Pair"].unique().tolist())
        with col_f2:
            dir_filter = st.multiselect("Filter by Direction", ["Long", "Short"],
                                        default=["Long", "Short"])
        with col_f3:
            reason_filter = st.multiselect("Filter by Exit Reason",
                                           trade_df["Exit Reason"].unique().tolist(),
                                           default=trade_df["Exit Reason"].unique().tolist())

        mask = (
            trade_df["Pair"].isin(pair_filter) &
            trade_df["Direction"].isin(dir_filter) &
            trade_df["Exit Reason"].isin(reason_filter)
        )
        st.dataframe(trade_df[mask], use_container_width=True, hide_index=True)
        st.caption(f"Showing {mask.sum()} of {len(trade_df)} trades")
    else:
        st.warning("No trades in test set.")
