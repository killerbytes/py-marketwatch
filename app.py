"""
app.py — MarketWatch entry point.

Orchestrates: load config → fetch prices → evaluate thresholds → send alerts.
Designed to run as a Railway Cron Job (schedule: 0 * * * *).

Environment variables required (set in Railway dashboard):
  RESEND_API_KEY, ALERT_EMAIL_TO
  GOOGLE_SHEETS_ID, GOOGLE_CREDENTIALS_JSON
  STATE_FILE_PATH  (default: /data/state.json — Railway Volume mount)
"""
from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()  # Loads .env locally; env vars from Railway override when deployed

# ---------------------------------------------------------------------------
# Logging — structured, low-cardinality messages suitable for Railway log drain
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from fetcher import PriceResult, fetch_prices, is_market_open  # noqa: E402
from notifier import EmailConfig, send_alert_email  # noqa: E402
from sheet_reader import load_watchlist  # noqa: E402
from threshold_engine import ThresholdEngine  # noqa: E402


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _require_env(key: str) -> str:
    """Return env var value or exit with a clear error (fail-fast on deploy)."""
    value = os.getenv(key)
    if not value:
        logger.error("Required environment variable is missing env_var=%s", key)
        sys.exit(1)
    return value


def _build_email_config() -> EmailConfig:
    return EmailConfig(
        resend_api_key=_require_env("RESEND_API_KEY"),
        alert_email_to=_require_env("ALERT_EMAIL_TO"),
    )


def _load_google_credentials() -> str:
    """
    Resolves Google service account credentials.

    Precedence:
      1. GOOGLE_CREDENTIALS_FILE — path to service-account.json (local dev)
      2. GOOGLE_CREDENTIALS_JSON — inline JSON string (Railway env var)

    Why two options: python-dotenv cannot parse multi-line values, so storing
    a pretty-printed JSON blob in .env fails silently. For local dev, point at
    the file directly. Railway's dashboard accepts the single-line JSON string.
    """
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE")
    if creds_file:
        import pathlib
        path = pathlib.Path(creds_file)
        if not path.exists():
            logger.error("Credentials file not found path=%s", creds_file)
            sys.exit(1)
        logger.info("Loading Google credentials from file path=%s", creds_file)
        return path.read_text(encoding="utf-8")

    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        logger.error("Neither GOOGLE_CREDENTIALS_FILE nor GOOGLE_CREDENTIALS_JSON is set")
        sys.exit(1)
    logger.info("Loading Google credentials from GOOGLE_CREDENTIALS_JSON env var")
    return creds_json


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Single execution pass: fetch → evaluate → notify.

    Exits 0 on success, 1 on unrecoverable config error.
    Individual ticker fetch failures are logged but do not abort the run.
    """
    logger.info("MarketWatch run starting")

    # 1. Load config
    sheets_id = _require_env("GOOGLE_SHEETS_ID")
    credentials_json = _load_google_credentials()
    state_file = os.getenv("STATE_FILE_PATH", "./state.json")
    email_cfg = _build_email_config()

    # 2. Load watchlist from Google Sheet
    try:
        rules = load_watchlist(sheets_id, credentials_json)
    except Exception as exc:
        logger.error(
            "Failed to load watchlist from Google Sheets — %s: %s",
            type(exc).__name__,
            exc or "(no message — check the sheet is shared with the service account email)",
        )
        sys.exit(1)

    if not rules:
        logger.info("Watchlist is empty or all rows disabled — nothing to do")
        return

    # 3. Filter to tickers whose markets are currently open
    open_rules = [r for r in rules if is_market_open(r.ticker)]
    skipped = len(rules) - len(open_rules)
    if skipped:
        logger.info("Rules skipped — market closed skipped=%s", skipped)
    if not open_rules:
        logger.info("No open markets right now — exiting")
        return

    # 4. Fetch prices (best-effort per ticker)
    tickers = [r.ticker for r in open_rules]
    prices: dict[str, PriceResult] = fetch_prices(tickers)

    # 5. Evaluate thresholds
    engine = ThresholdEngine(state_file=state_file)
    all_events = []

    for rule in open_rules:
        result = prices.get(rule.ticker)
        if result is None or result.price is None:
            err = result.error if result else "missing"
            logger.warning("No price — skipping threshold check ticker=%s error=%s", rule.ticker, err)
            continue

        events = engine.evaluate(rule, result.price)
        all_events.extend(events)

    logger.info("Threshold evaluation complete total_events=%s", len(all_events))

    # 6. Send consolidated email (one email per run, even for multiple alerts)
    if all_events:
        try:
            send_alert_email(email_cfg, all_events)
        except Exception as exc:
            logger.error("Failed to send alert email error=%s", exc)
            sys.exit(1)
    else:
        logger.info("No thresholds triggered — no email sent")

    logger.info("MarketWatch run complete")


if __name__ == "__main__":
    main()
