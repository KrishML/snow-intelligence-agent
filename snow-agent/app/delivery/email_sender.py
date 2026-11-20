"""SendGrid email reporting and failure notifications."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.settings import settings

logger = logging.getLogger(__name__)
_TEMPLATE_DIR = Path(__file__).parent / "templates"


class SendGridDelivery:
    def __init__(self, client_factory=SendGridAPIClient) -> None:
        self._client_factory = client_factory
        self._templates = Environment(
            loader=FileSystemLoader(_TEMPLATE_DIR),
            autoescape=select_autoescape(("html", "xml")),
        )
        self.last_error: str | None = None

    async def send_weekly_report(
        self,
        top5: list[dict[str, Any]],
        run_id: uuid.UUID,
        recipient: str | None = None,
    ) -> bool:
        self.last_error = None
        recipient = recipient or settings.delivery_email_address
        if not settings.sendgrid_api_key or not recipient:
            return self._failed("SendGrid API key or recipient is not configured.")
        try:
            template = self._templates.get_template("weekly_report.html")
            html = template.render(run_id=run_id, resorts=top5)
            mail = Mail(
                from_email=settings.sendgrid_from_email,
                to_emails=recipient,
                subject="Your weekly Snow Intelligence report",
                html_content=html,
            )
            response = await asyncio.to_thread(self._send, mail)
            if not 200 <= response.status_code < 300:
                return self._failed(f"SendGrid returned HTTP {response.status_code}.")
            return True
        except Exception as exc:
            logger.exception("SendGrid weekly report failed")
            return self._failed(str(exc))

    async def send_failure_notification(
        self,
        failed_components: dict[str, str],
        run_id: uuid.UUID,
        recipient: str | None = None,
    ) -> bool:
        self.last_error = None
        recipient = recipient or settings.delivery_email_address
        if not settings.sendgrid_api_key or not recipient:
            return self._failed("SendGrid API key or recipient is not configured.")
        try:
            rows = "".join(
                f"<li><strong>{_escape(component)}</strong>: {_escape(reason)}</li>"
                for component, reason in failed_components.items()
            )
            html = (
                "<h1>Weekly Snow Run failure or partial failure</h1>"
                f"<p>Run: {_escape(str(run_id))}</p><ul>{rows}</ul>"
            )
            mail = Mail(
                from_email=settings.sendgrid_from_email,
                to_emails=recipient,
                subject="Snow Intelligence weekly run needs attention",
                html_content=html,
            )
            response = await asyncio.to_thread(self._send, mail)
            if not 200 <= response.status_code < 300:
                return self._failed(f"SendGrid returned HTTP {response.status_code}.")
            return True
        except Exception as exc:
            logger.exception("SendGrid failure notification failed")
            return self._failed(str(exc))

    async def send_html(
        self, subject: str, html: str, recipient: str | None = None
    ) -> bool:
        self.last_error = None
        recipient = recipient or settings.delivery_email_address
        if not settings.sendgrid_api_key or not recipient:
            return self._failed("SendGrid API key or recipient is not configured.")
        try:
            mail = Mail(
                from_email=settings.sendgrid_from_email,
                to_emails=recipient,
                subject=subject,
                html_content=html,
            )
            response = await asyncio.to_thread(self._send, mail)
            if not 200 <= response.status_code < 300:
                return self._failed(f"SendGrid returned HTTP {response.status_code}.")
            return True
        except Exception as exc:
            logger.exception("SendGrid message failed")
            return self._failed(str(exc))

    async def send_plan(
        self, plan: dict[str, Any], recipient: str | None = None
    ) -> bool:
        self.last_error = None
        recipient = recipient or settings.delivery_email_address
        if not settings.sendgrid_api_key or not recipient:
            return self._failed("SendGrid API key or recipient is not configured.")
        try:
            rows = "".join(
                f"<tr><td>{_escape(label)}</td><td>{_escape(str(value))}</td></tr>"
                for label, value in (
                    ("Resort", plan.get("resort_name") or "selected resort"),
                    ("Dates", f"{plan['outbound_date']} to {plan['return_date']}"),
                    ("Flight", _money(plan.get("flight_total_gbp"))),
                    ("Accommodation", _money(plan.get("hotel_total_gbp"))),
                    ("Estimated total", _money(plan.get("total_estimated_gbp"))),
                    ("Plan ID", plan["id"]),
                )
            )
            html = (
                "<h1>Draft trip plan</h1>"
                f"<table>{rows}</table>"
                "<p>This is a draft only. No booking or payment has been made.</p>"
            )
            mail = Mail(
                from_email=settings.sendgrid_from_email,
                to_emails=recipient,
                subject="Your Snow Intelligence draft trip plan",
                html_content=html,
            )
            response = await asyncio.to_thread(self._send, mail)
            if not 200 <= response.status_code < 300:
                return self._failed(f"SendGrid returned HTTP {response.status_code}.")
            return True
        except Exception as exc:
            logger.exception("SendGrid plan delivery failed")
            return self._failed(str(exc))

    def _send(self, mail: Mail):
        return self._client_factory(api_key=settings.sendgrid_api_key).send(mail)

    def _failed(self, reason: str) -> bool:
        self.last_error = reason
        return False


def _money(value: Any) -> str:
    return f"\u00a3{value}" if value not in (None, "") else "unavailable"


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
