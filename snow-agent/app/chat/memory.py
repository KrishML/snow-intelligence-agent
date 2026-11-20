"""Semantic memory retrieval and extraction for chat sessions."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.openai_client import OpenAIClient, OpenAIClientError
from app.db.models.embeddings import PreferenceEmbedding, SessionSummaryEmbedding
from app.db.session import AsyncSessionLocal
from app.settings import settings
from app.utils import current_season

logger = logging.getLogger(__name__)

_PII_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
)
_EXTRACTION_PROMPT = """Extract durable trip preferences and one brief session summary.
Return JSON with exactly two fields: "preferences" (array of concise statements)
and "summary" (one concise, non-sensitive summary).
Do not include names, contact details, exact addresses, payment information,
raw quotations, or other personally identifying information. Do not infer
preferences that the user did not express."""


def _remove_obvious_pii(value: str) -> str:
    for pattern in _PII_PATTERNS:
        value = pattern.sub("[redacted]", value)
    return value.strip()


def _message_text(messages: list[Any]) -> str:
    parts: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            content = message.get("content", "")
        else:
            content = getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            role = message.get("role", "user") if isinstance(message, dict) else ""
            parts.append(f"{role}: {content}" if role else content)
    return _remove_obvious_pii("\n".join(parts))


class MemorySystem:
    """Reads and writes extracted memory; raw chat messages are never persisted."""

    def __init__(
        self,
        openai_client: OpenAIClient | None = None,
        session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        self._openai = openai_client or OpenAIClient()
        self._session_factory = session_factory or AsyncSessionLocal

    async def retrieve_context(self, user_id: uuid.UUID, query_text: str) -> str:
        """Return formatted context from the four bounded vector searches."""
        query_vector = await self._openai.embed(_remove_obvious_pii(query_text))
        vector_literal = "[" + ",".join(str(value) for value in query_vector) + "]"
        season = current_season()
        searches = (
            (
                "Past preferences",
                """SELECT text FROM preference_embeddings
                   WHERE user_id = :user_id
                   ORDER BY embedding <=> CAST(:embedding AS vector)
                   LIMIT 3""",
                {"user_id": str(user_id), "embedding": vector_literal},
            ),
            (
                "Trip feedback",
                """SELECT text FROM trip_feedback_embeddings
                   WHERE user_id = :user_id
                   ORDER BY embedding <=> CAST(:embedding AS vector)
                   LIMIT 2""",
                {"user_id": str(user_id), "embedding": vector_literal},
            ),
            (
                "Resort knowledge",
                """SELECT text FROM resort_knowledge_embeddings
                   WHERE season = :season
                   ORDER BY embedding <=> CAST(:embedding AS vector)
                   LIMIT 3""",
                {
                    "season": season,
                    "embedding": vector_literal,
                },
            ),
            (
                "Recent sessions",
                """SELECT text FROM session_summary_embeddings
                   WHERE user_id = :user_id
                     AND created_at >= now() - interval '30 days'
                   ORDER BY embedding <=> CAST(:embedding AS vector)
                   LIMIT 1""",
                {"user_id": str(user_id), "embedding": vector_literal},
            ),
        )
        results = await asyncio.gather(
            *(self._query_texts(query, params) for _, query, params in searches)
        )
        sections = []
        for (heading, _, _), memories in zip(searches, results, strict=True):
            if memories:
                sections.append(f"{heading}:\n" + "\n".join(f"- {item}" for item in memories))
        if not sections:
            return ""
        return "[MEMORY CONTEXT]\n" + "\n\n".join(sections) + "\n[END MEMORY CONTEXT]"

    async def _query_texts(self, query: str, params: dict[str, Any]) -> list[str]:
        async with self._session_factory() as session:
            result = await session.execute(text(query), params)
            return [row[0] for row in result.all()]

    async def _deduplicate_preference(
        self, user_id: uuid.UUID, embedding: list[float]
    ) -> bool:
        """Return True when an existing preference has cosine similarity >= .95."""
        vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """SELECT id
                       FROM preference_embeddings
                       WHERE user_id = :user_id
                         AND embedding <=> CAST(:embedding AS vector) <= 0.05
                       LIMIT 1"""
                ),
                {"user_id": str(user_id), "embedding": vector_literal},
            )
            return result.first() is not None

    async def extract_and_write(
        self,
        user_id: uuid.UUID,
        session_id: str,
        messages: list[Any],
    ) -> None:
        """Extract and persist sanitized preference statements and one summary."""
        sanitized_transcript = _message_text(messages)
        extracted = await self._openai.complete_json(
            system_prompt=_EXTRACTION_PROMPT,
            user_prompt=sanitized_transcript[:12000],
            model=settings.openai_model_qualitative,
        )
        raw_preferences = extracted.get("preferences", [])
        if not isinstance(raw_preferences, list):
            raise OpenAIClientError("Memory extraction returned invalid preferences")
        preferences = [
            _remove_obvious_pii(item)[:500]
            for item in raw_preferences
            if isinstance(item, str) and _remove_obvious_pii(item)
        ]
        summary_value = extracted.get("summary", "")
        if not isinstance(summary_value, str):
            raise OpenAIClientError("Memory extraction returned an invalid summary")
        summary = _remove_obvious_pii(summary_value)[:1000]
        if not summary:
            summary = "No durable trip preferences were identified."

        async with self._session_factory() as session:
            for preference in preferences:
                vector = await self._openai.embed(preference)
                if await self._deduplicate_preference(user_id, vector):
                    continue
                session.add(
                    PreferenceEmbedding(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        text=preference,
                        embedding=vector,
                    )
                )
            summary_vector = await self._openai.embed(summary)
            session.add(
                SessionSummaryEmbedding(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    session_id=session_id,
                    text=summary,
                    embedding=summary_vector,
                )
            )
            await session.commit()
