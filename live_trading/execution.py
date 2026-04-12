"""
OANDA execution layer — fetch candles, get positions, place market orders,
close all positions (kill switch).
"""

import pandas as pd
from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles
from oandapyV20.endpoints.orders import OrderCreate
from oandapyV20.endpoints.positions import PositionDetails, OpenPositions, PositionClose
from oandapyV20.endpoints.accounts import AccountDetails

import config as cfg


def get_client():
    return API(access_token=cfg.OANDA_TOKEN, environment=cfg.OANDA_ENV)


# ---- Account info ----

def get_account_summary(client=None):
    client = client or get_client()
    r = AccountDetails(accountID=cfg.OANDA_ACCOUNT_ID)
    client.request(r)
    acct = r.response["account"]
    return {
        "balance": float(acct["balance"]),
        "unrealizedPL": float(acct["unrealizedPL"]),
        "nav": float(acct["NAV"]),
        "openTradeCount": int(acct["openTradeCount"]),
        "marginUsed": float(acct["marginUsed"]),
    }


# ---- Candle data ----

def fetch_candles(instrument, granularity=cfg.GRANULARITY, count=cfg.LOOKBACK_BARS,
                  client=None):
    """Fetch the latest `count` H1 candles for `instrument`."""
    client = client or get_client()
    params = {"granularity": granularity, "price": "M", "count": count}
    r = InstrumentsCandles(instrument=instrument, params=params)
    client.request(r)
    candles = r.response.get("candles", [])
    rows = []
    for c in candles:
        if not c.get("complete", True):
            continue
        mid = c["mid"]
        rows.append({
            "time": pd.Timestamp(c["time"]).tz_convert("UTC"),
            "open": float(mid["o"]),
            "close": float(mid["c"]),
        })
    df = pd.DataFrame(rows).drop_duplicates("time").set_index("time").sort_index()
    df.index = df.index.tz_localize(None)
    return df


# ---- Positions ----

def get_open_positions(client=None):
    """Return dict {instrument: signed_units}."""
    client = client or get_client()
    r = OpenPositions(accountID=cfg.OANDA_ACCOUNT_ID)
    client.request(r)
    positions = {}
    for p in r.response.get("positions", []):
        inst = p["instrument"]
        long_units = int(p["long"]["units"])
        short_units = int(p["short"]["units"])
        net = long_units + short_units
        if net != 0:
            positions[inst] = net
    return positions


def get_position(instrument, client=None):
    """Return signed units for a single instrument, 0 if no position."""
    client = client or get_client()
    try:
        r = PositionDetails(accountID=cfg.OANDA_ACCOUNT_ID, instrument=instrument)
        client.request(r)
        pos = r.response["position"]
        return int(pos["long"]["units"]) + int(pos["short"]["units"])
    except Exception:
        return 0


# ---- Order placement ----

def place_market_order(instrument, units, client=None):
    """
    Place a market order. `units` is signed (positive=buy, negative=sell).
    Returns the order response dict.
    """
    client = client or get_client()
    if units == 0:
        return None
    data = {
        "order": {
            "instrument": instrument,
            "units": str(int(units)),
            "type": "MARKET",
            "timeInForce": "FOK",
        }
    }
    r = OrderCreate(accountID=cfg.OANDA_ACCOUNT_ID, data=data)
    client.request(r)
    return r.response


# ---- Kill switch: close all positions ----

def close_all_positions(client=None):
    """Close every open position. Returns list of close responses."""
    client = client or get_client()
    positions = get_open_positions(client)
    responses = []
    for inst, units in positions.items():
        try:
            if units > 0:
                data = {"longUnits": "ALL"}
            else:
                data = {"shortUnits": "ALL"}
            r = PositionClose(accountID=cfg.OANDA_ACCOUNT_ID,
                              instrument=inst, data=data)
            client.request(r)
            responses.append({"instrument": inst, "closed": units, "response": r.response})
        except Exception as e:
            responses.append({"instrument": inst, "error": str(e)})
    return responses
