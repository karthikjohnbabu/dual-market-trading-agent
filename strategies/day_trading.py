"""Intraday day-trading strategy (same-day square-off expected).

Uses the shared momentum signal stack on intraday bars. Positions must be
closed by ``day_trading.square_off_time`` (see ``execution.day_trader``).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from strategies.momentum import MomentumStrategy


class DayTradingStrategy(MomentumStrategy):
    """Momentum-based intraday strategy wrapper for MIS sessions."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialise with full config; reads ``day_trading`` and ``momentum``."""
        super().__init__(config)
        self.day_cfg = config.get("day_trading", {})

    def product(self) -> str:
        """Return the broker product for day trades (default ``MIS``)."""
        return str(self.day_cfg.get("product", "MIS")).upper()

    def timeframe(self) -> str:
        """Return Kite historical interval for intraday bars."""
        return str(self.day_cfg.get("timeframe", "5minute"))

    def summary(self, df: pd.DataFrame) -> dict[str, Any]:
        """Extend momentum summary with day-trading metadata."""
        base = super().summary(df)
        base["style"] = "day_trading"
        base["product"] = self.product()
        base["timeframe"] = self.timeframe()
        return base
