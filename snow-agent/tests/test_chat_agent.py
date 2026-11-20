from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import ToolException

from app.chat.agent import build_chat_graph
from app.chat.session import ChatSession
from app.chat.store import SessionStore
from app.chat.tools import create_chat_tools
from fakeredis import FakeAsyncRedis as FakeRedis


class FakeModel:
    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def astream(self, messages):
        yield AIMessageChunk(content="Snow conditions ")
        yield AIMessageChunk(content="are available in the weekly report.")


def test_tool_registry_is_exactly_the_permitted_seven():
    tools = create_chat_tools(
        "session-id",
        uuid.uuid4(),
        {"live_calls": 0, "plan_created": False},
    )
    assert {item.name for item in tools} == {
        "filter_results",
        "fetch_live_snow",
        "fetch_webcam",
        "search_flights_readonly",
        "search_hotels_readonly",
        "get_historical_reliability",
        "create_plan_artifact",
    }


async def test_graph_invokes_assistant_without_booking_tools():
    model = FakeModel()
    graph = build_chat_graph(
        "session-id",
        uuid.uuid4(),
        "",
        model=model,
    )
    chunks = []
    async for chunk, _metadata in graph.astream(
        {"messages": [("user", "What resorts are doing well?")]},
        config={"configurable": {"thread_id": "session-id"}},
        stream_mode="messages",
    ):
        if isinstance(chunk.content, str):
            chunks.append(chunk.content)
    assert "".join(chunks) == (
        "Snow conditions are available in the weekly report."
    )
    assert len(model.tools) == 7


async def test_live_snow_tool_enforces_ten_calls(monkeypatch, resort_factory):
    resort = resort_factory.build()

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def get(self, model, item_id):
            return resort

    class FakeOpenMeteo:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def get_current_conditions(self, resort_id, lat, lon):
            return SimpleNamespace(snow_depth_cm=60.0, temperature_c=-5.0)

        async def get_5day_forecast(self, resort_id, lat, lon):
            return SimpleNamespace(days=[])

    monkeypatch.setattr("app.chat.tools.AsyncSessionLocal", FakeSession)
    monkeypatch.setattr("app.chat.tools.OpenMeteoClient", FakeOpenMeteo)
    tools = create_chat_tools(
        "session-id",
        uuid.uuid4(),
        {"live_calls": 0, "plan_created": False},
    )
    live_tool = next(item for item in tools if item.name == "fetch_live_snow")

    for _ in range(10):
        result = await live_tool.ainvoke({"resort_id": str(resort.id)})
        assert result["snow_depth_cm"] == 60.0
    with pytest.raises(ToolException, match="limit"):
        await live_tool.ainvoke({"resort_id": str(resort.id)})


class FakeMemory:
    def __init__(self):
        self.queries = []
        self.writes = []

    async def retrieve_context(self, user_id, query_text):
        self.queries.append(query_text)
        return "[MEMORY CONTEXT] prefers quiet trips"

    async def extract_and_write(self, user_id, session_id, messages):
        self.writes.append((user_id, session_id, messages))


class FakeGraph:
    def __init__(self):
        self.messages = []

    async def astream(self, payload, config, stream_mode):
        self.messages.extend(payload["messages"])
        yield AIMessage(content="Answer"), {"node": "agent"}

    async def aget_state(self, config):
        return SimpleNamespace(values={"messages": self.messages})


async def test_session_retrieves_first_message_and_writes_on_end():
    memory = FakeMemory()
    graphs = []

    def graph_builder(session_id, user_id, context, prefs=None, store=None):
        graph = FakeGraph()
        graphs.append((graph, context))
        return graph

    class FakePrefs:
        async def get(self, user_id):
            return None

    chat = ChatSession(
        memory,
        graph_builder=graph_builder,
        prefs_service=FakePrefs(),
        store=SessionStore(FakeRedis(decode_responses=True)),
    )
    user_id = uuid.uuid4()
    session_id = await chat.create_session(user_id)

    tokens = [
        token async for token in chat.send_message(session_id, "I prefer quiet trips")
    ]
    assert tokens == ["Answer"]
    assert memory.queries == ["I prefer quiet trips"]
    assert graphs[-1][1] == "[MEMORY CONTEXT] prefers quiet trips"

    await chat.end_session(session_id)
    assert len(memory.writes) == 1
    assert memory.writes[0][0] == user_id
