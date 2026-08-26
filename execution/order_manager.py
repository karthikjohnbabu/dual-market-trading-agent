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
from execution.trade_journal import TradeJournal
from indicators.signals import generate_momentum_signals
from risk.manager import RiskManager


class OrderManager:
    """Validate position size and execute momentum signals via Zerodha."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Load config and initialise ZerodhaClient (mode from ``trading.mode``)."""
        self.config = config
        self.client = ZerodhaClient()
        self.client.config = self.config  # CLI may override mode
        self.risk = RiskManager(config)
        self.journal = TradeJournal(config)
        self._default_product = "CNC"
        self._paper_positions: dict[str, int] = {}
        mode = str(self.config.get("trading", {}).get("mode", "paper")).lower()
        logger.info("OrderManager ready (mode={})", mode)

    def set_default_product(self, product: str) -> None:
        """Set default broker product for subsequent orders (``MIS`` or ``CNC``)."""
        self._default_product = product.strip().upper()
        logger.info("Default product set to {}", self._default_product)

    def sleep(self, seconds: int) -> None:
        """Sleep helper used by day/swing loops."""
        time.sleep(seconds)

    def _risk_per_trade_pct(self) -> float:
        """Return max percent of capital allowed per trade."""
        return float(self.config.get("trading", {}).get("risk_per_trade_pct", 1))

    def _trading_mode(self) -> str:
        """Return paper/live mode from config."""
        return str(self.config.get("trading", {}).get("mode", "paper")).lower()

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
        """Return net open quantity for ``symbol`` (paper book or Zerodha)."""
        symbol_u = symbol.upper()
        if self._trading_mode() != "live":
            return int(self._paper_positions.get(symbol_u, 0))

        total = 0
        for pos in self.client.get_positions():
            if str(pos.get("tradingsymbol", "")).upper() == symbol_u:
                total += int(float(pos.get("quantity", 0) or 0))
        return total

    def square_off_paper_positions(self) -> list[dict[str, Any]]:
        """Close all tracked paper positions (day-trade square-off)."""
        results: list[dict[str, Any]] = []
        for symbol, qty in list(self._paper_positions.items()):
            if qty == 0:
                continue
            side = "SELL" if qty > 0 else "BUY"
            abs_qty = abs(qty)
            order_id = self.client.place_order(
                symbol=symbol,
                transaction_type=side,
                quantity=abs_qty,
                product=self._default_product,
            )
            self._paper_positions[symbol] = 0
            self.risk.register_exit(symbol, 0.0)
            results.append(
                {
                    "symbol": symbol,
                    "quantity": abs_qty,
                    "side": side,
                    "order_id": order_id,
                }
            )
            self.journal.record(
                {
                    "action": "SQUARE_OFF",
                    "symbol": symbol,
                    "quantity": abs_qty,
                    "order_id": order_id,
                }
            )
            logger.info("PAPER square-off {}: {} qty={}", symbol, side, abs_qty)
        return results

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
        product = self._default_product

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
            if self._position_quantity(symbol_u) > 0:
                return {
                    "action": "BUY_SKIPPED",
                    "order_id": None,
                    "quantity": 0,
                    "value": 0.0,
                    "reason": f"{symbol_u}: already in position",
                }
            gate = self.risk.allow_new_entry(symbol_u)
            if not gate["allowed"]:
                logger.warning("BUY risk-blocked for {}: {}", symbol_u, gate["reason"])
                self.journal.record(
                    {
                        "action": "BUY_BLOCKED",
                        "symbol": symbol_u,
                        "reason": gate["reason"],
                    }
                )
                return {
                    "action": "BUY_BLOCKED",
                    "order_id": None,
                    "quantity": 0,
                    "value": 0.0,
                    "reason": gate["reason"],
                }
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
            order_id = self.client.place_order(
                symbol_u, "BUY", quantity, product=product
            )
            if self._trading_mode() != "live":
                self._paper_positions[symbol_u] = (
                    self._paper_positions.get(symbol_u, 0) + quantity
                )
            self.risk.register_entry(symbol_u, quantity, current_price)
            result = {
                "action": "BUY",
                "order_id": order_id,
                "quantity": quantity,
                "value": check["order_value"],
                "reason": check["reason"],
                "product": product,
            }
            self.journal.record({"symbol": symbol_u, "price": current_price, **result})
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
            order_id = self.client.place_order(
                symbol_u, "SELL", quantity, product=product
            )
            if self._trading_mode() != "live":
                self._paper_positions[symbol_u] = 0
            pnl = self.risk.register_exit(symbol_u, current_price)
            result = {
                "action": "SELL",
                "order_id": order_id,
                "quantity": quantity,
                "value": order_value,
                "reason": "Full position close",
                "product": product,
                "pnl": round(pnl, 2),
            }
            self.journal.record({"symbol": symbol_u, "price": current_price, **result})
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
