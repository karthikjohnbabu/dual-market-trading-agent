"""Append-only JSONL trade journal for audit and post-trade review."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TradeJournal:
    """Persist trade events to ``logs/trades.jsonl`` (configurable)."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Resolve journal path from ``logging.trades_file``."""
        rel = str(
            config.get("logging", {}).get("trades_file", "logs/trades.jsonl")
        )
        self.path = PROJECT_ROOT / rel
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict[str, Any]) -> None:
        """Append one trade/risk event as a JSON line."""
        payload = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            **event,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
        logger.debug("Journaled event action={}", payload.get("action"))

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the last ``limit`` journal events (best-effort)."""
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        rows: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows
