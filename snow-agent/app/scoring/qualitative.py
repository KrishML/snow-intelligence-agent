"""Qualitative Signal Scorer — replaces the Phase 1 score_qualitative=10 placeholder.

Fetches Reddit posts and RSS entries for each resort, sends them to GPT-4o-mini,
and computes a 0–20 point qualitative score based on sentiment and negative keywords.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Literal
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.openai_client import OpenAIClient, OpenAIClientError
from app.clients.reddit import RedditClient
from app.clients.rss import RSSClient
from app.db.models.embeddings import ResortKnowledgeEmbedding
from app.db.models.qualitative_signal import QualitativeSignal
from app.db.models.resort import Resort
from app.settings import settings
from app.utils import current_season

logger = logging.getLogger(__name__)

_NEGATIVE_KEYWORDS = {"icy", "slush", "closed", "bare", "thin", "rock", "artificial"}

_SYSTEM_PROMPT = """You are a ski and winter travel conditions analyst.
Analyse the provided text from social media and RSS feeds about a resort.
Return a JSON object with exactly three fields:
  "summary": a 2-3 sentence summary of current resort conditions,
  "sentiment": one of "positive", "neutral", or "negative",
  "negative_keywords": an array of detected negative indicator words from
    the set: icy, slush, closed, bare, thin, rock, artificial.
Only include keywords that actually appear in the text."""


@dataclass
class QualitativeResult:
    resort_id: uuid.UUID
    score: int                          # 0–20
    sentiment: Literal["positive", "neutral", "negative"]
    summary: str
    negative_keywords: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    source_text_snippet: str = ""


def _compute_score(
    sentiment: Literal["positive", "neutral", "negative"],
    negative_keywords: list[str],
) -> int:
    """Pure function: (sentiment, keyword list) -> score in [0, 20].

    positive:  base=20, penalty=min(len(kw)*2, 5), floor=15
    neutral:   fixed 10
    negative:  base=5,  penalty=min(len(kw)*2, 5), floor=0
    """
    if sentiment == "positive":
        penalty = min(len(negative_keywords) * 2, 5)
        score = max(15, 20 - penalty)
    elif sentiment == "neutral":
        score = 10
    else:  # negative
        penalty = min(len(negative_keywords) * 2, 5)
        score = max(0, 5 - penalty)
    return max(0, min(20, score))


class QualitativeSignalScorer:
    """Orchestrates Reddit + RSS fetching, GPT-4o-mini summarisation, and scoring."""

    def __init__(self) -> None:
        self._reddit = RedditClient()
        self._rss = RSSClient()
        self._openai = OpenAIClient()
        self._sem = asyncio.Semaphore(settings.qualitative_concurrency)

    async def score(
        self,
        resort: Resort,
        mode: Literal["sports", "tourism"],
    ) -> QualitativeResult:
        """Score a single resort's qualitative signal.

        Returns QualitativeResult(score=10, sentiment='neutral') as the fallback
        if all sources fail or GPT-4o-mini returns unparseable output.
        """
        async with self._sem:
            return await self._score_inner(resort, mode)

    async def _score_inner(
        self,
        resort: Resort,
        mode: Literal["sports", "tourism"],
    ) -> QualitativeResult:
        sources_used: list[str] = []
        raw_texts: list[str] = []

        # 1. Fetch Reddit posts
        try:
            reddit_posts = await self._reddit.fetch_posts(resort.name, mode)
            if reddit_posts:
                raw_texts.extend(reddit_posts)
                sources_used.append("reddit")
        except Exception as exc:
            logger.warning("Reddit fetch error for %s: %s", resort.name, exc)

        # 2. Fetch RSS entries
        try:
            rss_entries = await self._rss.fetch_entries(resort.name)
            if rss_entries:
                raw_texts.extend(rss_entries)
                sources_used.append("rss")
        except Exception as exc:
            logger.warning("RSS fetch error for %s: %s", resort.name, exc)

        # 3. Fallback if no data
        if not raw_texts:
            logger.info("No source data for %s — using neutral default", resort.name)
            return QualitativeResult(
                resort_id=resort.id,
                score=10,
                sentiment="neutral",
                summary="No recent reports found. Conditions unknown.",
                sources_used=[],
                source_text_snippet="",
            )

        # 4. Build prompt text (truncate to ~4000 chars for token budget)
        combined = "\n\n".join(raw_texts)
        truncated = combined[:4000]
        snippet = truncated[:1000]

        user_prompt = (
            f"Resort: {resort.name} ({resort.country})\n\n"
            f"Recent reports:\n{truncated}"
        )

        # 5. Call GPT-4o-mini
        try:
            result = await self._openai.complete_json(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            summary = str(result.get("summary", "Conditions information available."))
            sentiment_raw = result.get("sentiment", "neutral")
            sentiment: Literal["positive", "neutral", "negative"] = (
                sentiment_raw if sentiment_raw in ("positive", "neutral", "negative") else "neutral"
            )
            raw_kw = result.get("negative_keywords", [])
            negative_keywords = [
                kw for kw in raw_kw if isinstance(kw, str) and kw.lower() in _NEGATIVE_KEYWORDS
            ]
        except OpenAIClientError as exc:
            logger.warning("GPT-4o-mini failed for %s: %s — using neutral fallback", resort.name, exc)
            return QualitativeResult(
                resort_id=resort.id,
                score=10,
                sentiment="neutral",
                summary="Unable to summarise conditions at this time.",
                sources_used=sources_used,
                source_text_snippet=snippet,
            )

        # 6. Compute score
        score = _compute_score(sentiment, negative_keywords)

        return QualitativeResult(
            resort_id=resort.id,
            score=score,
            sentiment=sentiment,
            summary=summary,
            negative_keywords=negative_keywords,
            sources_used=sources_used,
            source_text_snippet=snippet,
        )

    async def write_results(
        self,
        session: AsyncSession,
        weekly_run_id: uuid.UUID,
        result: QualitativeResult,
    ) -> None:
        """Write a QualitativeSignal record and a ResortKnowledgeEmbedding to the DB."""
        # 1. Write QualitativeSignal record
        signal = QualitativeSignal(
            id=uuid.uuid4(),
            weekly_run_id=weekly_run_id,
            resort_id=result.resort_id,
            source_type="reddit" if "reddit" in result.sources_used else "rss",
            raw_snippet=result.source_text_snippet[:1000] if result.source_text_snippet else None,
            summary=result.summary,
            sentiment=result.sentiment,
            negative_keywords=result.negative_keywords or None,
            score=result.score,
        )
        session.add(signal)

        # 2. Embed summary and write ResortKnowledgeEmbedding (only when API key is set)
        if settings.openai_api_key and result.summary:
            try:
                vector = await self._openai.embed(result.summary)
                embedding = ResortKnowledgeEmbedding(
                    resort_id=result.resort_id,
                    season=current_season(),
                    text=result.summary,
                    embedding=vector,
                )
                session.add(embedding)
            except OpenAIClientError as exc:
                logger.warning(
                    "Failed to embed qualitative summary for resort %s: %s — skipping embedding",
                    result.resort_id,
                    exc,
                )
