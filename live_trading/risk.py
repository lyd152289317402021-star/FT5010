"""
Risk monitoring — drawdown check, leverage check, kill switch logic.
"""

import config as cfg
import execution


def check_drawdown(nav, peak_nav):
    """Return True if drawdown exceeds MAX_DRAWDOWN_PCT."""
    if peak_nav <= 0:
        return False
    dd = (nav - peak_nav) / peak_nav
    return dd < -cfg.MAX_DRAWDOWN_PCT


def check_leverage(nav, positions, prices, qta_map):
    """
    Return True if total gross notional / NAV > MAX_LEVERAGE.
    `prices`: dict {instrument: current_close}
    `qta_map`: dict {instrument: quote_to_account}
    """
    gross = 0.0
    for inst, units in positions.items():
        px = prices.get(inst, 0)
        qta = qta_map.get(inst, 1.0)
        gross += abs(units) * px * qta
    lev = gross / max(nav, 1.0)
    return lev > cfg.MAX_LEVERAGE


def kill_switch(reason="manual"):
    """Close all positions and return summary."""
    print(f"[KILL SWITCH] triggered: {reason}")
    results = execution.close_all_positions()
    for r in results:
        print(f"  closed {r.get('instrument')}: {r}")
    return results
