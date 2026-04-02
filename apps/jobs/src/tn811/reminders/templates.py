"""
reminders/templates.py — Email content templates for TN811 reminders.

Uses Python string formatting (no Jinja2 dependency at runtime).
All formatting logic lives here; emailer.py only handles SMTP transport.
"""
from __future__ import annotations

from datetime import date

from tn811.models import NormalizedTicket


def render_subject(ticket_count: int, lead_days: int) -> str:
    """Render the email subject line."""
    noun = "ticket" if ticket_count == 1 else "tickets"
    return f"TN811 reminder: {ticket_count} relevant {noun} expire in {lead_days} days"


def render_plain_text(
    tickets: list[NormalizedTicket],
    target_expiration: date,
    lead_days: int,
) -> str:
    """Render a plain-text reminder email body."""
    lines: list[str] = []

    lines.append("=" * 60)
    lines.append("TN811 TICKET EXPIRY REMINDER")
    lines.append("=" * 60)
    lines.append(f"")
    lines.append(
        f"The following {len(tickets)} GFiber-related ticket(s) "
        f"expire on {target_expiration.strftime('%B %d, %Y')} "
        f"({lead_days} days from today)."
    )
    lines.append("")
    lines.append(
        "Please renew any tickets that are still needed before they expire."
    )
    lines.append("")
    lines.append("-" * 60)

    # Group by probable work group for readability
    groups: dict[str, list[NormalizedTicket]] = {}
    for t in tickets:
        group = t.probable_work_group or t.probable_company or "Unassigned"
        groups.setdefault(group, []).append(t)

    for group_name, group_tickets in sorted(groups.items()):
        lines.append(f"")
        lines.append(f"Work Group: {group_name}")
        lines.append("-" * 40)

        for t in group_tickets:
            lines.append(f"")
            lines.append(f"  Ticket:      {t.ticket_number}")
            lines.append(f"  County:      {t.county}")
            lines.append(f"  Expires:     {t.expiration_date}")
            lines.append(f"  Company:     {t.excavator_name or 'N/A'}")
            lines.append(f"  Work Type:   {t.work_type or 'N/A'}")
            if t.location_text:
                loc = t.location_text[:120]
                lines.append(f"  Location:    {loc}")
            lines.append(f"  Relevance:   {t.relevance_score:.2f}")

    lines.append("")
    lines.append("-" * 60)
    lines.append("This is an automated reminder from TN811 Monitor.")
    lines.append("Renew only if the work is still ongoing.")
    lines.append("")

    return "\n".join(lines)


def render_html(
    tickets: list[NormalizedTicket],
    target_expiration: date,
    lead_days: int,
) -> str:
    """Render an HTML reminder email body."""
    rows_html = ""

    groups: dict[str, list[NormalizedTicket]] = {}
    for t in tickets:
        group = t.probable_work_group or t.probable_company or "Unassigned"
        groups.setdefault(group, []).append(t)

    for group_name, group_tickets in sorted(groups.items()):
        rows_html += f"""
        <tr style="background:#f0f4ff;">
          <td colspan="6" style="padding:8px 12px;font-weight:bold;color:#1a56db;">
            Work Group: {_esc(group_name)}
          </td>
        </tr>"""
        for t in group_tickets:
            loc = (t.location_text or "")[:80]
            rows_html += f"""
        <tr>
          <td style="padding:6px 12px;font-family:monospace;">{_esc(t.ticket_number)}</td>
          <td style="padding:6px 12px;">{_esc(t.county)}</td>
          <td style="padding:6px 12px;">{_esc(str(t.expiration_date))}</td>
          <td style="padding:6px 12px;">{_esc(t.excavator_name or "N/A")}</td>
          <td style="padding:6px 12px;">{_esc(t.work_type or "N/A")}</td>
          <td style="padding:6px 12px;font-size:12px;color:#555;">{_esc(loc)}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;color:#222;max-width:900px;margin:0 auto;">
  <h2 style="color:#1a56db;">TN811 Ticket Expiry Reminder</h2>
  <p>
    The following <strong>{len(tickets)}</strong> GFiber-related ticket(s) expire on
    <strong>{target_expiration.strftime('%B %d, %Y')}</strong>
    ({lead_days} days from today).
  </p>
  <p>Please renew any tickets that are still needed before they expire.</p>
  <table border="0" cellspacing="0" cellpadding="0"
         style="border-collapse:collapse;width:100%;border:1px solid #ddd;">
    <thead>
      <tr style="background:#1a56db;color:#fff;">
        <th style="padding:8px 12px;text-align:left;">Ticket #</th>
        <th style="padding:8px 12px;text-align:left;">County</th>
        <th style="padding:8px 12px;text-align:left;">Expires</th>
        <th style="padding:8px 12px;text-align:left;">Company</th>
        <th style="padding:8px 12px;text-align:left;">Work Type</th>
        <th style="padding:8px 12px;text-align:left;">Location</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
  <p style="margin-top:24px;font-size:12px;color:#888;">
    This is an automated reminder from TN811 Monitor. Renew only if work is still ongoing.
  </p>
</body>
</html>"""


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
