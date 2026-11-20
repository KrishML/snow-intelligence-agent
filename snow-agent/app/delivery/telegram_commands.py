"""Telegram bot commands for the main service (everything except booking).

Booking commands live in the separate booking service on its own bot token, so
nothing in this module can reach a booking route.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from app.prefs.service import PreferenceError, PrefsService
from app.prefs.telegram_link import LinkCodeStore
from app.settings import settings

logger = logging.getLogger(__name__)

HELP_TEXT = "\n".join(
    [
        "/start <code>  link this chat (get a code from the dashboard)",
        "/mode          show the current mode",
        "/mode sports|tourism  switch mode",
        "/budget [£X]   show or set the trip budget",
        "/report        latest snow report",
        "/plan          latest draft trip plan",
        "/help          this list",
    ]
)

ReportLoader = Callable[[Any], Awaitable[dict[str, Any]]]
PlanLoader = Callable[[uuid.UUID], Awaitable[dict[str, Any] | None]]


def format_report(weekly: dict[str, Any]) -> str:
    if weekly.get("run") is None:
        return "No completed weekly run yet."
    lines = [f"Snow report ({weekly['mode']}, updated {weekly.get('last_updated')})"]
    for resort in weekly["resorts"][:5]:
        lines.append(
            f"{resort['rank']}. {resort['name']}, {resort['country']} - "
            f"{resort['score']['total']}/100, {resort['depth_cm']} cm"
        )
    if not weekly["resorts"]:
        lines.append("No resorts passed the snow gate.")
    if weekly.get("partial_failure"):
        lines.append("Some data sources failed in this run.")
    attempt = weekly.get("latest_attempt")
    if attempt and attempt["status"] == "failed":
        lines.append("The latest run failed; this is the previous result.")
    return "\n".join(lines)


def format_plan(plan: dict[str, Any] | None) -> str:
    if plan is None:
        return "No draft plan yet. Plan a trip in the chat."
    return (
        f"Draft plan: {plan.get('resort_name') or 'selected resort'}\n"
        f"Dates: {plan['outbound_date']} to {plan['return_date']}\n"
        f"Estimated total: £{plan.get('total_estimated_gbp')}\n"
        "This is a draft. Nothing has been booked."
    )


class TelegramCommands:
    def __init__(
        self,
        prefs: PrefsService,
        links: LinkCodeStore,
        load_report: ReportLoader,
        load_plan: PlanLoader,
        user_id: uuid.UUID | None = None,
    ) -> None:
        self._prefs = prefs
        self._links = links
        self._load_report = load_report
        self._load_plan = load_plan
        self._user_id = user_id or settings.default_user_id

    async def _is_linked(self, chat_id: str) -> bool:
        prefs = await self._prefs.get(self._user_id)
        return chat_id in {prefs.telegram_chat_id, settings.telegram_chat_id} - {None, ""}

    async def handle(self, chat_id: str, text: str) -> str:
        parts = text.strip().split(maxsplit=1)
        if not parts or not parts[0].startswith("/"):
            return "Send /help for the list of commands."
        command = parts[0].split("@")[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command == "/start":
            return await self._start(chat_id, argument)
        if not await self._is_linked(chat_id):
            return "This chat is not linked. Get a code from the dashboard and send /start <code>."
        if command == "/help":
            return HELP_TEXT
        if command == "/mode":
            return await self._mode(argument)
        if command == "/budget":
            return await self._budget(argument)
        if command == "/report":
            prefs = await self._prefs.get(self._user_id)
            return format_report(await self._load_report(prefs))
        if command == "/plan":
            return format_plan(await self._load_plan(self._user_id))
        if command in ("/book", "/confirm"):
            return "Booking is handled by the separate booking bot, not this chat."
        return "Unknown command. Send /help."

    async def _start(self, chat_id: str, code: str) -> str:
        if not code:
            return "Get a link code from the dashboard, then send /start <code>."
        user_id = await self._links.consume(code)
        if user_id is None or user_id != self._user_id:
            return "That code is invalid or has expired."
        await self._prefs.update(user_id, {"telegram_chat_id": chat_id})
        return "This chat is now linked. Send /help for commands."

    async def _mode(self, argument: str) -> str:
        if not argument:
            prefs = await self._prefs.get(self._user_id)
            return f"Current mode: {prefs.mode}."
        if argument.lower() not in ("sports", "tourism"):
            return "Mode must be sports or tourism."
        prefs = await self._prefs.update(self._user_id, {"mode": argument.lower()})
        return f"Mode set to {prefs.mode}."

    async def _budget(self, argument: str) -> str:
        if not argument:
            prefs = await self._prefs.get(self._user_id)
            budget = f"£{prefs.budget_total_gbp}" if prefs.budget_total_gbp is not None else "not set"
            return f"Budget: {budget}."
        cleaned = re.sub(r"^[£$]", "", argument.strip())
        try:
            amount = Decimal(cleaned)
        except InvalidOperation:
            return "Send a number, for example /budget 600."
        if not amount.is_finite() or amount < 0 or amount > 1_000_000:
            return "Budget must be between 0 and 1,000,000."
        try:
            prefs = await self._prefs.update(self._user_id, {"budget_total_gbp": amount})
        except PreferenceError as exc:
            return str(exc)
        return f"Budget set to £{prefs.budget_total_gbp}."


def build_application(commands: TelegramCommands, token: str):
    from telegram import Update
    from telegram.ext import Application, ContextTypes, MessageHandler, filters

    async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None or update.message is None or not update.message.text:
            return
        reply = await commands.handle(str(update.effective_chat.id), update.message.text)
        await update.message.reply_text(reply)

    application = Application.builder().token(token).build()
    application.add_handler(MessageHandler(filters.COMMAND, on_command))
    return application
