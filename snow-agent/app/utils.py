"""Shared utility functions for the Snow Intelligence Agent."""
from datetime import date


def current_season() -> str:
    """Return the current ski season string, e.g. '2025/26'.

    Season starts in October: if month >= 10, season is year/year+1.
    Otherwise it is year-1/year.
    """
    today = date.today()
    if today.month >= 10:
        return f"{today.year}/{str(today.year + 1)[2:]}"
    return f"{today.year - 1}/{str(today.year)[2:]}"


def describe_error(exc: BaseException) -> str:
    """Error text that is never empty: ``str(httpx.ReadTimeout())`` is ``""``."""
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
