"""Single CLI entry point for the dual-market trading agent.

Usage::

    python main.py --mode backtest
    python main.py --mode paper
    python main.py --mode live
    python main.py --mode daytrade
    python main.py --mode scan
    python main.py --mode health
    python main.py --mode status
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
from execution.day_trader import DayTrader
from execution.order_manager import OrderManager
from execution.trade_journal import TradeJournal
from indicators.signals import generate_momentum_signals
from ops.health import format_health_report, run_health_check
from risk.manager import RiskManager


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the top-level ``--mode`` argument."""
    parser = argparse.ArgumentParser(
        description="Dual-market trading agent (Zerodha India + eToro/UK scan)",
    )
    parser.add_argument(
        "--mode",
        choices=(
            "backtest",
            "paper",
            "live",
            "daytrade",
            "scan",
            "health",
            "status",
        ),
        required=True,
        help="backtest | paper | live | daytrade | scan | health | status",
    )
    return parser.parse_args(argv)


def run_backtest_mode(config: dict[str, Any]) -> None:
    """Run full India + UK backtests and print the summary table."""
    logger.info("Mode=backtest — running engine for all configured symbols")
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
    logger.info("Mode={} — starting OrderManager loop", mode)

    if mode == "live":
        if cfg.get("risk", {}).get("require_paper_before_live", True):
            logger.warning(
                "LIVE MODE: real orders may be sent. "
                "Confirm paper results first. Ctrl+C to stop."
            )
        logger.warning("LIVE MODE enabled — Ctrl+C to stop.")

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
    logger.info("Mode=scan — momentum signals for configured symbols")
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
        print(" | ".join(str(row.get(h, "")).ljust(widths[h]) for h in headers))
    print()

    buys = [r["symbol"] for r in rows if r.get("signal") == "BUY"]
    sells = [r["symbol"] for r in rows if r.get("signal") == "SELL"]
    logger.info("Scan complete — BUY: {} | SELL: {}", buys or "none", sells or "none")


def run_daytrade_mode(config: dict[str, Any]) -> None:
    """Run intraday MIS day-trading with same-day square-off (paper by default)."""
    cfg = copy.deepcopy(config)
    cfg.setdefault("trading", {})["style"] = "day_trading"
    if str(cfg.get("trading", {}).get("mode", "paper")).lower() == "live":
        logger.warning(
            "LIVE daytrade: real MIS orders + forced square-off. "
            "Paper trade first unless you explicitly intend live."
        )
    else:
        cfg.setdefault("trading", {})["mode"] = "paper"
        logger.info("Daytrade running in PAPER mode (MIS simulated + square-off)")

    trader = DayTrader(cfg)
    try:
        trader.run()
    except KeyboardInterrupt:
        logger.info("DayTrader stopped by user — attempting final square-off")
        trader.square_off_all()


def run_health_mode() -> None:
    """Print readiness checks (config, kill switch, risk limits, creds)."""
    report = run_health_check()
    print(format_health_report(report))
    if not report.get("overall_ok"):
        sys.exit(1)


def run_status_mode(config: dict[str, Any]) -> None:
    """Print risk snapshot and recent journal events."""
    risk = RiskManager(config)
    journal = TradeJournal(config)
    status = risk.status()
    print("Risk status")
    print("-" * 40)
    for key, value in status.items():
        print(f"{key}: {value}")
    print()
    print("Recent trades (journal)")
    print("-" * 40)
    recent = journal.recent(10)
    if not recent:
        print("(no trades logged yet)")
    else:
        for row in recent:
            print(
                f"{row.get('ts', '')} | {row.get('action')} | "
                f"{row.get('symbol')} | qty={row.get('quantity')} | "
                f"pnl={row.get('pnl', '-')}"
            )


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
    elif args.mode == "daytrade":
        run_daytrade_mode(config)
    elif args.mode == "scan":
        run_scan_mode(config)
    elif args.mode == "health":
        run_health_mode()
    elif args.mode == "status":
        run_status_mode(config)
    else:
        logger.error("Unknown mode: {}", args.mode)
        sys.exit(2)


if __name__ == "__main__":
    main()
