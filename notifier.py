"""
notifier.py — Sends HTML alert emails via Gmail SMTP over SSL.

Why Gmail SMTP + App Password: Zero cost, no third-party service,
works reliably from Railway without additional env vars or API keys beyond
what the user already has.

App Password setup: Google Account → Security → 2-Step Verification → App passwords.
"""
from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from threshold_engine import AlertEvent

logger = logging.getLogger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 465  # SSL


@dataclass(frozen=True)
class EmailConfig:
    """Gmail SMTP credentials and recipient address."""

    gmail_user: str
    gmail_app_password: str
    alert_email_to: str


def _build_html(events: list[AlertEvent]) -> str:
    """Renders a styled HTML email body listing all triggered alerts."""
    rows = ""
    for e in events:
        direction_label = "📈 ABOVE" if e.direction == "above" else "📉 BELOW"
        direction_color = "#16a34a" if e.direction == "above" else "#dc2626"
        rows += f"""
        <tr>
          <td style="padding:12px 16px;border-bottom:1px solid #1e293b;font-weight:600;color:#f1f5f9;">
            {e.ticker}
          </td>
          <td style="padding:12px 16px;border-bottom:1px solid #1e293b;color:#94a3b8;">{e.name}</td>
          <td style="padding:12px 16px;border-bottom:1px solid #1e293b;">
            <span style="color:{direction_color};font-weight:700;">{direction_label} {e.threshold:,.2f}</span>
          </td>
          <td style="padding:12px 16px;border-bottom:1px solid #1e293b;color:#f1f5f9;font-weight:600;">
            {e.price:,.2f}
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>MarketWatch Alert</title>
</head>
<body style="margin:0;padding:0;background:#0f172a;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;padding:32px 16px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0"
               style="background:#1e293b;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.4);">
          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#6366f1,#0ea5e9);padding:28px 32px;">
              <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.3px;">
                📊 MarketWatch Alert
              </h1>
              <p style="margin:6px 0 0;color:rgba(255,255,255,0.8);font-size:14px;">
                {len(events)} threshold{"s" if len(events) != 1 else ""} triggered
              </p>
            </td>
          </tr>
          <!-- Table -->
          <tr>
            <td style="padding:24px 32px;">
              <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
                <thead>
                  <tr style="background:#0f172a;">
                    <th style="padding:10px 16px;text-align:left;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.8px;">Ticker</th>
                    <th style="padding:10px 16px;text-align:left;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.8px;">Name</th>
                    <th style="padding:10px 16px;text-align:left;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.8px;">Threshold</th>
                    <th style="padding:10px 16px;text-align:left;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.8px;">Current Price</th>
                  </tr>
                </thead>
                <tbody>
                  {rows}
                </tbody>
              </table>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding:16px 32px 28px;color:#475569;font-size:12px;border-top:1px solid #1e293b;">
              Prices are ~15 minutes delayed. This alert will not repeat until the price
              returns to a safe range and breaches again. Edit thresholds in your Google Sheet.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_alert_email(cfg: EmailConfig, events: list[AlertEvent]) -> None:
    """
    Sends a single HTML email summarising all triggered events.

    No-op if *events* is empty (guard against unnecessary SMTP connections).

    Args:
        cfg:    Gmail SMTP credentials and recipient.
        events: Non-empty list of AlertEvent objects to include in the email.

    Raises:
        smtplib.SMTPAuthenticationError: If credentials are wrong.
        smtplib.SMTPException:           For other SMTP failures.
    """
    if not events:
        logger.debug("No events — skipping email")
        return

    tickers = ", ".join(e.ticker for e in events)
    subject = f"⚠️ MarketWatch: {len(events)} alert{'s' if len(events) != 1 else ''} — {tickers}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.gmail_user
    msg["To"] = cfg.alert_email_to
    msg.attach(MIMEText(_build_html(events), "html", "utf-8"))

    logger.info("Sending alert email to=%s events=%s", cfg.alert_email_to, len(events))
    with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT) as server:
        server.login(cfg.gmail_user, cfg.gmail_app_password)
        server.send_message(msg)
    logger.info("Email sent successfully subject=%s", subject)
