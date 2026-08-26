"""Mean-reversion strategy using RSI extremes and z-score of close."""

from __future__ import annotations

from typing import Any

import pandas as pd

from indicators.signals import compute_rsi


class MeanReversionStrategy:
    """Fade stretched moves when RSI and z-score agree."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Store config; reads ``mean_reversion`` section."""
        self.config = config
        self.params = config.get("mean_reversion", {})

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add ``zscore`` / ``rsi`` and a ``signal`` column (BUY/SELL/HOLD).

        **BUY** when RSI < oversold AND z-score <= -entry_z  
        **SELL** when RSI > overbought OR z-score >= entry_z  
        **HOLD** otherwise
        """
        lookback = int(self.params.get("lookback", 20))
        entry_z = float(self.params.get("entry_z", 2.0))
        rsi_period = int(self.params.get("rsi_period", 14))
        oversold = float(self.params.get("rsi_oversold", 30))
        overbought = float(self.params.get("rsi_overbought", 70))

        result = compute_rsi(df, period=rsi_period)
        close = result["close"].astype(float)
        mid = close.rolling(lookback, min_periods=lookback).mean()
        std = close.rolling(lookback, min_periods=lookback).std()
        result["zscore"] = (close - mid) / std.replace(0, pd.NA)

        buy = (result["rsi"] < oversold) & (result["zscore"] <= -entry_z)
        sell = (result["rsi"] > overbought) | (result["zscore"] >= entry_z)

        result["signal"] = "HOLD"
        result.loc[sell, "signal"] = "SELL"
        result.loc[buy, "signal"] = "BUY"
        return result

    def summary(self, df: pd.DataFrame) -> dict[str, Any]:
        """Summarize actionable mean-reversion signals."""
        if "signal" not in df.columns or df.empty:
            return {
                "total_signals": 0,
                "buy_signals": 0,
                "sell_signals": 0,
                "last_signal": "HOLD",
                "style": "mean_reversion",
            }
        buy_n = int((df["signal"] == "BUY").sum())
        sell_n = int((df["signal"] == "SELL").sum())
        return {
            "total_signals": buy_n + sell_n,
            "buy_signals": buy_n,
            "sell_signals": sell_n,
            "last_signal": str(df.iloc[-1]["signal"]),
            "style": "mean_reversion",
        }
