"""
fetcher.py — Retrieves latest market prices via yfinance.

Why yfinance: Free, no API key, supports both LSE (.L suffix) and US indices.
Data is ~15 min delayed — acceptable for hourly threshold checks.

LSE market hours guard: VWRA.L only trades 08:00–16:30 London time on weekdays.
Fetching outside those hours returns stale close prices which could produce
false threshold alerts, so callers should respect is_market_open().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

# London Exchange trading window (local London time)
_LSE_OPEN = time(8, 0)
_LSE_CLOSE = time(16, 30)

# Tickers that are on the LSE and require the market hours guard
_LSE_TICKERS = frozenset({"VWRA.L", "VWRP.L"})


@dataclass(frozen=True)
class PriceResult:
    """Immutable result of a single price fetch attempt."""

    ticker: str
    price: Optional[float]
    error: Optional[str]


def is_market_open(ticker: str, now: datetime | None = None) -> bool:
    """
    Returns True if the market for *ticker* is currently open.

    LSE tickers are only active Mon–Fri 08:00–16:30 London time.
    Non-LSE tickers (e.g., ^GSPC) are assumed always fetchable during
    the script's run window (US market hours are not enforced here because
    the script may legitimately run during pre/post-market for index data).

    Args:
        ticker: Yahoo Finance ticker symbol.
        now:    Override current time (for testing). Defaults to now in London tz.
    """
    if ticker not in _LSE_TICKERS:
        return True  # No hours restriction for non-LSE tickers

    import zoneinfo

    london_tz = zoneinfo.ZoneInfo("Europe/London")
    if now is None:
        now = datetime.now(tz=london_tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=london_tz)

    # Saturday=5, Sunday=6
    if now.weekday() >= 5:
        logger.info("LSE closed — weekend ticker=%s weekday=%s", ticker, now.weekday())
        return False

    current_time = now.time().replace(second=0, microsecond=0)
    is_open = _LSE_OPEN <= current_time <= _LSE_CLOSE
    if not is_open:
        logger.info("LSE closed — outside trading hours ticker=%s time=%s", ticker, current_time)
    return is_open


def fetch_prices(tickers: list[str]) -> dict[str, PriceResult]:
    """
    Fetches the latest price for each ticker via yfinance.fast_info.

    Uses fast_info (single lightweight request per ticker) rather than
    history() to minimise latency and API load on Railway's cron budget.

    Args:
        tickers: List of Yahoo Finance ticker symbols.

    Returns:
        Mapping of ticker → PriceResult. Every ticker is guaranteed a key;
        failed fetches have price=None and a non-None error string.
    """
    results: dict[str, PriceResult] = {}

    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            price = info.last_price

            if price is None:
                logger.warning("yfinance returned None price ticker=%s", ticker)
                results[ticker] = PriceResult(ticker=ticker, price=None, error="last_price is None")
            else:
                logger.info("Price fetched ticker=%s price=%s", ticker, price)
                results[ticker] = PriceResult(ticker=ticker, price=float(price), error=None)

        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to fetch price ticker=%s error=%s", ticker, exc)
            results[ticker] = PriceResult(ticker=ticker, price=None, error=str(exc))

    return results
