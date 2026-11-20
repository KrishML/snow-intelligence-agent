from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy import text

from app.chat.memory import MemorySystem
from app.db.models.embeddings import (
    EMBEDDING_DIM,
    PreferenceEmbedding,
    ResortKnowledgeEmbedding,
    SessionSummaryEmbedding,
    TripFeedbackEmbedding,
)
from app.utils import current_season
from tests.factories import make_resort

pytestmark = pytest.mark.pg


def unit(index: int, *, also: tuple[int, float] | None = None) -> list[float]:
    """A 1536-dimension vector with 1.0 at ``index`` (and optionally a second component)."""
    vector = [0.0] * EMBEDDING_DIM
    vector[index] = 1.0
    if also:
        vector[also[0]] = also[1]
    return vector


class FakeOpenAI:
    def __init__(self, query_vector, extraction=None):
        self.query_vector = query_vector
        self.extraction = extraction or {}
        self.embedded: list[str] = []

    async def embed(self, text_value):
        self.embedded.append(text_value)
        return self.query_vector

    async def complete_json(self, **kwargs):
        return self.extraction


async def _count(factory, table: str, user_id: uuid.UUID) -> int:
    async with factory() as session:
        return (
            await session.execute(
                text(f"SELECT count(*) FROM {table} WHERE user_id = :u"), {"u": user_id}
            )
        ).scalar_one()


async def test_ivfflat_cosine_indexes_exist_on_all_four_vector_tables(factory):
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT tablename FROM pg_indexes "
                    "WHERE indexdef ILIKE '%ivfflat%' AND indexdef ILIKE '%vector_cosine_ops%'"
                )
            )
        ).all()
    assert {r[0] for r in rows} == {
        "preference_embeddings", "trip_feedback_embeddings",
        "resort_knowledge_embeddings", "session_summary_embeddings",
    }


async def test_cosine_search_returns_nearest_first(factory):
    user = uuid.uuid4()
    async with factory() as session:
        session.add_all(
            PreferenceEmbedding(id=uuid.uuid4(), user_id=user, text=name, embedding=unit(i))
            for i, name in enumerate(["near", "middle", "far"])
        )
        await session.commit()
        query = "[" + ",".join(str(v) for v in unit(0, also=(1, 0.3))) + "]"
        rows = (
            await session.execute(
                text(
                    "SELECT text FROM preference_embeddings WHERE user_id = :u "
                    "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 3"
                ),
                {"u": user, "q": query},
            )
        ).all()
    assert [r[0] for r in rows] == ["near", "middle", "far"]


async def test_retrieve_context_runs_all_four_searches_with_real_sql(factory):
    user, other = uuid.uuid4(), uuid.uuid4()
    resort = make_resort(name=f"Resort {uuid.uuid4()}")
    now = datetime.now(timezone.utc)
    async with factory() as session:
        session.add(resort)
        await session.flush()
        session.add_all(
            [
                PreferenceEmbedding(id=uuid.uuid4(), user_id=user, text="prefers quiet villages", embedding=unit(0)),
                PreferenceEmbedding(id=uuid.uuid4(), user_id=other, text="OTHER USER SECRET", embedding=unit(0)),
                TripFeedbackEmbedding(id=uuid.uuid4(), user_id=user, text="Zermatt was crowded", embedding=unit(0)),
                ResortKnowledgeEmbedding(
                    id=uuid.uuid4(), resort_id=resort.id, season=current_season(),
                    text="deep snow this season", embedding=unit(0),
                ),
                ResortKnowledgeEmbedding(
                    id=uuid.uuid4(), resort_id=resort.id, season="1999/00",
                    text="wrong season", embedding=unit(0),
                ),
                SessionSummaryEmbedding(
                    id=uuid.uuid4(), user_id=user, session_id="recent", text="asked about Austria",
                    embedding=unit(0), created_at=now - timedelta(days=2),
                ),
                SessionSummaryEmbedding(
                    id=uuid.uuid4(), user_id=user, session_id="old", text="too old to show",
                    embedding=unit(0), created_at=now - timedelta(days=45),
                ),
            ]
        )
        await session.commit()

    context = await MemorySystem(FakeOpenAI(unit(0)), factory).retrieve_context(user, "quiet trip?")

    assert "prefers quiet villages" in context
    assert "Zermatt was crowded" in context
    assert "deep snow this season" in context
    assert "asked about Austria" in context
    assert "OTHER USER SECRET" not in context
    assert "wrong season" not in context
    assert "too old to show" not in context


async def test_retrieval_is_fast_on_a_few_hundred_rows(factory):
    user = uuid.uuid4()
    async with factory() as session:
        session.add_all(
            PreferenceEmbedding(
                id=uuid.uuid4(), user_id=user, text=f"p{i}", embedding=unit(i % EMBEDDING_DIM)
            )
            for i in range(300)
        )
        await session.commit()

    memory = MemorySystem(FakeOpenAI(unit(0)), factory)
    started = time.perf_counter()
    await memory.retrieve_context(user, "anything")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5  # spec: pgvector retrieval under 500 ms


async def test_extract_and_write_stores_once_and_deduplicates_on_real_vectors(factory):
    user = uuid.uuid4()
    extraction = {"preferences": ["likes quiet villages"], "summary": "Planned an Austria weekend."}
    memory = MemorySystem(FakeOpenAI(unit(5), extraction), factory)
    messages = [HumanMessage(content="I like quiet villages")]

    await memory.extract_and_write(user, "s1", messages)
    await memory.extract_and_write(user, "s2", messages)

    assert await _count(factory, "preference_embeddings", user) == 1  # duplicate skipped
    assert await _count(factory, "session_summary_embeddings", user) == 2
