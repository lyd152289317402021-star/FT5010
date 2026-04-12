# FT5010 FX Trend Strategy

Carver-style EWMAC Trend + Reversal + Vol Gate on 7 USD FX Majors.

## OANDA Credentials
- **Token**: `042919c9d89351ae2d6141778420434d-cdd631c6497843cd515172ea976b029d`
- **Account ID**: `101-003-38799014-001`
- **Environment**: practice

Please do not modify or revoke these credentials for at least one month after submission so the strategy can be tested live.

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Launch the Dashboard
```bash
cd dashboard
python app.py
# Open http://127.0.0.1:8050
```

The dashboard has two tabs:
- **3H Demo Review** — static view of the April 10, 2026 live session (no engine required)
- **Live Trading** — real-time monitoring; use the Start Engine and Kill Switch buttons here

### 3. Run the Engine (optional — or use the dashboard button)
```bash
cd live_trading
python main.py
```

Log output is written to `live_trading/engine.log`. State is written to `live_trading/state.json` at every poll and read by the dashboard.

## Kill Switch
The red **KILL SWITCH** button on the dashboard immediately closes all open OANDA positions via a direct API call and signals the engine to stop — no waiting for the next poll cycle. This is the recommended way to stop the system.

Pressing `Ctrl+C` in the terminal will stop the engine process but does **not** automatically close open positions on OANDA.

## Project Structure
```
FT5010 forex quantitive trading/
├── requirements.txt
├── README.md
├── new_strategy.ipynb           # Backtesting notebook (event-driven)
├── live_trading/
│   ├── config.py                # OANDA credentials + all strategy params
│   ├── strategy.py              # SingleFXCarverStrategy (fit + transform)
│   ├── execution.py             # OANDA API: candles, orders, positions
│   ├── risk.py                  # Drawdown monitor + leverage check
│   └── main.py                  # Live engine entry point
├── dashboard/
│   └── app.py                   # Plotly Dash monitoring dashboard
└── live_demo_evidence/
    ├── engine_3h_demo.log       # Full engine log from the 3h live session
    ├── engine.log               # Most recent engine log
    ├── live_demo_summary.json   # Structured summary data for the dashboard
    ├── state_3h_demo.json       # State snapshot from end of 3h session
    └── FT5010 Strategy Dashboard.pdf  # Dashboard screenshot PDF
```

## Strategy Summary
| Component | Detail |
|-----------|--------|
| Universe | EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD |
| Data | H1 candles via OANDA v20 API |
| Trend signal | EWMAC at three speeds (64/256, 128/512, 256/1024) — 70% weight |
| Reversal signal | Negative 72-bar price change — 30% weight |
| Vol gate | Top-decile realised vol → forecast × 0.5 |
| Position sizing | 12% annual vol target, equal capital per pair |
| Risk limits | Max leverage 4x, no-trade buffer 10%, drawdown kill switch at 25% |
| Execution | Market orders, 60-second poll interval |
