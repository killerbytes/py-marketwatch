"""
sheet_reader.py — Reads the watchlist from a Google Sheet via a Service Account.

Expected sheet layout (tab named "Watchlist"):
  ticker | name | alert_above | alert_below | enabled
  VWRA.L | VWRA LSE | 120 | 90 | TRUE
  ^GSPC  | S&P 500  | 6000 |    | TRUE

Why Service Account over OAuth: Railway runs headless with no browser;
service account credentials are injected as env vars — no token refresh needed.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from threshold_engine import ThresholdRule

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_WATCHLIST_TAB = "Watchlist"
_REQUIRED_COLUMNS = {"ticker", "name", "alert_above", "alert_below", "enabled"}


def _parse_optional_float(value: str) -> Optional[float]:
    """Return float or None for blank/invalid cells."""
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        logger.warning("Cannot parse threshold value — treating as None value=%s", value)
        return None


def load_watchlist(sheets_id: str, credentials_json: str) -> list[ThresholdRule]:
    """
    Fetches the Watchlist tab from the Google Sheet and returns enabled rules.

    Args:
        sheets_id:        The Google Sheet ID (from the URL).
        credentials_json: Full service account JSON as a string (from env var).

    Returns:
        List of ThresholdRule for every row where enabled=TRUE.

    Raises:
        gspread.exceptions.SpreadsheetNotFound: Sheet not found or not shared.
        KeyError: Required columns are missing from the sheet header.
    """
    creds_dict = json.loads(credentials_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=_SCOPES)
    client = gspread.authorize(creds)

    sheet = client.open_by_key(sheets_id)
    ws = sheet.worksheet(_WATCHLIST_TAB)
    rows = ws.get_all_records(numericise_ignore=["all"])  # Keep all values as strings

    if not rows:
        logger.warning("Watchlist tab is empty sheet_id=%s", sheets_id)
        return []

    # Validate header
    actual_cols = set(rows[0].keys())
    missing = _REQUIRED_COLUMNS - actual_cols
    if missing:
        raise KeyError(f"Google Sheet is missing required columns: {missing}")

    rules: list[ThresholdRule] = []
    for row in rows:
        enabled = str(row.get("enabled", "")).strip().upper()
        if enabled != "TRUE":
            logger.debug("Row disabled — skipping ticker=%s", row.get('ticker'))
            continue

        ticker = str(row["ticker"]).strip()
        name = str(row["name"]).strip()
        alert_above = _parse_optional_float(str(row.get("alert_above", "")))
        alert_below = _parse_optional_float(str(row.get("alert_below", "")))

        if alert_above is None and alert_below is None:
            logger.warning("No thresholds set — skipping row ticker=%s", ticker)
            continue

        rules.append(ThresholdRule(
            ticker=ticker,
            name=name,
            alert_above=alert_above,
            alert_below=alert_below,
        ))
        logger.info("Rule loaded ticker=%s above=%s below=%s", ticker, alert_above, alert_below)

    logger.info("Watchlist loaded count=%s", len(rules))
    return rules
