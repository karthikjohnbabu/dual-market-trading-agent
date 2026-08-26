"""Intraday day-trader: auto BUY/SELL on MIS with same-day square-off.

Runnable via::

    python main.py --mode daytrade
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from data.fetch import fetch_india_data
from execution.order_manager import OrderManager
from strategies.day_trading import DayTradingStrategy

IST = ZoneInfo("Asia/Kolkata")


def _parse_hhmm(value: str) -> time:
    """Parse ``HH:MM`` into a ``datetime.time``."""
    hour, minute = value.strip().split(":")
    return time(int(hour), int(minute))


class DayTrader:
    """Run momentum day-trading with forced MIS square-off before close."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Create order manager + day-trading strategy from config."""
        self.config = config
        self.day_cfg = config.get("day_trading", {})
        self.strategy = DayTradingStrategy(config)
        self.manager = OrderManager(config)
        self.manager.set_default_product(self.strategy.product())
        self._squared_off_date: date | None = None

    def _now_ist(self) -> datetime:
        """Current time in Asia/Kolkata."""
        return datetime.now(tz=IST)

    def _within_session(self, now: datetime) -> bool:
        """True if ``now`` is between market open and market close (IST clock)."""
        open_t = _parse_hhmm(str(self.day_cfg.get("market_open", "09:15")))
        close_t = _parse_hhmm(str(self.day_cfg.get("market_close", "15:30")))
        now_t = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
        return open_t <= now_t <= close_t

    def _past_square_off(self, now: datetime) -> bool:
        """True if at or after configured square-off time."""
        so = _parse_hhmm(str(self.day_cfg.get("square_off_time", "15:15")))
        now_t = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
        return now_t >= so

    def _allow_new_entries(self, now: datetime) -> bool:
        """True if new BUY entries are still allowed."""
        cutoff = _parse_hhmm(str(self.day_cfg.get("no_new_entries_after", "14:45")))
        now_t = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
        return now_t < cutoff

    def square_off_all(self) -> list[dict[str, Any]]:
        """Force-close all MIS / paper day positions."""
        logger.warning("DAY TRADE square-off — closing all open positions")
        mode = str(self.config.get("trading", {}).get("mode", "paper")).lower()
        if mode != "live":
            results = self.manager.square_off_paper_positions()
        else:
            results = self.manager.client.square_off_mis_positions()
        self._squared_off_date = self._now_ist().date()
        return results

    def run(self) -> None:
        """Main intraday loop until interrupted."""
        symbols = list(self.config.get("india", {}).get("symbols", []))
        capital = float(self.config.get("trading", {}).get("capital_inr", 0))
        interval = self.strategy.timeframe()
        lookback = int(self.day_cfg.get("lookback_days", 5))
        poll = int(self.day_cfg.get("poll_seconds", 60))
        mode = str(self.config.get("trading", {}).get("mode", "paper")).lower()

        logger.info(
            "DayTrader start mode={} product={} interval={} symbols={} "
            "square_off={}",
            mode,
            self.strategy.product(),
            interval,
            symbols,
            self.day_cfg.get("square_off_time", "15:15"),
        )
        if mode == "live":
            logger.warning(
                "LIVE day-trading: real MIS orders may be sent. Ctrl+C to stop."
            )

        while True:
            now = self._now_ist()
            logger.info("=== Day cycle {} IST ===", now.strftime("%Y-%m-%d %H:%M:%S"))

            if not self._within_session(now):
                logger.info("Outside market hours — sleeping {}s", poll)
                self.manager.sleep(poll)
                continue

            if self._past_square_off(now):
                if self._squared_off_date != now.date():
                    results = self.square_off_all()
                    logger.info("Square-off results: {}", results)
                else:
                    logger.info("Already squared off today — waiting for session end")
                self.manager.sleep(poll)
                continue

            to_date = now.date()
            from_date = to_date - timedelta(days=lookback)
            allow_buy = self._allow_new_entries(now)

            for symbol in symbols:
                try:
                    df = fetch_india_data(
                        symbol, from_date, to_date, interval=interval
                    )
                    if df.empty:
                        logger.warning("{}: no intraday data — skip", symbol)
                        continue

                    signaled = self.strategy.run(df)
                    last = signaled.iloc[-1]
                    signal = str(last.get("signal", "HOLD"))
                    price = float(last.get("close", 0.0))

                    # Hard exits beat discretionary signals.
                    stop_reason = self.manager.risk.check_stop_take_profit(
                        symbol, price
                    )
                    if stop_reason:
                        logger.warning(
                            "{}: {} triggered @ {:.2f} — forcing SELL",
                            symbol,
                            stop_reason,
                            price,
                        )
                        signal = "SELL"

                    if signal == "BUY" and not allow_buy:
                        logger.info(
                            "{}: BUY suppressed after no_new_entries_after",
                            symbol,
                        )
                        signal = "HOLD"

                    if self.manager.risk.kill_switch_active() and signal == "BUY":
                        logger.warning("{}: kill switch — BUY blocked", symbol)
                        signal = "HOLD"

                    logger.info(
                        "{}: signal={} close={:.2f} rsi={}",
                        symbol,
                        signal,
                        price,
                        last.get("rsi"),
                    )
                    result = self.manager.execute_signal(
                        symbol, signal, price, capital
                    )
                    logger.info("{}: result={}", symbol, result)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("{}: day cycle error: {}", symbol, exc)

            self.manager.sleep(poll)
