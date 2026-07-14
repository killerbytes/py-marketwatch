"""
Tests for notifier.py — Resend API email dispatch.
"""
from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from notifier import EmailConfig, send_alert_email
from threshold_engine import AlertEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg() -> EmailConfig:
    return EmailConfig(
        resend_api_key="re_testkey",
        alert_email_to="recipient@example.com",
    )


def _event(ticker: str = "VWRA.L", direction: str = "above", threshold: float = 100.0, price: float = 101.0) -> AlertEvent:
    return AlertEvent(
        ticker=ticker,
        name="VWRA LSE",
        direction=direction,
        threshold=threshold,
        price=price,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSendAlertEmail:
    def test_sends_email_via_resend(self, cfg: EmailConfig):
        events = [_event()]
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_urlopen.return_value.__enter__.return_value = mock_response
            send_alert_email(cfg, events)
            mock_urlopen.assert_called_once()
            
            # verify request properties
            req = mock_urlopen.call_args[0][0]
            assert req.full_url == "https://api.resend.com/emails"
            assert req.headers["Authorization"] == "Bearer re_testkey"
            assert req.headers["Content-type"] == "application/json"
            
            # verify payload structure
            data = json.loads(req.data.decode("utf-8"))
            assert data["to"] == ["recipient@example.com"]
            assert "MarketWatch" in data["from"]
            assert "VWRA.L" in data["subject"]

    def test_email_subject_contains_ticker(self, cfg: EmailConfig):
        events = [_event(ticker="VWRA.L", direction="above")]
        with patch("urllib.request.urlopen") as mock_urlopen:
            send_alert_email(cfg, events)
            req = mock_urlopen.call_args[0][0]
            data = json.loads(req.data.decode("utf-8"))
            assert "VWRA.L" in data["subject"]

    def test_email_contains_all_events(self, cfg: EmailConfig):
        events = [
            _event("VWRA.L", "above", 100.0, 101.5),
            _event("^GSPC", "below", 4500.0, 4400.0),
        ]
        with patch("urllib.request.urlopen") as mock_urlopen:
            send_alert_email(cfg, events)
            req = mock_urlopen.call_args[0][0]
            data = json.loads(req.data.decode("utf-8"))
            html_content = data["html"]
            assert "VWRA.L" in html_content
            assert "^GSPC" in html_content

    def test_raises_on_resend_api_failure(self, cfg: EmailConfig):
        fp = BytesIO(b"Unauthorized API Key")
        err = urllib.error.HTTPError(
            url="https://api.resend.com/emails",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=fp
        )
        
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError) as exc_info:
                send_alert_email(cfg, [_event()])
            assert "Resend API error: 401 Unauthorized API Key" in str(exc_info.value)

    def test_does_not_send_when_events_list_empty(self, cfg: EmailConfig):
        with patch("urllib.request.urlopen") as mock_urlopen:
            send_alert_email(cfg, [])
            mock_urlopen.assert_not_called()

