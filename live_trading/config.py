"""
Configuration for FT5010 Live Trading System.
"""

# ---- OANDA Credentials ----
OANDA_TOKEN      = "042919c9d89351ae2d6141778420434d-cdd631c6497843cd515172ea976b029d"
OANDA_ACCOUNT_ID = "101-003-38799014-001"
OANDA_ENV        = "practice"          # "practice" or "live"

# ---- Universe ----
UNIVERSE = ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD", "NZD_USD"]

# ---- Strategy parameters (must match backtester) ----
FAST_SLOW_PAIRS   = ((64, 256), (128, 512), (256, 1024))
TARGET_ABS_FORECAST = 10.0
CAP_FORECAST       = 20.0
PRICE_VOL_SPAN     = 120
REVERSAL_LOOKBACK  = 72
TREND_WEIGHT       = 0.70
REVERSAL_WEIGHT    = 0.30
VOL_GATE_ENABLED   = True
VOL_GATE_CUTOFF    = 0.90
VOL_GATE_SCALE     = 0.5

# ---- Portfolio ----
CAPITAL            = 100_000.0
VOL_TARGET_ANN     = 0.12
LOT_SIZE           = 1000
NO_TRADE_BUFFER    = 0.10

# ---- Execution ----
POLL_INTERVAL_SEC  = 60           # how often to check for rebalance (seconds)
GRANULARITY        = "H1"
LOOKBACK_BARS      = 5000         # bars to pull for signal warmup (max per OANDA request)

# ---- Risk ----
MAX_DRAWDOWN_PCT   = 0.25         # kill switch triggers at 25% drawdown
MAX_LEVERAGE       = 4.0

# ---- State file (shared with dashboard) ----
import os
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
