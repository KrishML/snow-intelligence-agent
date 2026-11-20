"""
Mocked integration tests for RedditClient and RSSClient — task 4.3.

All external calls (praw.Reddit, feedparser.parse) are intercepted via
pytest-mock or unittest.mock.  No real Reddit credentials or network I/O
are required.

Tests cover:
  - RedditClient: subreddit selection by mode (sports / tourism)
  - RedditClient: post_limit enforcement
  - RedditClient: PRAWException → empty list (no raise)
  - RedditClient: generic Exception → empty list (no raise)
  - RSSClient: returns up to 5 entries
  - RSSClient: entry formatted as "title summary"
  - RSSClient: feedparser error → empty list (no raise)
  - slug generation helper

Requirements: 10.1, 10.2, 10.7
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import praw.exceptions
import pytest

from app.clients.reddit import RedditClient
from app.clients.rss import RSSClient, _resort_name_to_slug
from app.settings import settings


@pytest.fixture(autouse=True)
def _reddit_credentials():
    original_id = settings.reddit_client_id
    original_secret = settings.reddit_client_secret
    settings.reddit_client_id = "test-client"
    settings.reddit_client_secret = "test-secret"
    yield
    settings.reddit_client_id = original_id
    settings.reddit_client_secret = original_secret


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_submission(title: str = "Snow report", body: str = "", days_ago: float = 0) -> MagicMock:
    """Build a MagicMock that resembles a praw Submission object."""
    sub = MagicMock()
    sub.title = title
    sub.selftext = body
    ts = (datetime.now(tz=timezone.utc) - timedelta(days=days_ago)).timestamp()
    sub.created_utc = ts
    return sub


# ---------------------------------------------------------------------------
# RedditClient — subreddit selection
# ---------------------------------------------------------------------------

def test_reddit_sports_mode_uses_correct_subreddits(mocker):
    """Sports mode must search skiing+snowboarding+Alps subreddits."""
    captured_subreddits: list[str] = []

    # The sub.search() call returns one recent post so the client actually runs
    recent_sub = _make_submission(days_ago=0)

    mock_sub = MagicMock()
    mock_sub.search.return_value = iter([recent_sub])

    def fake_subreddit(name: str):
        captured_subreddits.append(name)
        return mock_sub

    mock_reddit_instance = MagicMock()
    mock_reddit_instance.subreddit.side_effect = fake_subreddit

    mocker.patch("praw.Reddit", return_value=mock_reddit_instance)

    client = RedditClient()
    asyncio.run(client.fetch_posts("Zermatt", "sports"))

    assert len(captured_subreddits) == 1
    subreddit_str = captured_subreddits[0]
    assert "skiing" in subreddit_str
    assert "snowboarding" in subreddit_str
    assert "Alps" in subreddit_str


def test_reddit_tourism_mode_uses_correct_subreddits(mocker):
    """Tourism mode must search travel+europe subreddits."""
    captured_subreddits: list[str] = []

    recent_sub = _make_submission(days_ago=0)
    mock_sub = MagicMock()
    mock_sub.search.return_value = iter([recent_sub])

    def fake_subreddit(name: str):
        captured_subreddits.append(name)
        return mock_sub

    mock_reddit_instance = MagicMock()
    mock_reddit_instance.subreddit.side_effect = fake_subreddit

    mocker.patch("praw.Reddit", return_value=mock_reddit_instance)

    client = RedditClient()
    asyncio.run(client.fetch_posts("Zermatt", "tourism"))

    assert len(captured_subreddits) == 1
    subreddit_str = captured_subreddits[0]
    assert "travel" in subreddit_str
    assert "europe" in subreddit_str


# ---------------------------------------------------------------------------
# RedditClient — post_limit enforcement
# ---------------------------------------------------------------------------

def test_reddit_returns_at_most_post_limit_posts(mocker):
    """RedditClient must never return more than settings.reddit_post_limit posts."""
    original_limit = settings.reddit_post_limit
    settings.reddit_post_limit = 5

    try:
        # Generate 15 recent submissions
        recent_subs = [_make_submission(title=f"Post {i}", days_ago=0) for i in range(15)]

        mock_sub = MagicMock()
        mock_sub.search.return_value = iter(recent_subs)

        mock_reddit_instance = MagicMock()
        mock_reddit_instance.subreddit.return_value = mock_sub

        mocker.patch("praw.Reddit", return_value=mock_reddit_instance)

        client = RedditClient()
        result = asyncio.run(client.fetch_posts("Verbier", "sports"))

        assert len(result) <= 5
    finally:
        settings.reddit_post_limit = original_limit


# ---------------------------------------------------------------------------
# RedditClient — exception handling
# ---------------------------------------------------------------------------

def test_reddit_returns_empty_list_on_praw_exception(mocker):
    """PRAWException must be swallowed; an empty list is returned."""
    mock_sub = MagicMock()
    mock_sub.search.side_effect = praw.exceptions.PRAWException("rate limited")

    mock_reddit_instance = MagicMock()
    mock_reddit_instance.subreddit.return_value = mock_sub

    mocker.patch("praw.Reddit", return_value=mock_reddit_instance)

    client = RedditClient()
    result = asyncio.run(client.fetch_posts("Zermatt", "sports"))

    assert result == []


def test_reddit_returns_empty_list_on_generic_exception(mocker):
    """Any generic Exception must be swallowed; an empty list is returned."""
    mock_sub = MagicMock()
    mock_sub.search.side_effect = Exception("network error")

    mock_reddit_instance = MagicMock()
    mock_reddit_instance.subreddit.return_value = mock_sub

    mocker.patch("praw.Reddit", return_value=mock_reddit_instance)

    client = RedditClient()
    result = asyncio.run(client.fetch_posts("Zermatt", "sports"))

    assert result == []


# ---------------------------------------------------------------------------
# RSSClient — entry count and formatting
# ---------------------------------------------------------------------------

def _make_feed(n_entries: int) -> SimpleNamespace:
    """Build a minimal feedparser-style object with n_entries entries.

    feedparser returns a FeedParserDict that supports attribute access
    (feed.entries). We use SimpleNamespace to replicate that interface.
    """
    entries = [
        SimpleNamespace(
            title=f"Entry {i} title",
            summary=f"Entry {i} summary text",
        )
        for i in range(n_entries)
    ]
    return SimpleNamespace(entries=entries, bozo=False)


def _mock_rss_http(mocker, content: str = "<rss/>", *, error: Exception | None = None):
    """Mock the bounded HTTP fetch used before feedparser parses an RSS document."""
    response = MagicMock()
    response.text = content
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.get = AsyncMock(side_effect=error, return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    mocker.patch("httpx.AsyncClient", return_value=client)


def test_rss_returns_at_most_5_entries(mocker):
    """RSSClient must return at most 5 entries even when the feed has more."""
    feed = _make_feed(7)

    _mock_rss_http(mocker)
    mocker.patch("feedparser.parse", return_value=feed)

    client = RSSClient()
    result = asyncio.run(client.fetch_entries("Zermatt"))

    assert len(result) <= 5


def test_rss_formats_entry_as_title_summary(mocker):
    """Each returned string should start with the entry title."""
    entry = SimpleNamespace(title="Great powder", summary="30cm fell overnight")
    feed = SimpleNamespace(entries=[entry], bozo=False)

    _mock_rss_http(mocker)
    mocker.patch("feedparser.parse", return_value=feed)

    client = RSSClient()
    result = asyncio.run(client.fetch_entries("Chamonix"))

    assert len(result) == 1
    assert result[0].startswith("Great powder")


def test_rss_returns_empty_list_on_feedparser_error(mocker):
    """feedparser exceptions must be swallowed; an empty list is returned."""
    _mock_rss_http(mocker, error=Exception("timeout"))

    client = RSSClient()
    result = asyncio.run(client.fetch_entries("Zermatt"))

    assert result == []


async def test_reddit_missing_credentials_skips_external_call(mocker):
    settings.reddit_client_id = ""
    settings.reddit_client_secret = ""
    reddit = mocker.patch("praw.Reddit")

    client = RedditClient()
    assert await client.fetch_posts("Zermatt", "sports") == []
    assert client.last_error == "Reddit credentials are not configured"
    reddit.assert_not_called()


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

def test_slug_hyphenated_name():
    """Hyphenated resort names must be lowercased without modification."""
    assert _resort_name_to_slug("Chamonix-Mont-Blanc") == "chamonix-mont-blanc"


def test_slug_simple_name():
    """Simple resort names must be lowercased."""
    assert _resort_name_to_slug("Zermatt") == "zermatt"
