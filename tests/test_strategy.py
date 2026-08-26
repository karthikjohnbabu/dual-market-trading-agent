"""Unit tests for indicators, momentum backtest, and paper-order guardrails."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from brokers.zerodha import ZerodhaClient
from indicators.signals import compute_ema, compute_rsi, generate_momentum_signals
from strategies.momentum import backtest_momentum


def _mock_ohlcv(n_rows: int = 50, seed: int = 42) -> pd.DataFrame:
    """Build a deterministic OHLCV DataFrame for indicator tests."""
    rng = np.random.default_rng(seed)
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(n_rows)]
    # Random-walk closes keep RSI/EMA numerically stable.
    returns = rng.normal(loc=0.001, scale=0.015, size=n_rows)
    close = 100.0 * np.cumprod(1.0 + returns)
    high = close * (1.0 + rng.uniform(0.0, 0.01, size=n_rows))
    low = close * (1.0 - rng.uniform(0.0, 0.01, size=n_rows))
    open_ = close * (1.0 + rng.normal(0.0, 0.002, size=n_rows))
    volume = rng.integers(100_000, 500_000, size=n_rows)

    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def _sample_config() -> dict:
    """Return a minimal config matching settings.yaml momentum/trading keys."""
    return {
        "trading": {
            "mode": "paper",
            "max_drawdown_pct": 15,
            "risk_per_trade_pct": 1,
            "capital_inr": 100_000,
            "capital_gbp": 5_000,
        },
        "momentum": {
            "ema_fast": 9,
            "ema_slow": 21,
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "volume_factor": 1.5,
        },
    }


def test_compute_ema() -> None:
    """EMA column is added and filled after the warmup window."""
    df = _mock_ohlcv(50)
    result = compute_ema(df, period=9)

    assert "ema_9" in result.columns
    warmup = 9
    after_warmup = result["ema_9"].iloc[warmup - 1 :]
    assert after_warmup.notna().all()
    assert len(after_warmup) == 50 - (warmup - 1)


def test_compute_rsi() -> None:
    """RSI column exists and non-null values stay within [0, 100]."""
    df = _mock_ohlcv(50)
    result = compute_rsi(df, period=14)

    assert "rsi" in result.columns
    rsi = result["rsi"].dropna()
    assert not rsi.empty
    assert (rsi >= 0).all()
    assert (rsi <= 100).all()


def test_generate_signals() -> None:
    """Momentum signals only contain BUY / SELL / HOLD."""
    df = _mock_ohlcv(50)
    result = generate_momentum_signals(df, _sample_config())

    assert "signal" in result.columns
    allowed = {"BUY", "SELL", "HOLD"}
    unique = set(result["signal"].dropna().unique())
    assert unique.issubset(allowed)
    assert unique  # at least one signal present


def test_backtest_momentum() -> None:
    """Backtest result includes core performance metrics."""
    df = _mock_ohlcv(50)
    signaled = generate_momentum_signals(df, _sample_config())
    results = backtest_momentum(signaled, _sample_config())

    for key in (
        "total_return_pct",
        "max_drawdown_pct",
        "win_rate",
        "total_trades",
    ):
        assert key in results

    assert isinstance(results["total_return_pct"], (int, float))
    assert isinstance(results["max_drawdown_pct"], (int, float))
    assert isinstance(results["win_rate"], (int, float))
    assert isinstance(results["total_trades"], int)
    assert results["total_trades"] >= 0


def test_paper_order_guard() -> None:
    """Paper mode returns PAPER_ORDER_SIMULATED and never hits kite.place_order."""
    client = ZerodhaClient.__new__(ZerodhaClient)
    client.config = {"trading": {"mode": "paper"}}
    client.kite = MagicMock()
    client.kite.place_order = MagicMock(return_value="SHOULD_NOT_BE_USED")

    order_id = client.place_order(
        symbol="RELIANCE",
        transaction_type="BUY",
        quantity=1,
        product="MIS",
    )

    assert order_id == "PAPER_ORDER_SIMULATED"
    client.kite.place_order.assert_not_called()


def test_day_trading_strategy_metadata() -> None:
    """DayTradingStrategy exposes MIS product and intraday timeframe."""
    from strategies.day_trading import DayTradingStrategy

    cfg = _sample_config()
    cfg["day_trading"] = {
        "product": "MIS",
        "timeframe": "5minute",
        "square_off_time": "15:15",
    }
    strategy = DayTradingStrategy(cfg)
    assert strategy.product() == "MIS"
    assert strategy.timeframe() == "5minute"

    df = _mock_ohlcv(50)
    signaled = strategy.run(df)
    summary = strategy.summary(signaled)
    assert summary["style"] == "day_trading"
    assert summary["product"] == "MIS"


def test_mean_reversion_signals() -> None:
    """Mean reversion emits only BUY/SELL/HOLD."""
    from strategies.mean_reversion import MeanReversionStrategy

    cfg = _sample_config()
    cfg["mean_reversion"] = {
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "lookback": 20,
        "entry_z": 2.0,
    }
    result = MeanReversionStrategy(cfg).run(_mock_ohlcv(80))
    assert "signal" in result.columns
    assert "zscore" in result.columns
    assert set(result["signal"].dropna().unique()).issubset({"BUY", "SELL", "HOLD"})
