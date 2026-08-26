"""Portfolio risk gates: kill switch, daily loss, position caps, SL/TP."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class OpenPosition:
    """Tracked open long for stop-loss / take-profit evaluation."""

    symbol: str
    quantity: int
    entry_price: float
    opened_on: date = field(default_factory=date.today)


class RiskManager:
    """Pre-trade and intraday risk controls for the dual-market agent."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Load risk limits from ``config['risk']`` and ``config['trading']``."""
        self.config = config
        self.risk = config.get("risk", {})
        self.trading = config.get("trading", {})
        self.capital = float(self.trading.get("capital_inr", 0) or 0)
        self.daily_pnl: float = 0.0
        self.pnl_date: date = date.today()
        self.open_positions: dict[str, OpenPosition] = {}
        self._halted: bool = False
        self._halt_reason: str = ""

    def _reset_day_if_needed(self) -> None:
        """Reset daily PnL book when the calendar day rolls."""
        today = date.today()
        if self.pnl_date != today:
            self.pnl_date = today
            self.daily_pnl = 0.0
            if self._halt_reason.startswith("daily_loss"):
                self._halted = False
                self._halt_reason = ""

    def kill_switch_active(self) -> bool:
        """True if kill-switch file exists (blocks new entries)."""
        rel = str(self.risk.get("kill_switch_file", "logs/KILL_SWITCH"))
        path = PROJECT_ROOT / rel
        return path.exists()

    def open_count(self) -> int:
        """Number of currently tracked open symbols."""
        return sum(1 for p in self.open_positions.values() if p.quantity > 0)

    def status(self) -> dict[str, Any]:
        """Snapshot of risk state for CLI / health checks."""
        self._reset_day_if_needed()
        return {
            "halted": self._halted or self.kill_switch_active(),
            "halt_reason": self._halt_reason
            or ("kill_switch" if self.kill_switch_active() else ""),
            "daily_pnl": round(self.daily_pnl, 2),
            "open_positions": self.open_count(),
            "max_open_positions": int(self.risk.get("max_open_positions", 3)),
            "daily_loss_limit_pct": float(self.risk.get("daily_loss_limit_pct", 2)),
            "stop_loss_pct": float(self.risk.get("stop_loss_pct", 1.0)),
            "take_profit_pct": float(self.risk.get("take_profit_pct", 2.0)),
            "kill_switch": self.kill_switch_active(),
            "capital": self.capital,
        }

    def allow_new_entry(self, symbol: str) -> dict[str, Any]:
        """Gate a new BUY; returns ``{"allowed": bool, "reason": str}``."""
        self._reset_day_if_needed()
        symbol_u = symbol.upper()

        if self.kill_switch_active():
            return {"allowed": False, "reason": "kill_switch file present"}
        if self._halted:
            return {"allowed": False, "reason": self._halt_reason or "halted"}

        max_open = int(self.risk.get("max_open_positions", 3))
        if symbol_u not in self.open_positions and self.open_count() >= max_open:
            return {
                "allowed": False,
                "reason": f"max_open_positions={max_open} reached",
            }

        limit_pct = float(self.risk.get("daily_loss_limit_pct", 2))
        if self.capital > 0:
            day_loss_pct = (-self.daily_pnl / self.capital) * 100.0
            if self.daily_pnl < 0 and day_loss_pct >= limit_pct:
                self._halted = True
                self._halt_reason = f"daily_loss_limit {limit_pct}% hit"
                logger.warning("Risk halt: {}", self._halt_reason)
                return {"allowed": False, "reason": self._halt_reason}

        return {"allowed": True, "reason": "OK"}

    def register_entry(self, symbol: str, quantity: int, entry_price: float) -> None:
        """Track a new long for SL/TP monitoring."""
        self.open_positions[symbol.upper()] = OpenPosition(
            symbol=symbol.upper(),
            quantity=int(quantity),
            entry_price=float(entry_price),
        )

    def register_exit(self, symbol: str, exit_price: float) -> float:
        """Clear tracked position and book PnL; returns realized PnL."""
        self._reset_day_if_needed()
        symbol_u = symbol.upper()
        pos = self.open_positions.pop(symbol_u, None)
        if pos is None or pos.quantity <= 0:
            return 0.0
        pnl = (float(exit_price) - pos.entry_price) * pos.quantity
        self.daily_pnl += pnl
        logger.info(
            "Risk book: {} exit pnl={:.2f} daily_pnl={:.2f}",
            symbol_u,
            pnl,
            self.daily_pnl,
        )
        limit_pct = float(self.risk.get("daily_loss_limit_pct", 2))
        if self.capital > 0 and self.daily_pnl < 0:
            day_loss_pct = (-self.daily_pnl / self.capital) * 100.0
            if day_loss_pct >= limit_pct:
                self._halted = True
                self._halt_reason = f"daily_loss_limit {limit_pct}% hit"
                logger.warning("Risk halt after exit: {}", self._halt_reason)
        return pnl

    def check_stop_take_profit(self, symbol: str, price: float) -> str | None:
        """Return ``STOP_LOSS``, ``TAKE_PROFIT``, or ``None`` for an open long."""
        pos = self.open_positions.get(symbol.upper())
        if pos is None or pos.quantity <= 0 or pos.entry_price <= 0:
            return None
        sl = float(self.risk.get("stop_loss_pct", 1.0))
        tp = float(self.risk.get("take_profit_pct", 2.0))
        change_pct = ((float(price) - pos.entry_price) / pos.entry_price) * 100.0
        if change_pct <= -sl:
            return "STOP_LOSS"
        if change_pct >= tp:
            return "TAKE_PROFIT"
        return None
