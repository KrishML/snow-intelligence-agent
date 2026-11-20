"""Secret-boundary checks between the chat/main service and the booking service.

This module has no dependency on either service's settings, so it can be imported
by both without leaking credentials across the boundary.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# Present only in the chat/main service environment.
CHAT_ONLY_ENV_VARS = (
    "OPENAI_API_KEY",
    "AMADEUS_CLIENT_ID",
    "AMADEUS_CLIENT_SECRET",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
)
# Present only in the booking service environment.
BOOKING_ONLY_ENV_VARS = (
    "DUFFEL_API_KEY",
    "STRIPE_SECRET_KEY",
    "HOTEL_BOOKING_API_KEY",
    "BOOKING_API_TOKEN",
)


class IsolationError(RuntimeError):
    """The process environment violates the chat/booking secret boundary."""


def _assert_absent(
    names: tuple[str, ...], service: str, environ: Mapping[str, str] | None
) -> None:
    environ = os.environ if environ is None else environ
    present = sorted(name for name in names if environ.get(name))
    if present:
        raise IsolationError(f"{service} must not have {', '.join(present)} in its environment.")


def assert_chat_environment(environ: Mapping[str, str] | None = None) -> None:
    _assert_absent(BOOKING_ONLY_ENV_VARS, "The chat service", environ)


def assert_booking_environment(environ: Mapping[str, str] | None = None) -> None:
    _assert_absent(CHAT_ONLY_ENV_VARS, "The booking service", environ)
