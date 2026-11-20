"""Official resort snow-report collection from operator-configured pages."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.settings import settings

logger = logging.getLogger(__name__)

_MAX_CHARS = 1500
_NOISE_TAGS = ("script", "style", "nav", "footer", "header", "noscript", "svg")


def load_sources(path: str | Path | None = None) -> dict[str, str]:
    """Load ``{resort name: https URL}``; a missing file means no sources are configured."""
    file = Path(path or settings.official_report_sources_file)
    if not file.is_file():
        return {}
    data = json.loads(file.read_text(encoding="utf-8"))
    return {
        name: url
        for name, url in data.items()
        if isinstance(url, str) and urlparse(url).scheme in ("http", "https")
    }


def extract_report_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return text[:_MAX_CHARS]


class OfficialReportClient:
    def __init__(
        self,
        sources: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._sources = load_sources() if sources is None else sources
        self._timeout = timeout_seconds
        self.last_error: str | None = None

    async def fetch_report(self, resort_name: str) -> str | None:
        """Return the report text, or None when no source is configured or the fetch fails."""
        self.last_error = None
        url = self._sources.get(resort_name)
        if url is None:
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("Official report fetch failed for '%s': %s", resort_name, exc)
            return None
        text = extract_report_text(response.text)
        return text or None
