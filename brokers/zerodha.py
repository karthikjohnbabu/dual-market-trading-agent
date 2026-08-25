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

        missing = [
            name
            for name, value in (
                ("ZERODHA_API_KEY", self.api_key),
                ("ZERODHA_API_SECRET", self.api_secret),
                ("ZERODHA_ACCESS_TOKEN", self.access_token),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Missing Zerodha credentials in .env: " + ", ".join(missing)
            )

        self.config = load_config()
        self.kite = KiteConnect(api_key=self.api_key)
        self.kite.set_access_token(self.access_token)
        logger.info(
            "ZerodhaClient connected (mode={})",
            self._trading_mode(),
        )

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
            holdings = list(self.kite.holdings())
            logger.debug("Fetched {} Zerodha holdings", len(holdings))
            return holdings
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to fetch Zerodha portfolio/holdings: {}", exc)
            return []

    def place_order(
        self,
        symbol: str,
        transaction_type: str,
        quantity: int,
        order_type: str = "MARKET",
    ) -> str:
        """Place an NSE equity order, or simulate it in paper mode.

        Args:
            symbol: NSE trading symbol (e.g. ``RELIANCE``).
            transaction_type: ``BUY`` or ``SELL``.
            quantity: Number of shares (must be > 0).
            order_type: Order type string (default ``MARKET``).

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
        mode = self._trading_mode()

        if mode != "live":
            logger.info(
                "PAPER order (not sent): symbol={} side={} qty={} type={} mode={}",
                symbol.upper(),
                txn,
                quantity,
                order_type_norm,
                mode,
            )
            return "PAPER_ORDER_SIMULATED"

        kite_order_type = {
            "MARKET": self.kite.ORDER_TYPE_MARKET,
            "LIMIT": self.kite.ORDER_TYPE_LIMIT,
        }.get(order_type_norm)
        if kite_order_type is None:
            raise ValueError(f"Unsupported order_type: {order_type}")

        order_id = self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=self.kite.EXCHANGE_NSE,
            tradingsymbol=symbol.upper(),
            transaction_type=txn,
            quantity=int(quantity),
            order_type=kite_order_type,
            product=self.kite.PRODUCT_CNC,
        )
        logger.info(
            "LIVE order placed: order_id={} symbol={} side={} qty={} type={}",
            order_id,
            symbol.upper(),
            txn,
            quantity,
            order_type_norm,
        )
        return str(order_id)

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
