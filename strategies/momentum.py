"""Momentum trading strategy and simple signal-based backtest."""

from __future__ import annotations

from typing import Any

import pandas as pd

from indicators.signals import generate_momentum_signals


class MomentumStrategy:
    """EMA / RSI / volume momentum strategy wrapper."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Store the strategy configuration.

        Args:
            config: Full settings dictionary (must include ``momentum`` and
                ``trading`` sections for run / backtest workflows).
        """
        self.config = config

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate momentum signals for the given OHLCV DataFrame.

        Calls ``generate_momentum_signals`` from ``indicators.signals``.

        Args:
            df: Input OHLCV DataFrame with at least ``close`` and ``volume``.

        Returns:
            DataFrame with indicator columns and a ``signal`` column
            (``BUY`` / ``SELL`` / ``HOLD``).
        """
        return generate_momentum_signals(df, self.config)

    def summary(self, df: pd.DataFrame) -> dict[str, Any]:
        """Summarize signal counts from a DataFrame that includes ``signal``.

        Args:
            df: DataFrame produced by ``run`` (must contain ``signal``;
                ``date`` is used when present for ``last_signal_date``).

        Returns:
            Dictionary with ``total_signals``, ``buy_signals``, ``sell_signals``,
            ``last_signal``, and ``last_signal_date``.
        """
        if "signal" not in df.columns:
            raise KeyError("DataFrame must contain a 'signal' column; call run() first")

        if df.empty:
            return {
                "total_signals": 0,
                "buy_signals": 0,
                "sell_signals": 0,
                "last_signal": "HOLD",
                "last_signal_date": "",
            }

        buy_signals = int((df["signal"] == "BUY").sum())
        sell_signals = int((df["signal"] == "SELL").sum())
        # Count actionable signals (exclude HOLD).
        total_signals = buy_signals + sell_signals

        last_row = df.iloc[-1]
        last_signal = str(last_row["signal"])
        if "date" in df.columns:
            last_date = last_row["date"]
            last_signal_date = (
                last_date.strftime("%Y-%m-%d")
                if hasattr(last_date, "strftime")
                else str(last_date)[:10]
            )
        else:
            last_signal_date = str(df.index[-1])

        return {
            "total_signals": total_signals,
            "buy_signals": buy_signals,
            "sell_signals": sell_signals,
            "last_signal": last_signal,
            "last_signal_date": last_signal_date,
        }


def _starting_capital(config: dict[str, Any]) -> float:
    """Resolve starting capital from ``config['trading']``.

    Prefers ``capital_inr``, then falls back to ``capital_gbp``.
    """
    trading = config.get("trading", {})
    if "capital_inr" in trading:
        return float(trading["capital_inr"])
    if "capital_gbp" in trading:
        return float(trading["capital_gbp"])
    raise KeyError("config['trading'] must include capital_inr or capital_gbp")


def _max_drawdown_pct(equity: list[float]) -> float:
    """Compute maximum drawdown percentage from an equity curve."""
    if not equity:
        return 0.0

    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (peak - value) / peak * 100.0
            if drawdown > max_dd:
                max_dd = drawdown
    return float(max_dd)


def backtest_momentum(df: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    """Simulate a long-only backtest from momentum ``signal`` values.

    - Starts with ``capital_inr`` or ``capital_gbp`` from ``config['trading']``.
    - On **BUY** (when flat): invest ``risk_per_trade_pct``% of current capital
      at the bar's ``close``.
    - On **SELL** (when in a position): close the full position at ``close``.
    - Tracks ``final_capital``, ``total_return_pct``, ``max_drawdown_pct``,
      ``win_rate``, and ``total_trades``.
    - If ``max_drawdown_pct > 15``, adds ``risk_flag``: ``HIGH RISK``.

    Args:
        df: OHLCV DataFrame. If ``signal`` is missing, signals are generated
            via ``MomentumStrategy.run``.
        config: Full settings dictionary.

    Returns:
        Backtest results dictionary.
    """
    trading = config.get("trading", {})
    risk_per_trade_pct = float(trading.get("risk_per_trade_pct", 1))
    max_drawdown_limit = float(trading.get("max_drawdown_pct", 15))

    working = df.copy()
    if "signal" not in working.columns:
        working = MomentumStrategy(config).run(working)

    if "close" not in working.columns:
        raise KeyError("DataFrame must contain a 'close' column")

    capital = _starting_capital(config)
    starting_capital = capital
    position_shares = 0.0
    entry_price = 0.0
    equity_curve: list[float] = []
    trade_pnls: list[float] = []

    for _, row in working.iterrows():
        price = float(row["close"])
        signal = str(row["signal"])

        if signal == "BUY" and position_shares == 0.0 and capital > 0:
            notional = capital * (risk_per_trade_pct / 100.0)
            if notional > 0 and price > 0:
                position_shares = notional / price
                entry_price = price
                capital -= notional

        elif signal == "SELL" and position_shares > 0.0:
            proceeds = position_shares * price
            pnl = position_shares * (price - entry_price)
            capital += proceeds
            trade_pnls.append(pnl)
            position_shares = 0.0
            entry_price = 0.0

        mark_to_market = capital + (position_shares * price)
        equity_curve.append(mark_to_market)

    # Mark any open position at the last close.
    if position_shares > 0.0 and len(working) > 0:
        final_price = float(working.iloc[-1]["close"])
        capital += position_shares * final_price
        trade_pnls.append(position_shares * (final_price - entry_price))
        position_shares = 0.0
        if equity_curve:
            equity_curve[-1] = capital

    final_capital = float(capital)
    total_return_pct = (
        ((final_capital - starting_capital) / starting_capital) * 100.0
        if starting_capital
        else 0.0
    )
    max_drawdown_pct = _max_drawdown_pct(equity_curve)
    total_trades = len(trade_pnls)
    wins = sum(1 for pnl in trade_pnls if pnl > 0)
    win_rate = (wins / total_trades * 100.0) if total_trades else 0.0

    results: dict[str, Any] = {
        "final_capital": round(final_capital, 2),
        "total_return_pct": round(total_return_pct, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "win_rate": round(win_rate, 4),
        "total_trades": total_trades,
        "starting_capital": starting_capital,
    }

    if max_drawdown_pct > max_drawdown_limit or max_drawdown_pct > 15:
        results["risk_flag"] = "HIGH RISK"

    return results
