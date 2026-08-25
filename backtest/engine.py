"""Backtest runner for India (Zerodha) and UK/US (Alpha Vantage) symbols.

Runnable as::

    python -m backtest.engine
"""

from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

from data.fetch import fetch_india_data, fetch_uk_data, load_config
from strategies.momentum import MomentumStrategy, backtest_momentum

HIGH_RISK_DRAWDOWN_PCT = 15.0
LOOKBACK_DAYS = 365


def _date_window(days: int = LOOKBACK_DAYS) -> tuple[date, date]:
    """Return ``(from_date, to_date)`` covering the last ``days`` calendar days."""
    to_date = date.today()
    from_date = to_date - timedelta(days=days)
    return from_date, to_date


def _config_for_market(config: dict[str, Any], market: str) -> dict[str, Any]:
    """Return a config copy with capital appropriate for the market.

    India uses ``capital_inr``. UK drops ``capital_inr`` so ``backtest_momentum``
    falls back to ``capital_gbp``.
    """
    cfg = copy.deepcopy(config)
    if market.lower() == "uk":
        cfg.setdefault("trading", {}).pop("capital_inr", None)
    return cfg


def _run_symbol(
    symbol: str,
    market: str,
    df: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Run momentum signals and backtest for a single symbol.

    Args:
        symbol: Ticker symbol.
        market: Market label (``india`` or ``uk``).
        df: OHLCV DataFrame.
        config: Market-adjusted settings dictionary.

    Returns:
        Row dict for the summary table, including backtest metrics.
    """
    row: dict[str, Any] = {
        "symbol": symbol,
        "market": market,
        "total_return_pct": None,
        "max_drawdown_pct": None,
        "risk_flag": "",
        "total_trades": 0,
        "win_rate": None,
        "final_capital": None,
    }

    if df.empty:
        logger.warning("Skipping {}: no data returned", symbol)
        row["risk_flag"] = "NO DATA"
        return row

    strategy = MomentumStrategy(config)
    signaled = strategy.run(df)
    signal_summary = strategy.summary(signaled)
    results = backtest_momentum(signaled, config)

    drawdown = float(results.get("max_drawdown_pct", 0.0))
    risk_flag = str(results.get("risk_flag", ""))
    if drawdown > HIGH_RISK_DRAWDOWN_PCT:
        risk_flag = "HIGH RISK"
        results["risk_flag"] = risk_flag
        logger.warning(
            "{} [{}] flagged HIGH RISK — max drawdown {:.2f}% > {}%",
            symbol,
            market.upper(),
            drawdown,
            HIGH_RISK_DRAWDOWN_PCT,
        )

    logger.info(
        "{} [{}] | signals: buy={} sell={} last={} on {} | "
        "return={:.2f}% | drawdown={:.2f}% | trades={} | win_rate={:.2f}% | "
        "final_capital={:.2f} | risk={}",
        symbol,
        market.upper(),
        signal_summary["buy_signals"],
        signal_summary["sell_signals"],
        signal_summary["last_signal"],
        signal_summary["last_signal_date"],
        float(results.get("total_return_pct", 0.0)),
        drawdown,
        int(results.get("total_trades", 0)),
        float(results.get("win_rate", 0.0)),
        float(results.get("final_capital", 0.0)),
        risk_flag or "OK",
    )

    row.update(
        {
            "total_return_pct": results.get("total_return_pct"),
            "max_drawdown_pct": results.get("max_drawdown_pct"),
            "risk_flag": risk_flag,
            "total_trades": results.get("total_trades", 0),
            "win_rate": results.get("win_rate"),
            "final_capital": results.get("final_capital"),
        }
    )
    return row


def run_india_backtests(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch and backtest every symbol in ``config['india']['symbols']``."""
    india = config.get("india", {})
    symbols = list(india.get("symbols", []))
    interval = str(india.get("timeframe", "day"))
    from_date, to_date = _date_window()
    market_config = _config_for_market(config, "india")

    logger.info(
        "Starting India backtests: {} symbols, {} → {}, interval={}",
        len(symbols),
        from_date,
        to_date,
        interval,
    )

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        logger.info("Fetching India data for {}", symbol)
        try:
            df = fetch_india_data(symbol, from_date, to_date, interval=interval)
        except Exception as exc:  # noqa: BLE001
            logger.exception("India fetch failed for {}: {}", symbol, exc)
            df = pd.DataFrame()
        rows.append(_run_symbol(symbol, "india", df, market_config))
    return rows


def run_uk_backtests(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch and backtest every symbol in ``config['uk']['symbols']``."""
    uk = config.get("uk", {})
    symbols = list(uk.get("symbols", []))
    from_date, to_date = _date_window()
    market_config = _config_for_market(config, "uk")

    logger.info(
        "Starting UK backtests: {} symbols, {} → {}",
        len(symbols),
        from_date,
        to_date,
    )

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        logger.info("Fetching UK/US data for {}", symbol)
        try:
            df = fetch_uk_data(symbol, from_date, to_date)
        except Exception as exc:  # noqa: BLE001
            logger.exception("UK fetch failed for {}: {}", symbol, exc)
            df = pd.DataFrame()
        rows.append(_run_symbol(symbol, "uk", df, market_config))
    return rows


def print_summary_table(rows: list[dict[str, Any]]) -> None:
    """Print a console summary table for all backtested symbols."""
    headers = ("symbol", "market", "total_return_pct", "max_drawdown_pct", "risk_flag")
    widths = {
        "symbol": 12,
        "market": 8,
        "total_return_pct": 18,
        "max_drawdown_pct": 18,
        "risk_flag": 12,
    }

    def _fmt(value: Any, key: str) -> str:
        if value is None or value == "":
            text = "-"
        elif key in {"total_return_pct", "max_drawdown_pct"} and isinstance(
            value, (int, float)
        ):
            text = f"{float(value):.2f}"
        else:
            text = str(value)
        return text.ljust(widths[key]) if key == "risk_flag" or key in {
            "symbol",
            "market",
        } else text.rjust(widths[key])

    header_line = " | ".join(h.ljust(widths[h]) if h in {"symbol", "market", "risk_flag"} else h.rjust(widths[h]) for h in headers)
    separator = "-+-".join("-" * widths[h] for h in headers)

    logger.info("Backtest summary")
    print()
    print(header_line)
    print(separator)
    for row in rows:
        print(" | ".join(_fmt(row.get(h), h) for h in headers))
    print()


def main() -> None:
    """Load config/env, run India and UK backtests, then print a summary."""
    load_dotenv()
    logger.info("Loaded environment from .env (if present)")

    config = load_config()
    logger.info("Loaded config; trading mode={}", config.get("trading", {}).get("mode"))

    all_rows: list[dict[str, Any]] = []
    all_rows.extend(run_india_backtests(config))
    all_rows.extend(run_uk_backtests(config))

    print_summary_table(all_rows)

    high_risk = [r["symbol"] for r in all_rows if r.get("risk_flag") == "HIGH RISK"]
    if high_risk:
        logger.warning("HIGH RISK symbols: {}", ", ".join(high_risk))
    else:
        logger.info("No symbols exceeded {}% max drawdown", HIGH_RISK_DRAWDOWN_PCT)

    logger.info("Backtest run complete ({} symbols)", len(all_rows))


if __name__ == "__main__":
    main()
