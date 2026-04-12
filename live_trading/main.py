"""
FT5010 Live Trading Engine
==========================
Carver-style H1 EWMAC trend + reversal + vol gate on USD FX majors.

Usage:
    cd live_trading
    python main.py

The engine:
  1. Pulls latest H1 candles from OANDA for each pair in the universe.
  2. Fits the strategy on a rolling training window (all available history).
  3. Generates a combined forecast per pair.
  4. Converts forecast → target units via Carver vol targeting.
  5. Compares target vs current OANDA position → places market orders if
     the change exceeds the no-trade buffer.
  6. Writes state.json for the dashboard to read.
  7. Sleeps POLL_INTERVAL_SEC and repeats.

Kill switch: if drawdown > MAX_DRAWDOWN_PCT, all positions are closed and
the engine halts.
"""

import json
import time
import datetime
import traceback
import numpy as np
import pandas as pd

import config as cfg
from strategy import SingleFXCarverStrategy
from execution import (
    get_client, fetch_candles, get_account_summary,
    get_open_positions, get_position, place_market_order,
)
from risk import check_drawdown, check_leverage, kill_switch


# ---- Helpers ----

ACCOUNT_CCY = "USD"
BARS_PER_YEAR = 24 * 5 * 52  # H1
VOL_SCALE = float(np.sqrt(BARS_PER_YEAR))


def pair_label(inst):
    """'EUR_USD' -> 'EURUSD'."""
    return inst.replace("_", "")


def quote_to_account(inst, close):
    """PnL conversion factor. X_USD -> 1, USD_Y -> 1/close."""
    base, quote = inst.split("_")
    if quote == ACCOUNT_CCY:
        return 1.0
    if base == ACCOUNT_CCY:
        return 1.0 / close
    return 1.0  # fallback


def target_position(forecast, price_vol, close, inst, capital):
    """Carver vol-targeted position sizing."""
    n_inst = len(cfg.UNIVERSE)
    inst_cap = capital / n_inst
    ann_cash_vol_target = inst_cap * cfg.VOL_TARGET_ANN
    per_bar_target = ann_cash_vol_target / VOL_SCALE
    qta = quote_to_account(inst, close)
    ivv = price_vol * qta
    if not np.isfinite(ivv) or ivv <= 0:
        return 0.0
    vs = per_bar_target / ivv
    # leverage cap
    max_notional = cfg.MAX_LEVERAGE * inst_cap
    max_units = max_notional / max(close * qta, 1e-9)
    target = vs * forecast / cfg.TARGET_ABS_FORECAST
    target = float(np.clip(target, -max_units, max_units))
    return round(target / cfg.LOT_SIZE) * cfg.LOT_SIZE


def should_trade(target, current):
    """Apply no-trade buffer."""
    if current == 0:
        return target != 0
    denom = max(abs(current), cfg.LOT_SIZE)
    return abs(target - current) / denom > cfg.NO_TRADE_BUFFER


def write_state(state):
    """Write state dict to JSON for the dashboard."""
    with open(cfg.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)
    # Remove the starting flag so dashboard knows engine is live
    flag = os.path.join(os.path.dirname(cfg.STATE_FILE), "engine_starting.flag")
    try:
        os.remove(flag)
    except FileNotFoundError:
        pass


# ---- Main loop ----

