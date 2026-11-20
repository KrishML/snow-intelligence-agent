"""Chat session lifecycle; durable state lives in Redis so workers can restart."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.chat.agent import build_chat_graph
from app.chat.memory import MemorySystem
from app.chat.store import SessionStore
from app.plans.service import load_plan_payload
from app.prefs.service import PrefsService


class ChatSession:
    """Owns session metadata and message history in Redis; graphs are rebuilt on demand."""

    def __init__(
        self,
        memory_system: MemorySystem | None = None,
        *,
        graph_builder=build_chat_graph,
        prefs_service: PrefsService | None = None,
        store: SessionStore | None = None,
        plan_loader=load_plan_payload,
    ) -> None:
        self._memory = memory_system or MemorySystem()
        self._graph_builder = graph_builder
        self._prefs = prefs_service or PrefsService()
        self._store = store or SessionStore()
        self._plan_loader = plan_loader
        self._graphs: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def create_session(
        self,
        user_id: uuid.UUID,
        initial_message: str = "",
    ) -> str:
        """Create a session ID and initialize its graph and optional memory context."""
        session_id = str(uuid.uuid4())
        memory_context = ""
        memory_loaded = bool(initial_message.strip())
        if memory_loaded:
            memory_context = await self._memory.retrieve_context(
                user_id, initial_message
            )
        await self._store.create(
            session_id,
            user_id,
            memory_context=memory_context,
            memory_loaded=memory_loaded,
        )
        await self._build_graph(session_id, user_id, memory_context)
        return session_id

    async def send_message(self, session_id: str, user_message: str) -> AsyncIterator[str]:
        """Yield streamed assistant text for a user message."""
        meta = await self._require_meta(session_id)
        user_id = uuid.UUID(meta["user_id"])
        async with self._lock(session_id):
            memory_context = meta["memory_context"]
            if not meta["memory_loaded"]:
                memory_context = await self._memory.retrieve_context(
                    user_id, user_message
                )
                await self._store.update_meta(
                    session_id, memory_context=memory_context, memory_loaded=True
                )
                await self._build_graph(session_id, user_id, memory_context)
            graph = await self._graph(session_id, user_id, memory_context)
            config = {"configurable": {"thread_id": session_id}}
            async for chunk, _metadata in graph.astream(
                {"messages": [HumanMessage(content=user_message)]},
                config=config,
                stream_mode="messages",
            ):
                async for item in self._events_for(chunk, user_id):
                    yield item
            state = await graph.aget_state(config)
            await self._store.save_messages(
                session_id, list(state.values.get("messages", []))
            )

    async def end_session(self, session_id: str) -> None:
        """Extract memory before removing the session."""
        meta = await self._require_meta(session_id)
        async with self._lock(session_id):
            messages = await self._store.load_messages(session_id)
            if messages:
                await self._memory.extract_and_write(
                    uuid.UUID(meta["user_id"]), session_id, messages
                )
            await self._store.delete(session_id)
            self._graphs.pop(session_id, None)
        self._locks.pop(session_id, None)

    async def owns_session(self, session_id: str, user_id: uuid.UUID) -> bool:
        meta = await self._store.get_meta(session_id)
        return meta is not None and meta["user_id"] == str(user_id)

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def _events_for(
        self, chunk: Any, user_id: uuid.UUID
    ) -> AsyncIterator[str | dict[str, Any]]:
        """Translate a graph stream chunk into text tokens and typed UI events."""
        if isinstance(chunk, ToolMessage):
            if chunk.name == "create_plan_artifact" and chunk.status != "error":
                plan = await self._load_plan_card(chunk.content, user_id)
                if plan is not None:
                    yield {"type": "plan_card", "plan": plan}
            return
        if not isinstance(chunk, AIMessage):
            return
        for call in getattr(chunk, "tool_call_chunks", None) or []:
            if call.get("name"):
                yield {"type": "tool_call", "name": call["name"]}
        content = chunk.content
        if isinstance(content, str) and content:
            yield content
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    yield part["text"]

    async def _load_plan_card(
        self, content: Any, user_id: uuid.UUID
    ) -> dict[str, Any] | None:
        try:
            plan_id = uuid.UUID(json.loads(content)["plan_id"])
        except (TypeError, ValueError, KeyError):
            return None
        return await self._plan_loader(plan_id, user_id)

    async def _require_meta(self, session_id: str) -> dict[str, Any]:
        meta = await self._store.get_meta(session_id)
        if meta is None:
            raise KeyError(f"Unknown chat session: {session_id}")
        return meta

    async def _build_graph(
        self, session_id: str, user_id: uuid.UUID, memory_context: str
    ) -> Any:
        graph = self._graph_builder(
            session_id,
            user_id,
            memory_context,
            prefs=await self._prefs.get(user_id),
            store=self._store,
        )
        self._graphs[session_id] = graph
        return graph

    async def _graph(
        self, session_id: str, user_id: uuid.UUID, memory_context: str
    ) -> Any:
        """Return the cached graph, restoring history from Redis after a restart."""
        graph = self._graphs.get(session_id)
        if graph is not None:
            return graph
        graph = await self._build_graph(session_id, user_id, memory_context)
        messages = await self._store.load_messages(session_id)
        if messages:
            await graph.aupdate_state(
                {"configurable": {"thread_id": session_id}},
                {"messages": messages},
                as_node="agent",
            )
        return graph


async def stream_session_message(
    chat_session: ChatSession, session_id: str, message: str
) -> AsyncIterator[str]:
    """Typed async-generator wrapper for API/WebSocket consumers."""
    async for token in chat_session.send_message(session_id, message):
        yield token
