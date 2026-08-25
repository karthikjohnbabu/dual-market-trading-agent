"""Order validation and signal execution for Zerodha (paper / live).

Runnable as::

    python -m execution.order_manager --mode paper
    python -m execution.order_manager --mode live
"""

from __future__ import annotations

import argparse
import copy
import sys
import time
from datetime import date, timedelta
from typing import Any

from loguru import logger

from brokers.zerodha import ZerodhaClient
from data.fetch import fetch_india_data, load_config
from indicators.signals import generate_momentum_signals


class OrderManager:
    """Validate position size and execute momentum signals via Zerodha."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Load config and initialise ZerodhaClient (mode from ``trading.mode``)."""
        self.config = config
        self.client = ZerodhaClient()
        self.client.config = self.config  # CLI may override mode
        mode = str(self.config.get("trading", {}).get("mode", "paper")).lower()
        logger.info("OrderManager ready (mode={})", mode)

    def _risk_per_trade_pct(self) -> float:
        """Return max percent of capital allowed per trade."""
        return float(self.config.get("trading", {}).get("risk_per_trade_pct", 1))

    def validate_order(
        self,
        symbol: str,
        quantity: int,
        price: float,
        capital: float,
    ) -> dict[str, Any]:
        """Validate quantity and that order value stays within risk_per_trade_pct.

        Returns:
            Dict with ``valid``, ``reason``, and ``order_value``.
        """
        order_value = float(quantity) * float(price)
        risk_pct = self._risk_per_trade_pct()
        max_value = float(capital) * (risk_pct / 100.0)

        if quantity <= 0:
            return {
                "valid": False,
                "reason": f"{symbol}: quantity must be > 0 (got {quantity})",
                "order_value": order_value,
            }
        if price <= 0 or capital <= 0:
            return {
                "valid": False,
                "reason": f"{symbol}: price and capital must be > 0",
                "order_value": order_value,
            }
        if order_value > max_value + 1e-9:
            return {
                "valid": False,
                "reason": (
                    f"{symbol}: order_value {order_value:.2f} exceeds "
                    f"{risk_pct}% of capital ({max_value:.2f})"
                ),
                "order_value": order_value,
            }
        return {"valid": True, "reason": "OK", "order_value": order_value}

    def _position_quantity(self, symbol: str) -> int:
        """Return net open quantity for ``symbol`` from Zerodha positions."""
        symbol_u = symbol.upper()
        total = 0
        for pos in self.client.get_positions():
            if str(pos.get("tradingsymbol", "")).upper() == symbol_u:
                total += int(float(pos.get("quantity", 0) or 0))
        return total

    def execute_signal(
        self,
        symbol: str,
        signal: str,
        current_price: float,
        capital: float,
    ) -> dict[str, Any]:
        """Execute BUY (risk-sized), SELL (full position), or HOLD.

        Returns:
            Dict with ``action``, ``order_id``, ``quantity``, ``value``, ``reason``.
        """
        signal_u = signal.strip().upper()
        symbol_u = symbol.upper()

        if signal_u == "HOLD":
            logger.info("{}: HOLD — no trade", symbol_u)
            return {
                "action": "HOLD",
                "order_id": None,
                "quantity": 0,
                "value": 0.0,
                "reason": "No trade",
            }

        if signal_u == "BUY":
            risk_pct = self._risk_per_trade_pct()
            notional = float(capital) * (risk_pct / 100.0)
            quantity = int(notional // float(current_price)) if current_price > 0 else 0
            check = self.validate_order(symbol_u, quantity, current_price, capital)
            if not check["valid"]:
                logger.warning("BUY blocked for {}: {}", symbol_u, check["reason"])
                return {
                    "action": "BUY_REJECTED",
                    "order_id": None,
                    "quantity": quantity,
                    "value": check["order_value"],
                    "reason": check["reason"],
                }
            order_id = self.client.place_order(symbol_u, "BUY", quantity)
            result = {
                "action": "BUY",
                "order_id": order_id,
                "quantity": quantity,
                "value": check["order_value"],
                "reason": check["reason"],
            }
            logger.info("BUY executed: {}", result)
            return result

        if signal_u == "SELL":
            quantity = self._position_quantity(symbol_u)
            if quantity <= 0:
                reason = f"{symbol_u}: no open position to sell"
                logger.info(reason)
                return {
                    "action": "SELL_SKIPPED",
                    "order_id": None,
                    "quantity": 0,
                    "value": 0.0,
                    "reason": reason,
                }
            order_value = quantity * float(current_price)
            order_id = self.client.place_order(symbol_u, "SELL", quantity)
            result = {
                "action": "SELL",
                "order_id": order_id,
                "quantity": quantity,
                "value": order_value,
                "reason": "Full position close",
            }
            logger.info("SELL executed: {}", result)
            return result

        reason = f"Unknown signal: {signal}"
        logger.warning(reason)
        return {
            "action": "ERROR",
            "order_id": None,
            "quantity": 0,
            "value": 0.0,
            "reason": reason,
        }

    def run_live(self) -> None:
        """Main loop: scan India symbols, execute last signal, sleep 60s."""
        symbols = list(self.config.get("india", {}).get("symbols", []))
        capital = float(self.config.get("trading", {}).get("capital_inr", 0))
        mode = str(self.config.get("trading", {}).get("mode", "paper")).lower()

        logger.info(
            "Starting order loop mode={} symbols={} capital_inr={}",
            mode,
            symbols,
            capital,
        )
        if mode == "live":
            logger.warning(
                "LIVE mode enabled — real orders may be sent. Ctrl+C to stop."
            )

        while True:
            cycle_start = time.strftime("%Y-%m-%d %H:%M:%S")
            logger.info("=== Cycle start {} ===", cycle_start)
            to_date = date.today()
            from_date = to_date - timedelta(days=90)

            for symbol in symbols:
                try:
                    df = fetch_india_data(symbol, from_date, to_date, interval="day")
                    if df.empty:
                        logger.warning("{}: no data — skipping", symbol)
                        continue

                    signaled = generate_momentum_signals(df, self.config)
                    last = signaled.iloc[-1]
                    signal = str(last.get("signal", "HOLD"))
                    price = float(last.get("close", 0.0))

                    logger.info(
                        "{}: last_signal={} close={:.2f} rsi={}",
                        symbol,
                        signal,
                        price,
                        last.get("rsi"),
                    )
                    result = self.execute_signal(symbol, signal, price, capital)
                    logger.info("{}: execute result={}", symbol, result)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("{}: cycle error: {}", symbol, exc)

            logger.info("Cycle complete — sleeping 60 seconds")
            time.sleep(60)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for trading mode."""
    parser = argparse.ArgumentParser(
        description="Dual-market order manager (Zerodha India)",
    )
    parser.add_argument(
        "--mode",
        choices=("paper", "live"),
        default=None,
        help="Trading mode (overrides config/settings.yaml trading.mode)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point: load config, apply --mode, run the order loop."""
    args = parse_args(argv)
    config = copy.deepcopy(load_config())

    if args.mode is not None:
        config.setdefault("trading", {})["mode"] = args.mode
        logger.info("CLI override: trading.mode={}", args.mode)

    mode = str(config.get("trading", {}).get("mode", "paper")).lower()
    if mode == "live":
        logger.warning(
            "You requested LIVE trading. Paper trade first unless you intend real orders."
        )

    manager = OrderManager(config)
    try:
        manager.run_live()
    except KeyboardInterrupt:
        logger.info("OrderManager stopped by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
