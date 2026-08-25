"""FastMCP server exposing Zerodha trading actions as Cursor agent tools.

Run with::

    python -m mcp_servers.zerodha_mcp
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP
from loguru import logger

# Ensure project root is importable when launched as a script/stdio MCP process.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from brokers.zerodha import ZerodhaClient  # noqa: E402
from data.fetch import (  # noqa: E402
    fetch_india_data,
    fetch_uk_data,
    load_config,
)
from indicators.signals import generate_momentum_signals  # noqa: E402
from strategies.momentum import backtest_momentum  # noqa: E402

load_dotenv()

# Keep loguru off stdout so it does not corrupt MCP stdio JSON-RPC.
logger.remove()
logger.add(sys.stderr, level="INFO")

mcp = FastMCP("zerodha-trading")

_client: ZerodhaClient | None = None


def _get_client() -> ZerodhaClient:
    """Return a lazily initialised ``ZerodhaClient`` singleton."""
    global _client
    if _client is None:
        _client = ZerodhaClient()
    return _client


def _format_records(records: list[dict[str, Any]], keys: list[str]) -> str:
    """Format a list of dict records as a simple pipe-separated table."""
    if not records:
        return "(none)"

    header = " | ".join(keys)
    lines = [header, "-+-".join("-" * len(k) for k in keys)]
    for record in records:
        lines.append(" | ".join(str(record.get(k, "")) for k in keys))
    return "\n".join(lines)


def _fetch_symbol_data(
    symbol: str,
    market: str,
    days: int,
) -> Any:
    """Fetch OHLCV for ``symbol`` over the last ``days`` calendar days."""
    to_date = date.today()
    from_date = to_date - timedelta(days=days)
    market_norm = market.strip().lower()

    if market_norm == "india":
        return fetch_india_data(symbol, from_date, to_date, interval="day")
    if market_norm == "uk":
        return fetch_uk_data(symbol, from_date, to_date)
    raise ValueError("market must be 'india' or 'uk'")


def _scan_symbol(symbol: str, market: str, days: int, config: dict[str, Any]) -> dict[str, Any]:
    """Run momentum signals for one symbol and return a summary row."""
    df = _fetch_symbol_data(symbol, market, days)
    if df is None or df.empty:
        return {
            "symbol": symbol,
            "last_signal": "NO_DATA",
            "last_close": "",
            "rsi": "",
            "ema_fast": "",
            "ema_slow": "",
        }

    signaled = generate_momentum_signals(df, config)
    last = signaled.iloc[-1]
    momentum = config["momentum"]
    fast = int(momentum["ema_fast"])
    slow = int(momentum["ema_slow"])

    def _num(value: Any) -> str:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return ""

    return {
        "symbol": symbol,
        "last_signal": str(last.get("signal", "HOLD")),
        "last_close": _num(last.get("close")),
        "rsi": _num(last.get("rsi")),
        "ema_fast": _num(last.get(f"ema_{fast}")),
        "ema_slow": _num(last.get(f"ema_{slow}")),
    }


@mcp.tool
def get_positions() -> str:
    """Get all current open positions from Zerodha."""
    positions = _get_client().get_positions()
    if not positions:
        return "No open positions."

    keys = ["tradingsymbol", "quantity", "average_price", "last_price", "pnl", "product"]
    present = [k for k in keys if any(k in p for p in positions)]
    return _format_records(positions, present or list(positions[0].keys())[:6])


@mcp.tool
def get_portfolio() -> str:
    """Get current Zerodha holdings/portfolio."""
    holdings = _get_client().get_portfolio()
    if not holdings:
        return "No holdings in portfolio."

    keys = ["tradingsymbol", "quantity", "average_price", "last_price", "pnl"]
    present = [k for k in keys if any(k in h for h in holdings)]
    return _format_records(holdings, present or list(holdings[0].keys())[:6])


@mcp.tool
def run_momentum_scan(symbols: list[str], market: str = "india") -> str:
    """Run momentum strategy scan on given symbols and return BUY/SELL/HOLD signals."""
    if not symbols:
        return "No symbols provided."

    config = load_config()
    rows = [_scan_symbol(symbol, market, days=90, config=config) for symbol in symbols]
    keys = ["symbol", "last_signal", "last_close", "rsi", "ema_fast", "ema_slow"]
    return _format_records(rows, keys)


@mcp.tool
def run_backtest(symbol: str, market: str = "india", days: int = 365) -> str:
    """Run a backtest on a symbol and return performance metrics."""
    config = load_config()
    market_norm = market.strip().lower()
    cfg = dict(config)
    if market_norm == "uk":
        trading = dict(config.get("trading", {}))
        trading.pop("capital_inr", None)
        cfg = {**config, "trading": trading}

    df = _fetch_symbol_data(symbol, market_norm, days)
    if df is None or df.empty:
        return f"No data for {symbol} ({market_norm})."

    results = backtest_momentum(df, cfg)
    drawdown = float(results.get("max_drawdown_pct", 0.0))
    risk_flag = str(results.get("risk_flag", ""))
    if drawdown > 15 and not risk_flag:
        risk_flag = "HIGH RISK"

    return (
        f"symbol={symbol}\n"
        f"market={market_norm}\n"
        f"total_return_pct={results.get('total_return_pct')}\n"
        f"max_drawdown_pct={results.get('max_drawdown_pct')}\n"
        f"win_rate={results.get('win_rate')}\n"
        f"total_trades={results.get('total_trades')}\n"
        f"risk_flag={risk_flag or 'OK'}"
    )


@mcp.tool
def place_order(symbol: str, transaction_type: str, quantity: int) -> str:
    """Place a BUY or SELL order. Always paper mode unless explicitly set to live in config."""
    order_id = _get_client().place_order(
        symbol=symbol,
        transaction_type=transaction_type,
        quantity=quantity,
    )
    mode = load_config().get("trading", {}).get("mode", "paper")
    return (
        f"order_id={order_id}\n"
        f"symbol={symbol.upper()}\n"
        f"transaction_type={transaction_type.upper()}\n"
        f"quantity={quantity}\n"
        f"mode={mode}"
    )


@mcp.tool
def get_market_summary() -> str:
    """Get a summary of all configured symbols — current signals and positions."""
    config = load_config()
    symbols = list(config.get("india", {}).get("symbols", []))
    if not symbols:
        return "No India symbols configured in settings.yaml."

    rows = [_scan_symbol(symbol, "india", days=90, config=config) for symbol in symbols]

    position_symbols: set[str] = set()
    try:
        for pos in _get_client().get_positions():
            qty = float(pos.get("quantity", 0) or 0)
            if qty != 0:
                position_symbols.add(str(pos.get("tradingsymbol", "")).upper())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load positions for market summary: {}", exc)

    for row in rows:
        sym = str(row["symbol"]).upper()
        row["in_position"] = "YES" if sym in position_symbols else "NO"

    keys = [
        "symbol",
        "last_signal",
        "last_close",
        "rsi",
        "ema_fast",
        "ema_slow",
        "in_position",
    ]
    return _format_records(rows, keys)


def main() -> None:
    """Start the Zerodha FastMCP server over stdio."""
    logger.info("Starting zerodha-trading MCP server (stdio)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
