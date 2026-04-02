"""
reminders/emailer.py — SMTP transport for TN811 reminder emails.

Handles:
- SMTP connection with TLS
- Multipart plain+HTML message assembly
- Dry-run mode (writes preview to disk instead of sending)
- Logging of send events

All email content is rendered by reminders/templates.py.
"""
from __future__ import annotations

import json
import logging
import smtplib
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from tn811.config import RemindersConfig
from tn811.models import NormalizedTicket
from tn811.reminders.templates import render_html, render_plain_text, render_subject

logger = logging.getLogger(__name__)


class ReminderEmailer:
    """
    Sends (or previews) reminder emails for expiring relevant tickets.

    Usage:
        emailer = ReminderEmailer(config.reminders, preview_dir)
        result = emailer.send(tickets, target_expiration, dry_run=False)
    """

    def __init__(self, config: RemindersConfig, preview_dir: str) -> None:
        self._config = config
        self._preview_dir = Path(preview_dir)

    def send(
        self,
        tickets: list[NormalizedTicket],
        target_expiration: date,
        dry_run: bool = False,
    ) -> "SendResult":
        """
        Send a batched reminder email for the given tickets.

        Args:
            tickets:           Eligible tickets to include in the email.
            target_expiration: The expiration date they all share.
            dry_run:           If True, write preview to disk instead of sending.

        Returns:
            SendResult with success flag and details.
        """
        if not tickets:
            logger.info("No tickets for reminder — skipping send")
            return SendResult(sent=False, dry_run=dry_run, reason="no_tickets")

        recipients = self._config.recipients
        if not recipients:
            logger.warning("No reminder recipients configured — skipping send")
            return SendResult(sent=False, dry_run=dry_run, reason="no_recipients")

        lead_days = self._config.lead_days
        subject = render_subject(len(tickets), lead_days)
        plain = render_plain_text(tickets, target_expiration, lead_days)
        html = render_html(tickets, target_expiration, lead_days)

        if dry_run:
            return self._write_preview(subject, plain, html, recipients, target_expiration)

        return self._send_smtp(subject, plain, html, recipients)

    def _send_smtp(
        self,
        subject: str,
        plain: str,
        html: str,
        recipients: list[str],
    ) -> "SendResult":
        smtp_cfg = self._config.smtp
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{smtp_cfg.from_name} <{smtp_cfg.from_address}>"
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        try:
            if smtp_cfg.use_tls:
                server = smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=30)
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                server = smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=30)

            if smtp_cfg.user and smtp_cfg.password:
                server.login(smtp_cfg.user, smtp_cfg.password)

            server.sendmail(smtp_cfg.from_address, recipients, msg.as_string())
            server.quit()

            logger.info(
                "Reminder email sent",
                extra={"recipients": recipients, "subject": subject},
            )
            return SendResult(sent=True, dry_run=False, recipients=recipients)

        except smtplib.SMTPException as exc:
            logger.error("SMTP error sending reminder", exc_info=exc)
            return SendResult(sent=False, dry_run=False, reason=str(exc))

    def _write_preview(
        self,
        subject: str,
        plain: str,
        html: str,
        recipients: list[str],
        target_expiration: date,
    ) -> "SendResult":
        """Write email preview files to disk (dry-run mode)."""
        self._preview_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        stem = f"reminder_{target_expiration.isoformat()}_{ts}"

        (self._preview_dir / f"{stem}.txt").write_text(
            f"TO: {', '.join(recipients)}\nSUBJECT: {subject}\n\n{plain}",
            encoding="utf-8",
        )
        (self._preview_dir / f"{stem}.html").write_text(html, encoding="utf-8")
        (self._preview_dir / f"{stem}_meta.json").write_text(
            json.dumps({
                "subject": subject,
                "recipients": recipients,
                "target_expiration": target_expiration.isoformat(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "dry_run": True,
            }, indent=2),
            encoding="utf-8",
        )

        logger.info(
            "Dry-run: reminder preview written",
            extra={"dir": str(self._preview_dir), "stem": stem},
        )
        return SendResult(
            sent=False,
            dry_run=True,
            reason="dry_run",
            preview_path=str(self._preview_dir / f"{stem}.txt"),
            recipients=recipients,
        )


from dataclasses import dataclass, field as dc_field


@dataclass
class SendResult:
    sent: bool
    dry_run: bool
    reason: str = ""
    recipients: list[str] = dc_field(default_factory=list)
    preview_path: str | None = None
