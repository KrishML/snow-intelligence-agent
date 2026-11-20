from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.chat.memory import MemorySystem, _message_text


class FakeResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.statements = []
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

    async def execute(self, query, params):
        query_str = str(query)
        self.statements.append((query_str, params))
        if "preference_embeddings" in query_str and "SELECT id" in query_str:
            return self.responses.get("dedup", FakeResult())
        if "trip_feedback_embeddings" in query_str:
            return FakeResult([("User liked the guided tour.",)])
        if "resort_knowledge_embeddings" in query_str:
            return FakeResult([("Recent snow was stable.",)])
        if "session_summary_embeddings" in query_str:
            return FakeResult([("Prefers short trips.",)])
        if "preference_embeddings" in query_str:
            return FakeResult([("Prefers quiet resorts.",)])
        return FakeResult()

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True


def test_message_text_redacts_contact_and_payment_patterns():
    transcript = _message_text(
        [
            {"role": "user", "content": "Email me at skier@example.com"},
            {"role": "assistant", "content": "Card 4242 4242 4242 4242"},
        ]
    )
    assert "skier@example.com" not in transcript
    assert "4242 4242 4242 4242" not in transcript
    assert "[redacted]" in transcript


async def test_retrieve_context_uses_bounded_similarity_queries():
    sessions = []

    def session_factory():
        session = FakeSession()
        sessions.append(session)
        return session

    client = SimpleNamespace(embed=AsyncMock(return_value=[0.1] * 1536))
    memory = MemorySystem(openai_client=client, session_factory=session_factory)

    context = await memory.retrieve_context(uuid.uuid4(), "snowy quiet weekend")

    assert context.startswith("[MEMORY CONTEXT]")
    assert "Prefers quiet resorts." in context
    assert "User liked the guided tour." in context
    assert "Recent snow was stable." in context
    assert "Prefers short trips." in context
    assert len(sessions) == 4
    limits = [
        "LIMIT 3",
        "LIMIT 2",
        "LIMIT 3",
        "LIMIT 1",
    ]
    for session, limit in zip(sessions, limits, strict=True):
        query, params = session.statements[0]
        assert limit in query
        assert "<=>" in query
        assert len(params["embedding"]) > 0


async def test_deduplicate_preference_uses_cosine_threshold():
    session = FakeSession({"dedup": FakeResult([("existing-id",)])})
    memory = MemorySystem(
        openai_client=SimpleNamespace(),
        session_factory=lambda: session,
    )

    duplicate = await memory._deduplicate_preference(uuid.uuid4(), [0.1] * 1536)

    assert duplicate
    query, params = session.statements[0]
    assert "<= 0.05" in query
    assert params["embedding"].startswith("[")


async def test_extract_writes_only_extracted_sanitized_memory():
    session = FakeSession({"dedup": FakeResult([("existing-id",)])})
    client = SimpleNamespace(
        complete_json=AsyncMock(
            return_value={
                "preferences": [
                    "Prefers quiet resorts.",
                    "Contact skier@example.com",
                ],
                "summary": "Prefers quiet weekend trips.",
            }
        ),
        embed=AsyncMock(return_value=[0.2] * 1536),
    )
    memory = MemorySystem(
        openai_client=client,
        session_factory=lambda: session,
    )

    await memory.extract_and_write(
        uuid.uuid4(),
        "session-123",
        [{"role": "user", "content": "I prefer quiet resorts. Email me at skier@example.com"}],
    )

    assert client.complete_json.await_args.kwargs["user_prompt"].find(
        "skier@example.com"
    ) == -1
    assert len(session.added) == 1
    assert session.added[0].__tablename__ == "session_summary_embeddings"
    assert "skier@example.com" not in session.added[0].text
    assert session.committed
