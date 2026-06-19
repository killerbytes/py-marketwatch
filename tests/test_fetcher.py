"""
Tests for fetcher.py — market price retrieval via yfinance.
All network calls are mocked; no real API calls made during tests.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fetcher import PriceResult, fetch_prices, is_market_open


# ---------------------------------------------------------------------------
# is_market_open
# ---------------------------------------------------------------------------

class TestIsMarketOpen:
    """LSE hours: Mon–Fri 08:00–16:30 London time."""

    def test_returns_true_during_lse_hours(self):
        # Tuesday 12:00 UTC+1 (BST) → 11:00 UTC — within window
        from datetime import datetime, timezone
        import zoneinfo

        london = zoneinfo.ZoneInfo("Europe/London")
        dt = datetime(2026, 6, 16, 12, 0, tzinfo=london)  # Tuesday
        assert is_market_open("VWRA.L", dt) is True

    def test_returns_false_outside_lse_hours(self):
        from datetime import datetime
        import zoneinfo

        london = zoneinfo.ZoneInfo("Europe/London")
        dt = datetime(2026, 6, 16, 18, 0, tzinfo=london)  # After 16:30
        assert is_market_open("VWRA.L", dt) is False

    def test_returns_false_on_weekend(self):
        from datetime import datetime
        import zoneinfo

        london = zoneinfo.ZoneInfo("Europe/London")
        dt = datetime(2026, 6, 21, 12, 0, tzinfo=london)  # Sunday
        assert is_market_open("VWRA.L", dt) is False

    def test_sp500_always_considered_open_on_weekday(self):
        """S&P500 market hours check is skipped (US hours not enforced)."""
        from datetime import datetime
        import zoneinfo

        london = zoneinfo.ZoneInfo("Europe/London")
        dt = datetime(2026, 6, 16, 12, 0, tzinfo=london)
        assert is_market_open("^GSPC", dt) is True


# ---------------------------------------------------------------------------
# fetch_prices
# ---------------------------------------------------------------------------

class TestFetchPrices:
    def _make_ticker_mock(self, price: float) -> MagicMock:
        mock = MagicMock()
        mock.fast_info.last_price = price
        return mock

    def test_returns_price_dict_for_valid_tickers(self):
        tickers = ["VWRA.L", "^GSPC"]
        with patch("fetcher.yf.Ticker") as mock_ticker_cls:
            mock_ticker_cls.side_effect = [
                self._make_ticker_mock(100.5),
                self._make_ticker_mock(5300.0),
            ]
            results = fetch_prices(tickers)

        assert len(results) == 2
        assert results["VWRA.L"] == PriceResult(ticker="VWRA.L", price=100.5, error=None)
        assert results["^GSPC"] == PriceResult(ticker="^GSPC", price=5300.0, error=None)

    def test_records_error_on_none_price(self):
        with patch("fetcher.yf.Ticker") as mock_ticker_cls:
            mock = MagicMock()
            mock.fast_info.last_price = None
            mock_ticker_cls.return_value = mock
            results = fetch_prices(["VWRA.L"])

        assert results["VWRA.L"].price is None
        assert results["VWRA.L"].error is not None

    def test_records_error_on_exception(self):
        with patch("fetcher.yf.Ticker") as mock_ticker_cls:
            mock_ticker_cls.side_effect = RuntimeError("network error")
            results = fetch_prices(["VWRA.L"])

        assert results["VWRA.L"].price is None
        assert "network error" in results["VWRA.L"].error

    def test_one_failure_does_not_abort_others(self):
        with patch("fetcher.yf.Ticker") as mock_ticker_cls:
            bad = MagicMock()
            bad.fast_info.last_price = None
            good = self._make_ticker_mock(5300.0)
            mock_ticker_cls.side_effect = [bad, good]
            results = fetch_prices(["VWRA.L", "^GSPC"])

        assert results["VWRA.L"].price is None
        assert results["^GSPC"].price == 5300.0
