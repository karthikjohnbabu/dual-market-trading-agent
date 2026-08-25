"""Market data fetchers for India (Zerodha) and UK/US (Alpha Vantage)."""

from __future__ import annotations

import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from loguru import logger

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def load_config(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load application settings from ``config/settings.yaml``.

    Args:
        config_path: Optional path to a YAML settings file. Defaults to
            ``config/settings.yaml`` under the project root.

    Returns:
        Parsed configuration as a dictionary.

    Raises:
        FileNotFoundError: If the settings file does not exist.
        yaml.YAMLError: If the file cannot be parsed.
    """
    path = Path(config_path) if config_path is not None else CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    if not isinstance(config, dict):
        raise ValueError(f"Expected a mapping in {path}, got {type(config).__name__}")

    logger.debug("Loaded config from {}", path)
    return config


def _empty_ohlcv() -> pd.DataFrame:
    """Return an empty OHLCV DataFrame with the standard column schema."""
    return pd.DataFrame(columns=OHLCV_COLUMNS)


def _normalize_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a DataFrame to the standard OHLCV column schema and types."""
    if frame.empty:
        return _empty_ohlcv()

    result = frame[OHLCV_COLUMNS].copy()
    result["date"] = pd.to_datetime(result["date"])
    for col in ("open", "high", "low", "close"):
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result["volume"] = pd.to_numeric(result["volume"], errors="coerce").fillna(0).astype("int64")
    result = result.dropna(subset=["open", "high", "low", "close"])
    result = result.sort_values("date").reset_index(drop=True)
    return result


def _parse_date(value: date | datetime | str) -> date:
    """Convert a date-like value to a ``datetime.date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _resolve_nse_instrument_token(kite: KiteConnect, symbol: str) -> int:
    """Resolve an NSE equity trading symbol to a Kite instrument token.

    Args:
        kite: Authenticated KiteConnect client.
        symbol: NSE trading symbol (e.g. ``RELIANCE``).

    Returns:
        Instrument token for historical data requests.

    Raises:
        ValueError: If the symbol cannot be found on NSE.
    """
    instruments = kite.instruments("NSE")
    symbol_upper = symbol.upper()
    for instrument in instruments:
        if (
            instrument.get("tradingsymbol") == symbol_upper
            and instrument.get("instrument_type") == "EQ"
            and instrument.get("segment") == "NSE"
        ):
            return int(instrument["instrument_token"])

    raise ValueError(f"NSE equity instrument not found for symbol: {symbol}")


def fetch_india_data(
    symbol: str,
    from_date: date | datetime | str,
    to_date: date | datetime | str,
    interval: str = "day",
) -> pd.DataFrame:
    """Fetch OHLCV data for an NSE symbol via Zerodha Kite Connect.

    Reads ``ZERODHA_API_KEY`` and ``ZERODHA_ACCESS_TOKEN`` from the environment
    (loaded from ``.env`` via python-dotenv).

    Args:
        symbol: NSE trading symbol (e.g. ``RELIANCE``, ``INFY``).
        from_date: Start date (inclusive).
        to_date: End date (inclusive).
        interval: Kite historical interval (default ``day``).

    Returns:
        DataFrame with columns: ``date``, ``open``, ``high``, ``low``,
        ``close``, ``volume``. Returns an empty DataFrame on failure.
    """
    api_key = os.getenv("ZERODHA_API_KEY")
    access_token = os.getenv("ZERODHA_ACCESS_TOKEN")

    if not api_key or not access_token:
        logger.error(
            "Missing Zerodha credentials. Set ZERODHA_API_KEY and "
            "ZERODHA_ACCESS_TOKEN in .env"
        )
        return _empty_ohlcv()

    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

        start = _parse_date(from_date)
        end = _parse_date(to_date)
        instrument_token = _resolve_nse_instrument_token(kite, symbol)

        records = kite.historical_data(
            instrument_token=instrument_token,
            from_date=start,
            to_date=end,
            interval=interval,
        )
        if not records:
            logger.warning("No India data returned for {} ({} → {})", symbol, start, end)
            return _empty_ohlcv()

        frame = pd.DataFrame(records)
        # Kite uses "date" for the candle timestamp.
        if "date" not in frame.columns and "datetime" in frame.columns:
            frame = frame.rename(columns={"datetime": "date"})

        result = _normalize_ohlcv(frame)
        logger.info(
            "Fetched {} India candles for {} ({} → {}, interval={})",
            len(result),
            symbol.upper(),
            start,
            end,
            interval,
        )
        return result

    except Exception as exc:  # noqa: BLE001 — surface broker/network failures via logger
        logger.exception("Failed to fetch India data for {}: {}", symbol, exc)
        return _empty_ohlcv()


def fetch_uk_data(
    symbol: str,
    from_date: date | datetime | str,
    to_date: date | datetime | str,
) -> pd.DataFrame:
    """Fetch daily OHLCV data via Alpha Vantage (free tier).

    Suitable for UK/US symbols traded on eToro watchlists. Reads
    ``ALPHA_VANTAGE_API_KEY`` from ``.env``. Sleeps 12 seconds to respect the
    free-tier limit of approximately 5 calls per minute.

    Args:
        symbol: Ticker symbol (e.g. ``TSLA``, ``AAPL``).
        from_date: Start date (inclusive); used to filter the series.
        to_date: End date (inclusive); used to filter the series.

    Returns:
        DataFrame with columns: ``date``, ``open``, ``high``, ``low``,
        ``close``, ``volume``. Returns an empty DataFrame on failure.
    """
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        logger.error("Missing ALPHA_VANTAGE_API_KEY in .env")
        return _empty_ohlcv()

    # Free tier ≈ 5 calls/min — wait between requests.
    logger.debug("Rate-limit pause (12s) before Alpha Vantage request for {}", symbol)
    time.sleep(12)

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol.upper(),
        "outputsize": "full",
        "apikey": api_key,
    }

    try:
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        payload = response.json()

        if "Note" in payload or "Information" in payload:
            message = payload.get("Note") or payload.get("Information")
            logger.warning("Alpha Vantage rate/info message for {}: {}", symbol, message)
            return _empty_ohlcv()

        if "Error Message" in payload:
            logger.error("Alpha Vantage error for {}: {}", symbol, payload["Error Message"])
            return _empty_ohlcv()

        series = payload.get("Time Series (Daily)")
        if not series:
            logger.warning("No daily time series in Alpha Vantage response for {}", symbol)
            return _empty_ohlcv()

        rows: list[dict[str, Any]] = []
        for day_str, values in series.items():
            rows.append(
                {
                    "date": day_str,
                    "open": values["1. open"],
                    "high": values["2. high"],
                    "low": values["3. low"],
                    "close": values["4. close"],
                    "volume": values["5. volume"],
                }
            )

        frame = pd.DataFrame(rows)
        result = _normalize_ohlcv(frame)

        start = pd.Timestamp(_parse_date(from_date))
        end = pd.Timestamp(_parse_date(to_date))
        result = result[(result["date"] >= start) & (result["date"] <= end)].reset_index(drop=True)

        logger.info(
            "Fetched {} UK/US candles for {} ({} → {})",
            len(result),
            symbol.upper(),
            start.date(),
            end.date(),
        )
        return result

    except Exception as exc:  # noqa: BLE001 — surface API/network failures via logger
        logger.exception("Failed to fetch UK/US data for {}: {}", symbol, exc)
        return _empty_ohlcv()
