"""
threshold_engine.py — Stateful threshold evaluation with deduplication.

State machine per (ticker, direction) key:
  IDLE      → price breaches threshold → TRIGGERED  (emit AlertEvent)
  TRIGGERED → price still in breach    → TRIGGERED  (no event — dedup)
  TRIGGERED → price returns to safe    → IDLE        (reset)

State is persisted to a JSON file on the Railway Volume between cron runs.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThresholdRule:
    """A single watchlist entry read from the Google Sheet."""

    ticker: str
    name: str
    alert_above: Optional[float]
    alert_below: Optional[float]


@dataclass(frozen=True)
class AlertEvent:
    """Emitted when a threshold is newly crossed (not deduplicated)."""

    ticker: str
    name: str
    direction: str       # "above" | "below"
    threshold: float
    price: float

    @property
    def state_key(self) -> str:
        return f"{self.ticker}:{self.direction}:{self.threshold}"


# State dict shape stored per key:
# { "triggered": bool, "triggered_at": ISO str | None, "trigger_price": float | None }
_StateEntry = dict


class ThresholdEngine:
    """
    Loads and persists alert state, evaluates rules against live prices.

    Args:
        state_file: Absolute path to the JSON state file (Railway Volume mount).
    """

    def __init__(self, state_file: str) -> None:
        self.state_file = state_file
        self._state: dict[str, _StateEntry] = self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, rule: ThresholdRule, price: float) -> list[AlertEvent]:
        """
        Evaluate *rule* against *price*. Returns a list of AlertEvents to send.

        Side-effects: mutates internal state and writes to state_file.

        Args:
            rule:  The threshold rule for a single ticker.
            price: The latest fetched price.

        Returns:
            List of AlertEvent objects (0, 1, or 2 items).
        """
        events: list[AlertEvent] = []

        if rule.alert_above is not None:
            event = self._check_direction(rule, price, "above", rule.alert_above)
            if event:
                events.append(event)

        if rule.alert_below is not None:
            event = self._check_direction(rule, price, "below", rule.alert_below)
            if event:
                events.append(event)

        self._save_state()
        return events

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_direction(
        self,
        rule: ThresholdRule,
        price: float,
        direction: str,
        threshold: float,
    ) -> Optional[AlertEvent]:
        key = f"{rule.ticker}:{direction}:{threshold}"
        entry = self._state.get(key, {"triggered": False, "triggered_at": None, "trigger_price": None})

        is_breach = (
            (direction == "above" and price > threshold)
            or (direction == "below" and price < threshold)
        )

        if is_breach and not entry["triggered"]:
            # Transition: IDLE → TRIGGERED — emit alert
            logger.info("Threshold crossed — alerting key=%s price=%s threshold=%s", key, price, threshold)
            self._state[key] = {
                "triggered": True,
                "triggered_at": datetime.now(tz=timezone.utc).isoformat(),
                "trigger_price": price,
            }
            return AlertEvent(
                ticker=rule.ticker,
                name=rule.name,
                direction=direction,
                threshold=threshold,
                price=price,
            )

        if not is_breach and entry["triggered"]:
            # Transition: TRIGGERED → IDLE — reset so next breach re-alerts
            logger.info("Price returned to safe zone — resetting state key=%s price=%s", key, price)
            self._state[key] = {"triggered": False, "triggered_at": None, "trigger_price": None}

        return None

    def _load_state(self) -> dict[str, _StateEntry]:
        path = Path(self.state_file)
        if not path.exists():
            logger.info("No state file found — starting fresh path=%s", path)
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Corrupted state file — resetting path=%s error=%s", path, exc)
            return {}

    def _save_state(self) -> None:
        path = Path(self.state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        logger.debug("State saved path=%s keys=%s", path, len(self._state))
