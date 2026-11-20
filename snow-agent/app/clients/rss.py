"""RSS client — feedparser wrapper for SnowForecast.com resort feeds."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import feedparser
import httpx

from app.settings import settings
from app.utils import describe_error

logger = logging.getLogger(__name__)

# SnowForecast.com RSS URL template
_RSS_URL_TEMPLATE = "https://www.snowforecast.com/resorts/{slug}/6day/top/snow/rss"


def _resort_name_to_slug(resort_name: str) -> str:
    """Convert a resort name to a SnowForecast.com slug.

    e.g. 'Chamonix-Mont-Blanc' -> 'chamonix-mont-blanc'
         'Val d'Isère' -> 'val-disere'
    """
    import unicodedata
    # Normalise unicode (remove accents)
    normalised = unicodedata.normalize("NFKD", resort_name)
    ascii_str = normalised.encode("ascii", "ignore").decode("ascii")
    # Replace spaces and special chars with hyphens, lowercase
    slug = ascii_str.lower().replace(" ", "-").replace("'", "").replace("--", "-")
    # Remove any remaining non-alphanumeric/hyphen characters
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    return slug.strip("-")


class RSSClient:
    """Fetches resort condition entries from SnowForecast.com RSS feeds."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.last_error: str | None = None

    async def fetch_entries(self, resort_name: str) -> list[str]:
        """Return up to 5 entry strings (title + summary) from the resort RSS feed.

        Feedparser parses the downloaded feed in a thread. Network I/O is
        performed with a bounded timeout so a stalled feed cannot block a run.
        Returns empty list on any error.
        """
        self.last_error = None
        slug = _resort_name_to_slug(resort_name)
        url = _RSS_URL_TEMPLATE.format(slug=slug)

        def _sync_parse(feed_content: str) -> list[str]:
            feed = feedparser.parse(feed_content)
            entries: list[str] = []
            for entry in feed.entries[:5]:
                title = getattr(entry, "title", "") or ""
                summary = getattr(entry, "summary", "") or ""
                text = f"{title} {summary}".strip()
                if text:
                    entries.append(text[:500])
            return entries

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(url)
                response.raise_for_status()
            return await asyncio.to_thread(_sync_parse, response.text)
        except Exception as exc:
            self.last_error = describe_error(exc)
            logger.warning("RSS fetch failed for '%s': %s", resort_name, exc)
            return []

    async def fetch_travel_entries(self, resort_name: str, feeds: list[str]) -> list[str]:
        """Return up to 5 entries from travel-blog feeds that mention the resort by name."""
        self.last_error = None
        needle = resort_name.casefold()
        matches: list[str] = []
        errors: list[str] = []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for url in feeds:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    feed = await asyncio.to_thread(feedparser.parse, response.text)
                except Exception as exc:
                    errors.append(f"{url}: {describe_error(exc)}")
                    continue
                for entry in feed.entries:
                    title = getattr(entry, "title", "") or ""
                    summary = getattr(entry, "summary", "") or ""
                    text = f"{title} {summary}".strip()
                    if needle in text.casefold():
                        matches.append(text[:500])
        if errors:
            self.last_error = "; ".join(errors)
            logger.warning("Travel feed errors for '%s': %s", resort_name, self.last_error)
        return matches[:5]
