from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fakeredis import FakeAsyncRedis
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.tools import ToolException
from redis.exceptions import ConnectionError as RedisConnectionError

from app.chat.agent import build_chat_graph
from app.chat.session import ChatSession
from app.chat.store import SessionStore, SessionStoreError
from app.chat.tools import create_chat_tools

USER = uuid.uuid4()


class RecordingModel:
    def __init__(self):
        self.seen: list[list] = []

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        self.seen.append(list(messages))
        yield AIMessageChunk(content="ok")


class NoMemory:
    async def retrieve_context(self, user_id, query_text):
        return ""

    async def extract_and_write(self, user_id, session_id, messages):
        self.written = messages


class NoPrefs:
    async def get(self, user_id):
        return None


def _chat(redis, model, memory=None):
    def builder(session_id, user_id, context, prefs=None, store=None):
        return build_chat_graph(
            session_id, user_id, context, model=model, prefs=prefs, store=store
        )

    return ChatSession(
        memory or NoMemory(),
        graph_builder=builder,
        prefs_service=NoPrefs(),
        store=SessionStore(redis),
    )


async def _drain(chat, session_id, text):
    return [t async for t in chat.send_message(session_id, text)]


async def test_session_history_survives_worker_restart():
    redis = FakeAsyncRedis(decode_responses=True)
    model = RecordingModel()
    first = _chat(redis, model)
    session_id = await first.create_session(USER)
    await _drain(first, session_id, "I like quiet villages")

    restarted = _chat(redis, model)
    assert await restarted.owns_session(session_id, USER)
    await _drain(restarted, session_id, "Where should I go?")

    contents = [m.content for m in model.seen[-1] if isinstance(m, HumanMessage)]
    assert contents == ["I like quiet villages", "Where should I go?"]


async def test_session_owned_only_by_creator():
    chat = _chat(FakeAsyncRedis(decode_responses=True), RecordingModel())
    session_id = await chat.create_session(USER)
    assert not await chat.owns_session(session_id, uuid.uuid4())
    assert not await chat.owns_session("missing", USER)


async def test_end_session_extracts_memory_and_clears_redis():
    redis = FakeAsyncRedis(decode_responses=True)
    memory = NoMemory()
    chat = _chat(redis, RecordingModel(), memory)
    session_id = await chat.create_session(USER)
    await _drain(chat, session_id, "hello")
    await chat.end_session(session_id)

    assert len(memory.written) == 2
    assert await redis.keys("snow-agent:session:*") == []
    with pytest.raises(KeyError):
        await _drain(chat, session_id, "again")


async def test_keys_carry_ttl():
    redis = FakeAsyncRedis(decode_responses=True)
    store = SessionStore(redis, ttl_seconds=100)
    await store.create("s1", USER, memory_context="", memory_loaded=False)
    await store.save_messages("s1", [HumanMessage(content="hi")])
    await store.reserve_live_call("s1", 10)
    await store.save_flight_options("s1", [{"id": "1"}])
    for part in ("meta", "messages", "live-snow", "flights"):
        ttl = await redis.ttl(f"snow-agent:session:s1:{part}")
        assert 0 < ttl <= 100


async def test_live_call_limit_survives_graph_rebuild():
    redis = FakeAsyncRedis(decode_responses=True)
    store = SessionStore(redis)
    results = [await store.reserve_live_call("s1", 10) for _ in range(12)]
    assert results == [True] * 10 + [False] * 2
    assert await redis.get("snow-agent:session:s1:live-snow") == "10"


async def test_live_snow_tool_uses_redis_limit(monkeypatch, resort_factory):
    redis = FakeAsyncRedis(decode_responses=True)
    store = SessionStore(redis)
    for _ in range(10):
        await store.reserve_live_call("sess", 10)
    tools = create_chat_tools(
        "sess", USER, {"live_calls": 0, "plan_created": False}, store=store
    )
    live = next(t for t in tools if t.name == "fetch_live_snow")
    with pytest.raises(ToolException, match="limit"):
        await live.ainvoke({"resort_id": str(uuid.uuid4())})


async def test_plan_flag_and_flights_persist(monkeypatch):
    store = SessionStore(FakeAsyncRedis(decode_responses=True))
    await store.create("s2", USER, memory_context="", memory_loaded=True)
    await store.mark_plan_created("s2")
    await store.save_flight_options("s2", [{"id": "f"}])
    assert (await store.get_meta("s2"))["plan_created"] is True
    assert await store.load_flight_options("s2") == [{"id": "f"}]


async def test_redis_outage_fails_loudly():
    class Broken:
        async def set(self, *a, **k):
            raise RedisConnectionError("down")

    store = SessionStore(Broken())
    with pytest.raises(SessionStoreError):
        await store.create("s3", USER, memory_context="", memory_loaded=False)