def run():
    print("=" * 60)
    print("  FT5010 Live Trading Engine")
    print(f"  Account : {cfg.OANDA_ACCOUNT_ID}")
    print(f"  Universe: {cfg.UNIVERSE}")
    print(f"  Poll    : every {cfg.POLL_INTERVAL_SEC}s")
    print("=" * 60)

    client = get_client()
    peak_nav = 0.0
    trade_log = []
    start_time = datetime.datetime.utcnow().isoformat()

    while True:
        try:
            ts = datetime.datetime.utcnow()
            print(f"\n[{ts.strftime('%H:%M:%S')}] ---- poll ----")

            # 1. Account snapshot
            acct = get_account_summary(client)
            nav = acct["nav"]
            peak_nav = max(peak_nav, nav)
            print(f"  NAV={nav:,.2f}  PL={acct['unrealizedPL']:+,.2f}"
                  f"  trades={acct['openTradeCount']}")

            # 2. Drawdown kill switch
            if check_drawdown(nav, peak_nav):
                kill_switch(f"drawdown {((nav/peak_nav)-1)*100:.1f}% > limit")
                post_acct = get_account_summary(client)
                try:
                    with open(cfg.STATE_FILE) as f:
                        full = json.load(f)
                except Exception:
                    full = {}
                full.update({"killed": True, "reason": "drawdown",
                             "nav": post_acct["nav"],
                             "balance": post_acct["balance"],
                             "unrealized_pl": post_acct["unrealizedPL"],
                             "time": ts.isoformat()})
                write_state(full)
                break

            # 3. Check for external kill signal from dashboard
            try:
                with open(cfg.STATE_FILE) as f:
                    st = json.load(f)
                if st.get("kill_requested"):
                    kill_switch("dashboard kill button")
                    # re-query account AFTER closing positions for actual NAV
                    post_acct = get_account_summary(client)
                    st.update({"killed": True, "reason": "dashboard",
                               "nav": post_acct["nav"],
                               "balance": post_acct["balance"],
                               "unrealized_pl": post_acct["unrealizedPL"],
                               "time": ts.isoformat()})
                    write_state(st)
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                pass

            # 4. For each instrument: fetch data, compute signal, maybe trade
            positions_snapshot = {}
            signals_snapshot = {}
            prices_snapshot = {}
            qta_snapshot = {}

            for inst in cfg.UNIVERSE:
                label = pair_label(inst)
                try:
                    df = fetch_candles(inst, client=client)
                    close = float(df["close"].iloc[-1])
                    prices_snapshot[inst] = close
                    qta_snapshot[inst] = quote_to_account(inst, close)

                    # Fit on all available data (expanding window).
                    model = SingleFXCarverStrategy(
                        fast_slow_pairs=cfg.FAST_SLOW_PAIRS,
                        trend_group_weight=cfg.TREND_WEIGHT,
                        reversal_lookback=cfg.REVERSAL_LOOKBACK,
                        reversal_group_weight=cfg.REVERSAL_WEIGHT,
                        vol_gate_enabled=cfg.VOL_GATE_ENABLED,
                        vol_gate_cutoff=cfg.VOL_GATE_CUTOFF,
                        vol_gate_scale=cfg.VOL_GATE_SCALE,
                        price_vol_span=cfg.PRICE_VOL_SPAN,
                        lot_size=cfg.LOT_SIZE,
                        no_trade_buffer=cfg.NO_TRADE_BUFFER,
                    )
                    model.fit(df)

                    forecast = model.latest_forecast(df)
                    pvol = model.latest_price_vol(df)
                    signals_snapshot[inst] = forecast

                    # Position sizing
                    target = target_position(forecast, pvol, close, inst, nav)
                    current = get_position(inst, client)
                    positions_snapshot[inst] = current

                    print(f"  {label:8} close={close:.5f}  fc={forecast:+.2f}"
                          f"  tgt={target:,.0f}  cur={current:,.0f}", end="")

                    if should_trade(target, current):
                        delta = int(target - current)
                        resp = place_market_order(inst, delta, client)
                        trade_log.append({
                            "time": ts.isoformat(), "inst": inst,
                            "delta": delta, "target": target, "current": current,
                            "forecast": forecast, "close": close,
                        })
                        print(f"  -> ORDER {delta:+,}")
                    else:
                        print("  (hold)")

                except Exception as e:
                    print(f"  {label:8} ERROR: {e}")

            # 5. Leverage check
            if check_leverage(nav, positions_snapshot, prices_snapshot, qta_snapshot):
                print("  [WARN] leverage exceeded, closing overshoot")
                kill_switch("leverage_breach")
                state["killed"] = True
                state["reason"] = "leverage"
                write_state(state)
                break

            # 6. Write state for dashboard
            state = {
                "time": ts.isoformat(),
                "start_time": start_time,
                "nav": nav,
                "balance": acct["balance"],
                "unrealized_pl": acct["unrealizedPL"],
                "peak_nav": peak_nav,
                "drawdown_pct": (nav / peak_nav - 1) * 100 if peak_nav > 0 else 0,
                "positions": positions_snapshot,
                "signals": signals_snapshot,
                "prices": prices_snapshot,
                "trades": trade_log[-50:],  # keep last 50
                "killed": False,
                "kill_requested": False,
            }
            write_state(state)

        except Exception as e:
            print(f"  [ERROR] {e}")
            traceback.print_exc()

        time.sleep(cfg.POLL_INTERVAL_SEC)


if __name__ == "__main__":
    run()
