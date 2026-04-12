"""
SingleFXCarverStrategy — identical to the backtester notebook version.
H1 EWMAC trend + short-term reversal + vol gate.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field


@dataclass
class SingleFXCarverStrategy:
    fast_slow_pairs: tuple = ((64, 256), (128, 512), (256, 1024))
    trend_group_weight: float = 0.70
    reversal_lookback: int = 72
    reversal_group_weight: float = 0.30
    vol_gate_enabled: bool = True
    vol_gate_realized_window: int = 24 * 7
    vol_gate_percentile_window: int = 24 * 30 * 6
    vol_gate_cutoff: float = 0.90
    vol_gate_scale: float = 0.5
    target_abs_forecast: float = 10.0
    cap_forecast: float = 20.0
    price_vol_span: int = 120
    min_train_obs: int = 2000
    lot_size: int = 1000
    no_trade_buffer: float = 0.10
    corr_floor: float = 0.05

    scalars_: dict = field(default_factory=dict, init=False)
    rule_weights_: dict = field(default_factory=dict, init=False)
    forecast_div_mult_: float = field(default=np.nan, init=False)
    fitted_: bool = field(default=False, init=False)

    @staticmethod
    def _clip(s, cap):
        return s.clip(lower=-cap, upper=cap)

    @staticmethod
    def _safe_mean_abs(s):
        x = s.replace([np.inf, -np.inf], np.nan).dropna()
        return np.nan if len(x) == 0 else x.abs().mean()

    def _price_vol(self, close):
        return close.diff().ewm(span=self.price_vol_span, adjust=False,
                                min_periods=self.price_vol_span).std().clip(lower=1e-12)

    def _raw_ewmac(self, close, fast, slow, pvol):
        f = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
        s = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
        return ((f - s) / pvol).replace([np.inf, -np.inf], np.nan)

    def _raw_reversal(self, close, pvol, lb):
        return (-(close - close.shift(lb)) / pvol).replace([np.inf, -np.inf], np.nan)

    def _vol_gate(self, close):
        if not self.vol_gate_enabled:
            return pd.Series(1.0, index=close.index)
        r = close.pct_change().rolling(self.vol_gate_realized_window,
                                       min_periods=self.vol_gate_realized_window).std()
        pct = r.rolling(self.vol_gate_percentile_window,
                        min_periods=self.vol_gate_percentile_window // 2).rank(pct=True)
        g = pd.Series(1.0, index=close.index)
        g[pct > self.vol_gate_cutoff] = self.vol_gate_scale
        return g.fillna(1.0)

    def _build_raw(self, df):
        out = pd.DataFrame(index=df.index)
        out["close"] = df["close"].astype(float)
        out["price_vol_points"] = self._price_vol(out["close"])
        for f, s in self.fast_slow_pairs:
            out[f"raw_ewmac_{f}_{s}"] = self._raw_ewmac(
                out["close"], f, s, out["price_vol_points"])
        out["raw_reversal"] = self._raw_reversal(
            out["close"], out["price_vol_points"], self.reversal_lookback)
        return out

    def _scalar(self, raw):
        m = self._safe_mean_abs(raw)
        return np.nan if (pd.isna(m) or m <= 0) else self.target_abs_forecast / m

    def _rule_weights(self, fdf):
        trend_cols = [f"forecast_ewmac_{f}_{s}" for f, s in self.fast_slow_pairs]
        tdf = fdf[trend_cols].dropna()
        if len(tdf) < 20:
            ti = {c: 1 / len(trend_cols) for c in trend_cols}
        else:
            corr = tdf.corr().fillna(0.0)
            scores = {}
            for c in trend_cols:
                others = [x for x in trend_cols if x != c]
                ac = max(float(corr.loc[c, others].mean()), self.corr_floor)
                scores[c] = 1.0 / ac
            tot = sum(scores.values())
            ti = {k: v / tot for k, v in scores.items()}
        w = {c: self.trend_group_weight * ti[c] for c in trend_cols}
        w["forecast_reversal"] = self.reversal_group_weight
        return w

    def fit(self, train_df):
        raw = self._build_raw(train_df)
        if len(raw.dropna()) < self.min_train_obs:
            raise ValueError(f"Need >= {self.min_train_obs} clean obs")
        self.scalars_ = {}
        ft = pd.DataFrame(index=raw.index)
        for f, s in self.fast_slow_pairs:
            rule = f"ewmac_{f}_{s}"
            sc = self._scalar(raw[f"raw_{rule}"])
            self.scalars_[rule] = sc
            ft[f"forecast_{rule}"] = self._clip(raw[f"raw_{rule}"] * sc, self.cap_forecast)
        rs = self._scalar(raw["raw_reversal"])
        self.scalars_["reversal"] = rs if np.isfinite(rs) else 0.0
        ft["forecast_reversal"] = self._clip(
            raw["raw_reversal"] * self.scalars_["reversal"], self.cap_forecast)
        self.rule_weights_ = self._rule_weights(ft)
        combined = sum(ft[c] * w for c, w in self.rule_weights_.items())
        mac = self._safe_mean_abs(combined)
        if pd.isna(mac) or mac <= 0:
            raise ValueError("Cannot compute FDM")
        self.forecast_div_mult_ = self.target_abs_forecast / mac
        self.fitted_ = True
        return self

    def transform(self, df):
        if not self.fitted_:
            raise ValueError("fit first")
        raw = self._build_raw(df)
        out = raw.copy()
        for f, s in self.fast_slow_pairs:
            rule = f"ewmac_{f}_{s}"
            out[f"forecast_{rule}"] = self._clip(
                out[f"raw_{rule}"] * self.scalars_[rule], self.cap_forecast)
        out["forecast_reversal"] = self._clip(
            out["raw_reversal"] * self.scalars_["reversal"], self.cap_forecast)
        combined_raw = sum(out[c] * w for c, w in self.rule_weights_.items())
        combined = self._clip(combined_raw * self.forecast_div_mult_, self.cap_forecast)
        gate = self._vol_gate(out["close"])
        smoothed = (combined * gate).ewm(span=8, min_periods=1).mean()
        out["combined_forecast"] = smoothed.clip(
            lower=-self.cap_forecast, upper=self.cap_forecast)
        return out

    def latest_forecast(self, df):
        """Return the most recent combined_forecast value."""
        sig = self.transform(df)
        return float(sig["combined_forecast"].iloc[-1])

    def latest_price_vol(self, df):
        """Return the most recent price_vol_points."""
        sig = self.transform(df)
        return float(sig["price_vol_points"].iloc[-1])
