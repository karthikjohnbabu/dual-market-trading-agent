"""Technical indicators and momentum signal generation."""

from __future__ import annotations

from typing import Any

import pandas as pd


def compute_ema(
    df: pd.DataFrame,
    period: int,
    column: str = "close",
) -> pd.DataFrame:
    """Compute an exponential moving average and append it to the DataFrame.

    Uses pandas ``ewm`` with ``span=period`` and ``adjust=False``.

    Args:
        df: Input OHLCV (or price) DataFrame.
        period: EMA lookback period.
        column: Source price column (default ``close``).

    Returns:
        Copy of ``df`` with a new column ``ema_{period}``.
    """
    if column not in df.columns:
        raise KeyError(f"Column '{column}' not found in DataFrame")
    if period < 1:
        raise ValueError("period must be >= 1")

    result = df.copy()
    result[f"ema_{period}"] = (
        result[column].astype(float).ewm(span=period, adjust=False).mean()
    )
    return result


def compute_rsi(
    df: pd.DataFrame,
    period: int = 14,
    column: str = "close",
) -> pd.DataFrame:
    """Compute the Relative Strength Index using Wilder's smoothing.

    Implemented with pandas only (no TA-Lib). Wilder's smoothing is applied via
    ``ewm(alpha=1/period, adjust=False)``.

    Args:
        df: Input OHLCV (or price) DataFrame.
        period: RSI lookback period (default 14).
        column: Source price column (default ``close``).

    Returns:
        Copy of ``df`` with a new column ``rsi``.
    """
    if column not in df.columns:
        raise KeyError(f"Column '{column}' not found in DataFrame")
    if period < 1:
        raise ValueError("period must be >= 1")

    result = df.copy()
    delta = result[column].astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # When average loss is zero and there is gain, RSI is 100.
    rsi = rsi.fillna(100.0).where(avg_loss != 0, 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)

    result["rsi"] = rsi
    return result


def compute_volume_signal(
    df: pd.DataFrame,
    factor: float = 1.5,
) -> pd.DataFrame:
    """Flag bars where volume exceeds ``factor`` times the 20-day average.

    Args:
        df: Input DataFrame with a ``volume`` column.
        factor: Multiple of the 20-day average volume required (default 1.5).

    Returns:
        Copy of ``df`` with a boolean column ``volume_signal``.
    """
    if "volume" not in df.columns:
        raise KeyError("Column 'volume' not found in DataFrame")
    if factor <= 0:
        raise ValueError("factor must be > 0")

    result = df.copy()
    avg_volume = result["volume"].astype(float).rolling(window=20, min_periods=20).mean()
    result["volume_signal"] = result["volume"].astype(float) > (factor * avg_volume)
    result["volume_signal"] = result["volume_signal"].fillna(False).astype(bool)
    return result


def generate_momentum_signals(
    df: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    """Generate BUY / SELL / HOLD momentum signals from EMA, RSI, and volume.

    Reads parameters from ``config["momentum"]``:

    - ``ema_fast``, ``ema_slow``
    - ``rsi_period``, ``rsi_overbought``
    - ``volume_factor``

    Signal rules:

    - **BUY** when ``ema_fast > ema_slow`` AND ``rsi < rsi_overbought``
      AND ``volume_signal`` is True
    - **SELL** when ``ema_fast < ema_slow`` OR ``rsi > rsi_overbought``
    - **HOLD** otherwise

    Args:
        df: Input OHLCV DataFrame (must include ``close`` and ``volume``).
        config: Full settings dict (must contain a ``momentum`` section).

    Returns:
        DataFrame with indicator columns and a ``signal`` column.
    """
    if "momentum" not in config:
        raise KeyError("config must contain a 'momentum' section")

    momentum = config["momentum"]
    ema_fast = int(momentum["ema_fast"])
    ema_slow = int(momentum["ema_slow"])
    rsi_period = int(momentum["rsi_period"])
    rsi_overbought = float(momentum["rsi_overbought"])
    volume_factor = float(momentum["volume_factor"])

    result = compute_ema(df, period=ema_fast)
    result = compute_ema(result, period=ema_slow)
    result = compute_rsi(result, period=rsi_period)
    result = compute_volume_signal(result, factor=volume_factor)

    fast_col = f"ema_{ema_fast}"
    slow_col = f"ema_{ema_slow}"

    buy_mask = (
        (result[fast_col] > result[slow_col])
        & (result["rsi"] < rsi_overbought)
        & (result["volume_signal"])
    )
    sell_mask = (result[fast_col] < result[slow_col]) | (result["rsi"] > rsi_overbought)

    result["signal"] = "HOLD"
    result.loc[sell_mask, "signal"] = "SELL"
    result.loc[buy_mask, "signal"] = "BUY"
    return result
