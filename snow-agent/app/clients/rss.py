"""RSS client — feedparser wrapper for SnowForecast.com resort feeds."""
from __future__ import annotations

import logging
from typing import Any

import feedparser

from app.settings import settings

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

    async def fetch_entries(self, resort_name: str) -> list[str]:
        """Return up to 5 entry strings (title + summary) from the resort RSS feed.

        feedparser.parse() is synchronous but fast (network-bound).
        We run it in a thread to avoid blocking.
        Returns empty list on any error.
        """
        import asyncio
        slug = _resort_name_to_slug(resort_name)
        url = _RSS_URL_TEMPLATE.format(slug=slug)

        def _sync_parse() -> list[str]:
            feed = feedparser.parse(url)
            entries: list[str] = []
            for entry in feed.entries[:5]:
                title = getattr(entry, "title", "") or ""
                summary = getattr(entry, "summary", "") or ""
                text = f"{title} {summary}".strip()
                if text:
                    entries.append(text[:500])
            return entries

        try:
            return await asyncio.to_thread(_sync_parse)
        except Exception as exc:
            logger.warning("RSS fetch failed for '%s': %s", resort_name, exc)
            return []
