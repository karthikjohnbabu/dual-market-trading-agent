"""Zerodha Kite Connect broker client for NSE equity trading."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from kiteconnect import KiteConnect
from loguru import logger

from data.fetch import load_config

load_dotenv()


class ZerodhaClient:
    """Thin wrapper around Zerodha Kite Connect for positions, holdings, and orders.

    Respects ``trading.mode`` from ``config/settings.yaml``:

    - ``paper``: log orders only; never send to the exchange
    - ``live``: place/cancel real orders (requires explicit live configuration)
    """

    def __init__(self) -> None:
        """Load credentials from ``.env`` and initialise a KiteConnect session.

        Expects ``ZERODHA_API_KEY``, ``ZERODHA_API_SECRET``, and
        ``ZERODHA_ACCESS_TOKEN`` to be set. The API secret is loaded for
        completeness (e.g. future login flows) but the session uses the
        access token.

        Raises:
            ValueError: If required Zerodha environment variables are missing.
        """
        load_dotenv()

        self.api_key = os.getenv("ZERODHA_API_KEY", "").strip()
        self.api_secret = os.getenv("ZERODHA_API_SECRET", "").strip()
        self.access_token = os.getenv("ZERODHA_ACCESS_TOKEN", "").strip()
        self.config = load_config()
        mode = str(self.config.get("trading", {}).get("mode", "paper")).lower()

        missing = [
            name
            for name, value in (
                ("ZERODHA_API_KEY", self.api_key),
                ("ZERODHA_API_SECRET", self.api_secret),
                ("ZERODHA_ACCESS_TOKEN", self.access_token),
            )
            if not value
        ]
        if missing and mode == "live":
            raise ValueError(
                "Missing Zerodha credentials in .env: " + ", ".join(missing)
            )
        if missing:
            logger.warning(
                "Zerodha credentials missing ({}) — paper/simulation only",
                ", ".join(missing),
            )
            self.kite = None  # type: ignore[assignment]
        else:
            self.kite = KiteConnect(api_key=self.api_key)
            self.kite.set_access_token(self.access_token)

        logger.info("ZerodhaClient connected (mode={})", self._trading_mode())

    def _trading_mode(self) -> str:
        """Return the configured trading mode (``paper`` or ``live``)."""
        mode = str(self.config.get("trading", {}).get("mode", "paper")).lower()
        return mode

    def get_positions(self) -> list[dict[str, Any]]:
        """Return current open (net) positions from Zerodha.

        Returns:
            List of position dictionaries from ``kite.positions()['net']``.
            Returns an empty list on failure.
        """
        try:
            if self.kite is None:
                return []
            payload = self.kite.positions()
            positions = list(payload.get("net", []))
            logger.debug("Fetched {} Zerodha net positions", len(positions))
            return positions
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to fetch Zerodha positions: {}", exc)
            return []

    def get_portfolio(self) -> list[dict[str, Any]]:
        """Return current holdings (portfolio) from Zerodha.

        Returns:
            List of holding dictionaries from ``kite.holdings()``.
            Returns an empty list on failure.
        """
        try:
            if self.kite is None:
                return []
            holdings = list(self.kite.holdings())
            logger.debug("Fetched {} Zerodha holdings", len(holdings))
            return holdings
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to fetch Zerodha portfolio/holdings: {}", exc)
            return []

    def _resolve_product(self, product: str | None) -> str:
        """Map a product label to a Kite product constant (CNC or MIS)."""
        label = (product or self.config.get("day_trading", {}).get("product") or "CNC")
        label = str(label).strip().upper()
        if label == "MIS":
            return self.kite.PRODUCT_MIS
        if label == "CNC":
            return self.kite.PRODUCT_CNC
        raise ValueError(f"Unsupported product: {product} (use MIS or CNC)")

    def place_order(
        self,
        symbol: str,
        transaction_type: str,
        quantity: int,
        order_type: str = "MARKET",
        product: str | None = None,
    ) -> str:
        """Place an NSE equity order, or simulate it in paper mode.

        Args:
            symbol: NSE trading symbol (e.g. ``RELIANCE``).
            transaction_type: ``BUY`` or ``SELL``.
            quantity: Number of shares (must be > 0).
            order_type: Order type string (default ``MARKET``).
            product: ``MIS`` (intraday) or ``CNC`` (delivery). Defaults to
                ``day_trading.product`` from config, else ``CNC``.

        Returns:
            Zerodha ``order_id`` in live mode, or ``PAPER_ORDER_SIMULATED``
            in paper mode.

        Raises:
            ValueError: If ``quantity <= 0`` or ``transaction_type`` is invalid,
                or if live mode is requested with an unsupported order type.
        """
        if quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {quantity}")

        txn = transaction_type.strip().upper()
        if txn not in {"BUY", "SELL"}:
            raise ValueError("transaction_type must be 'BUY' or 'SELL'")

        order_type_norm = order_type.strip().upper()
        product_label = (
            product
            or self.config.get("day_trading", {}).get("product")
            or "CNC"
        )
        product_label = str(product_label).strip().upper()
        mode = self._trading_mode()

        if mode != "live":
            logger.info(
                "PAPER order (not sent): symbol={} side={} qty={} type={} "
                "product={} mode={}",
                symbol.upper(),
                txn,
                quantity,
                order_type_norm,
                product_label,
                mode,
            )
            return "PAPER_ORDER_SIMULATED"

        if self.kite is None:
            raise RuntimeError("Kite client not initialised — cannot place live orders")

        kite_order_type = {
            "MARKET": self.kite.ORDER_TYPE_MARKET,
            "LIMIT": self.kite.ORDER_TYPE_LIMIT,
        }.get(order_type_norm)
        if kite_order_type is None:
            raise ValueError(f"Unsupported order_type: {order_type}")

        kite_product = self._resolve_product(product_label)
        order_id = self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=self.kite.EXCHANGE_NSE,
            tradingsymbol=symbol.upper(),
            transaction_type=txn,
            quantity=int(quantity),
            order_type=kite_order_type,
            product=kite_product,
        )
        logger.info(
            "LIVE order placed: order_id={} symbol={} side={} qty={} "
            "type={} product={}",
            order_id,
            symbol.upper(),
            txn,
            quantity,
            order_type_norm,
            product_label,
        )
        return str(order_id)

    def square_off_mis_positions(self) -> list[dict[str, Any]]:
        """Market-sell all open MIS (intraday) net positions.

        Returns:
            List of result dicts per symbol (``symbol``, ``quantity``, ``order_id``).
        """
        results: list[dict[str, Any]] = []
        for pos in self.get_positions():
            product = str(pos.get("product", "")).upper()
            qty = int(float(pos.get("quantity", 0) or 0))
            symbol = str(pos.get("tradingsymbol", "")).upper()
            if product != "MIS" or qty == 0 or not symbol:
                continue
            side = "SELL" if qty > 0 else "BUY"
            abs_qty = abs(qty)
            try:
                order_id = self.place_order(
                    symbol=symbol,
                    transaction_type=side,
                    quantity=abs_qty,
                    product="MIS",
                )
                results.append(
                    {
                        "symbol": symbol,
                        "quantity": abs_qty,
                        "side": side,
                        "order_id": order_id,
                    }
                )
                logger.info(
                    "Squared off MIS {}: {} qty={} order_id={}",
                    symbol,
                    side,
                    abs_qty,
                    order_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Square-off failed for {}: {}", symbol, exc)
                results.append(
                    {
                        "symbol": symbol,
                        "quantity": abs_qty,
                        "side": side,
                        "order_id": None,
                        "error": str(exc),
                    }
                )
        return results

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a live order. No-op in paper mode.

        Args:
            order_id: Zerodha order id to cancel.

        Returns:
            ``True`` if cancel succeeded (or paper no-op), ``False`` on failure.
        """
        mode = self._trading_mode()
        if mode != "live":
            logger.info(
                "PAPER cancel (no-op): order_id={} mode={}",
                order_id,
                mode,
            )
            return True

        if self.kite is None:
            logger.error("Cannot cancel — Kite client not initialised")
            return False

        try:
            self.kite.cancel_order(
                variety=self.kite.VARIETY_REGULAR,
                order_id=order_id,
            )
            logger.info("Cancelled live order_id={}", order_id)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to cancel order_id={}: {}", order_id, exc)
            return False
