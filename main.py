"""Single CLI entry point for the dual-market trading agent.

Usage::

    python main.py --mode backtest
    python main.py --mode paper
    python main.py --mode live
    python main.py --mode scan
"""

from __future__ import annotations

import argparse
import copy
import sys
from datetime import date, timedelta
from typing import Any

from dotenv import load_dotenv
from loguru import logger

from backtest.engine import (
    HIGH_RISK_DRAWDOWN_PCT,
    print_summary_table,
    run_india_backtests,
    run_uk_backtests,
)
from data.fetch import fetch_india_data, fetch_uk_data, load_config
from execution.order_manager import OrderManager
from indicators.signals import generate_momentum_signals


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the top-level ``--mode`` argument."""
    parser = argparse.ArgumentParser(
        description="Dual-market trading agent (Zerodha India + eToro/UK scan)",
    )
    parser.add_argument(
        "--mode",
        choices=("backtest", "paper", "live", "scan"),
        required=True,
        help="backtest | paper | live | scan",
    )
    return parser.parse_args(argv)


def run_backtest_mode(config: dict[str, Any]) -> None:
    """Run full India + UK backtests and print the summary table."""
    logger.info("Mode=backtest â€” running engine for all configured symbols")
    rows: list[dict[str, Any]] = []
    rows.extend(run_india_backtests(config))
    rows.extend(run_uk_backtests(config))
    print_summary_table(rows)

    high_risk = [r["symbol"] for r in rows if r.get("risk_flag") == "HIGH RISK"]
    if high_risk:
        logger.warning("HIGH RISK symbols: {}", ", ".join(high_risk))
    else:
        logger.info("No symbols exceeded {}% max drawdown", HIGH_RISK_DRAWDOWN_PCT)


def run_order_mode(config: dict[str, Any], mode: str) -> None:
    """Run the order-manager loop in paper or live mode."""
    cfg = copy.deepcopy(config)
    cfg.setdefault("trading", {})["mode"] = mode
    logger.info("Mode={} â€” starting OrderManager loop", mode)

    if mode == "live":
        logger.warning(
            "LIVE MODE: real orders may be sent to Zerodha. "
            "Paper trade first unless you explicitly intend live trading. "
            "Ctrl+C to stop."
        )

    manager = OrderManager(cfg)
    try:
        manager.run_live()
    except KeyboardInterrupt:
        logger.info("OrderManager stopped by user")


def _scan_one(
    symbol: str,
    market: str,
    config: dict[str, Any],
    days: int = 90,
) -> dict[str, Any]:
    """Fetch data and return the latest momentum signal row for one symbol."""
    to_date = date.today()
    from_date = to_date - timedelta(days=days)

    if market == "india":
        df = fetch_india_data(symbol, from_date, to_date, interval="day")
    else:
        df = fetch_uk_data(symbol, from_date, to_date)

    if df.empty:
        return {
            "symbol": symbol,
            "market": market,
            "signal": "NO_DATA",
            "close": "",
            "rsi": "",
        }

    signaled = generate_momentum_signals(df, config)
    last = signaled.iloc[-1]
    rsi = last.get("rsi")
    try:
        rsi_s = f"{float(rsi):.2f}"
    except (TypeError, ValueError):
        rsi_s = ""
    try:
        close_s = f"{float(last.get('close')):.2f}"
    except (TypeError, ValueError):
        close_s = ""

    return {
        "symbol": symbol,
        "market": market,
        "signal": str(last.get("signal", "HOLD")),
        "close": close_s,
        "rsi": rsi_s,
    }


def run_scan_mode(config: dict[str, Any]) -> None:
    """Scan all configured India and UK symbols; print BUY/SELL/HOLD."""
    logger.info("Mode=scan â€” momentum signals for configured symbols")
    rows: list[dict[str, Any]] = []

    for symbol in config.get("india", {}).get("symbols", []):
        logger.info("Scanning India: {}", symbol)
        try:
            rows.append(_scan_one(symbol, "india", config))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scan failed for {}: {}", symbol, exc)
            rows.append(
                {
                    "symbol": symbol,
                    "market": "india",
                    "signal": "ERROR",
                    "close": "",
                    "rsi": "",
                }
            )

    for symbol in config.get("uk", {}).get("symbols", []):
        logger.info("Scanning UK: {}", symbol)
        try:
            rows.append(_scan_one(symbol, "uk", config))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scan failed for {}: {}", symbol, exc)
            rows.append(
                {
                    "symbol": symbol,
                    "market": "uk",
                    "signal": "ERROR",
                    "close": "",
                    "rsi": "",
                }
            )

    headers = ("symbol", "market", "signal", "close", "rsi")
    widths = {"symbol": 12, "market": 8, "signal": 10, "close": 12, "rsi": 8}
    print()
    print(" | ".join(h.ljust(widths[h]) for h in headers))
    print("-+-".join("-" * widths[h] for h in headers))
    for row in rows:
        print(
            " | ".join(str(row.get(h, "")).ljust(widths[h]) for h in headers)
        )
    print()

    buys = [r["symbol"] for r in rows if r.get("signal") == "BUY"]
    sells = [r["symbol"] for r in rows if r.get("signal") == "SELL"]
    logger.info("Scan complete â€” BUY: {} | SELL: {}", buys or "none", sells or "none")


def main(argv: list[str] | None = None) -> None:
    """Load env/config and dispatch to the selected mode."""
    load_dotenv()
    args = parse_args(argv)
    config = load_config()
    logger.info(
        "Loaded config; file_mode={} cli_mode={}",
        config.get("trading", {}).get("mode"),
        args.mode,
    )

    if args.mode == "backtest":
        run_backtest_mode(config)
    elif args.mode == "paper":
        run_order_mode(config, "paper")
    elif args.mode == "live":
        run_order_mode(config, "live")
    elif args.mode == "scan":
        run_scan_mode(config)
    else:
        logger.error("Unknown mode: {}", args.mode)
        sys.exit(2)


if __name__ == "__main__":
    main()
