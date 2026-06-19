"""
Tests for threshold_engine.py — stateful threshold evaluation.

State machine transitions verified:
  - IDLE  + breach   → TRIGGERED (email sent)
  - TRIGGERED + breach  → TRIGGERED (no email — dedup)
  - TRIGGERED + safe    → IDLE     (reset, ready to re-alert)
  - IDLE  + safe    → IDLE     (no-op)
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from threshold_engine import AlertEvent, ThresholdEngine, ThresholdRule


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_state_file(tmp_path: Path) -> str:
    return str(tmp_path / "state.json")


@pytest.fixture
def engine(tmp_state_file: str) -> ThresholdEngine:
    return ThresholdEngine(state_file=tmp_state_file)


def _rule(ticker: str, above: float | None = None, below: float | None = None) -> ThresholdRule:
    return ThresholdRule(ticker=ticker, name=ticker, alert_above=above, alert_below=below)


# ---------------------------------------------------------------------------
# Above-threshold tests
# ---------------------------------------------------------------------------

class TestAboveThreshold:
    def test_triggers_alert_when_price_crosses_above(self, engine: ThresholdEngine):
        rule = _rule("VWRA.L", above=100.0)
        events = engine.evaluate(rule, price=101.0)
        assert len(events) == 1
        assert events[0].direction == "above"
        assert events[0].price == 101.0

    def test_no_duplicate_alert_when_still_above(self, engine: ThresholdEngine):
        rule = _rule("VWRA.L", above=100.0)
        engine.evaluate(rule, price=101.0)   # First — triggers
        events = engine.evaluate(rule, price=102.0)  # Still above — dedup
        assert events == []

    def test_resets_after_price_returns_below(self, engine: ThresholdEngine):
        rule = _rule("VWRA.L", above=100.0)
        engine.evaluate(rule, price=101.0)  # Trigger
        engine.evaluate(rule, price=99.0)   # Reset
        events = engine.evaluate(rule, price=101.5)  # Should re-trigger
        assert len(events) == 1

    def test_no_alert_when_price_safely_below(self, engine: ThresholdEngine):
        rule = _rule("VWRA.L", above=100.0)
        events = engine.evaluate(rule, price=99.0)
        assert events == []


# ---------------------------------------------------------------------------
# Below-threshold tests
# ---------------------------------------------------------------------------

class TestBelowThreshold:
    def test_triggers_alert_when_price_drops_below(self, engine: ThresholdEngine):
        rule = _rule("VWRA.L", below=90.0)
        events = engine.evaluate(rule, price=89.0)
        assert len(events) == 1
        assert events[0].direction == "below"

    def test_no_duplicate_alert_when_still_below(self, engine: ThresholdEngine):
        rule = _rule("VWRA.L", below=90.0)
        engine.evaluate(rule, price=89.0)
        events = engine.evaluate(rule, price=88.0)
        assert events == []

    def test_resets_after_price_recovers_above(self, engine: ThresholdEngine):
        rule = _rule("VWRA.L", below=90.0)
        engine.evaluate(rule, price=89.0)  # Trigger
        engine.evaluate(rule, price=91.0)  # Reset
        events = engine.evaluate(rule, price=88.0)  # Re-trigger
        assert len(events) == 1


# ---------------------------------------------------------------------------
# Both thresholds set simultaneously
# ---------------------------------------------------------------------------

class TestBothThresholds:
    def test_both_can_trigger_independently(self, engine: ThresholdEngine):
        rule = _rule("^GSPC", above=6000.0, below=4500.0)

        above_events = engine.evaluate(rule, price=6100.0)
        assert len(above_events) == 1
        assert above_events[0].direction == "above"

        # Reset above, check below independently
        engine2 = ThresholdEngine(state_file=engine.state_file)
        below_events = engine2.evaluate(rule, price=4400.0)
        assert len(below_events) == 1
        assert below_events[0].direction == "below"


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

class TestStatePersistence:
    def test_state_persists_between_engine_instances(self, tmp_state_file: str):
        rule = _rule("VWRA.L", above=100.0)

        engine1 = ThresholdEngine(state_file=tmp_state_file)
        engine1.evaluate(rule, price=101.0)  # Trigger and save

        engine2 = ThresholdEngine(state_file=tmp_state_file)
        events = engine2.evaluate(rule, price=102.0)  # Should NOT re-trigger
        assert events == []

    def test_handles_missing_state_file_gracefully(self, tmp_state_file: str):
        assert not Path(tmp_state_file).exists()
        engine = ThresholdEngine(state_file=tmp_state_file)
        rule = _rule("VWRA.L", above=100.0)
        # Should not raise
        events = engine.evaluate(rule, price=101.0)
        assert len(events) == 1

    def test_handles_corrupted_state_file(self, tmp_state_file: str):
        Path(tmp_state_file).write_text("not valid json")
        engine = ThresholdEngine(state_file=tmp_state_file)
        rule = _rule("VWRA.L", above=100.0)
        events = engine.evaluate(rule, price=101.0)
        assert len(events) == 1  # Recovers gracefully
