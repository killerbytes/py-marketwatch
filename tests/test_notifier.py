"""
Tests for notifier.py — Gmail SMTP email dispatch.
SMTP is fully mocked; no real email is sent during tests.
"""
from __future__ import annotations

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
        gmail_user="sender@gmail.com",
        gmail_app_password="test-app-password",
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
    def test_sends_email_via_smtp_ssl(self, cfg: EmailConfig):
        events = [_event()]
        with patch("notifier.smtplib.SMTP_SSL") as mock_smtp_cls:
            mock_ctx = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_ctx
            send_alert_email(cfg, events)
            mock_ctx.login.assert_called_once_with(cfg.gmail_user, cfg.gmail_app_password)
            mock_ctx.send_message.assert_called_once()

    def test_email_subject_contains_ticker(self, cfg: EmailConfig):
        events = [_event(ticker="VWRA.L", direction="above")]
        with patch("notifier.smtplib.SMTP_SSL") as mock_smtp_cls:
            mock_ctx = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_ctx
            send_alert_email(cfg, events)
            msg = mock_ctx.send_message.call_args[0][0]
            assert "VWRA.L" in msg["Subject"]

    def test_email_contains_all_events(self, cfg: EmailConfig):
        events = [
            _event("VWRA.L", "above", 100.0, 101.5),
            _event("^GSPC", "below", 4500.0, 4400.0),
        ]
        with patch("notifier.smtplib.SMTP_SSL") as mock_smtp_cls:
            mock_ctx = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_ctx
            send_alert_email(cfg, events)
            msg = mock_ctx.send_message.call_args[0][0]
            body = msg.get_payload()
            # Find the HTML part
            html_content = ""
            if isinstance(body, list):
                for part in body:
                    if part.get_content_type() == "text/html":
                        html_content = part.get_payload(decode=True).decode()
            else:
                html_content = body
            assert "VWRA.L" in html_content
            assert "^GSPC" in html_content

    def test_raises_on_smtp_auth_failure(self, cfg: EmailConfig):
        import smtplib
        with patch("notifier.smtplib.SMTP_SSL") as mock_smtp_cls:
            mock_smtp_cls.side_effect = smtplib.SMTPAuthenticationError(535, b"Bad credentials")
            with pytest.raises(smtplib.SMTPAuthenticationError):
                send_alert_email(cfg, [_event()])

    def test_does_not_send_when_events_list_empty(self, cfg: EmailConfig):
        with patch("notifier.smtplib.SMTP_SSL") as mock_smtp_cls:
            send_alert_email(cfg, [])
            mock_smtp_cls.assert_not_called()
