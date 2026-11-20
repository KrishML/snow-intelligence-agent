"""Reddit client — async-compatible praw wrapper for subreddit post fetching.

praw is synchronous; we wrap it using asyncio.to_thread() to avoid blocking
the event loop.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

import praw
import praw.exceptions

from app.settings import settings

logger = logging.getLogger(__name__)

# Subreddits to search per mode
_SUBREDDITS: dict[str, list[str]] = {
    "sports": ["skiing", "snowboarding", "Alps"],
    "tourism": ["travel", "europe"],
}


class RedditClient:
    """Async-compatible wrapper around praw.Reddit.

    All public methods are async; praw calls are delegated to a thread pool
    via ``asyncio.to_thread`` to avoid blocking the event loop.
    """

    def __init__(self) -> None:
        self.last_error: str | None = None

    def _make_reddit(self) -> praw.Reddit:
        return praw.Reddit(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
        )

    async def fetch_posts(
        self,
        resort_name: str,
        mode: Literal["sports", "tourism"],
    ) -> list[str]:
        """Search mode-appropriate subreddits for recent posts mentioning resort_name.

        Returns up to settings.reddit_post_limit post strings
        (title + " " + body, truncated to 500 chars each).
        Returns empty list on any error — callers handle fallback.
        """
        self.last_error = None
        if not settings.reddit_client_id or not settings.reddit_client_secret:
            self.last_error = "Reddit credentials are not configured"
            logger.warning("%s", self.last_error)
            return []

        subreddits = _SUBREDDITS.get(mode, ["skiing"])
        subreddit_str = "+".join(subreddits)
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=settings.reddit_lookback_days)

        def _sync_fetch() -> list[str]:
            reddit = self._make_reddit()
            results: list[str] = []
            sub = reddit.subreddit(subreddit_str)
            for submission in sub.search(
                resort_name,
                sort="new",
                time_filter="week",
                limit=settings.reddit_post_limit * 2,  # fetch extra, filter by date
            ):
                if datetime.fromtimestamp(submission.created_utc, tz=timezone.utc) < cutoff:
                    continue
                text = f"{submission.title} {submission.selftext or ''}".strip()
                results.append(text[:500])
                if len(results) >= settings.reddit_post_limit:
                    break
            return results

        try:
            return await asyncio.to_thread(_sync_fetch)
        except praw.exceptions.PRAWException as exc:
            self.last_error = str(exc)
            logger.warning("Reddit fetch failed for '%s': %s", resort_name, exc)
            return []
        except Exception as exc:  # rate limits, network errors, etc.
            self.last_error = str(exc)
            logger.warning("Reddit fetch error for '%s': %s", resort_name, exc)
            return []
