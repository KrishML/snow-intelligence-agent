"""Redis-backed chat session state with a 24-hour TTL on every key."""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.settings import settings

_PREFIX = "snow-agent:session"


class SessionStoreError(RuntimeError):
    """Redis is unreachable or returned an error; sessions must not silently degrade."""


def create_redis_client() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


class SessionStore:
    def __init__(self, redis: Redis | None = None, ttl_seconds: int | None = None) -> None:
        self._redis = redis or create_redis_client()
        self._ttl = ttl_seconds or settings.redis_session_ttl_seconds

    @staticmethod
    def _key(session_id: str, part: str) -> str:
        return f"{_PREFIX}:{session_id}:{part}"

    async def _guard(self, coro):
        try:
            return await coro
        except RedisError as exc:
            raise SessionStoreError(f"Session store unavailable: {exc}") from exc

    async def _set_json(self, session_id: str, part: str, value: Any) -> None:
        await self._guard(
            self._redis.set(self._key(session_id, part), json.dumps(value), ex=self._ttl)
        )

    async def _get_json(self, session_id: str, part: str) -> Any | None:
        raw = await self._guard(self._redis.get(self._key(session_id, part)))
        return json.loads(raw) if raw is not None else None

    async def _touch(self, session_id: str) -> None:
        for part in ("meta", "messages", "live-snow", "flights", "hotels"):
            await self._guard(self._redis.expire(self._key(session_id, part), self._ttl))

    async def create(
        self,
        session_id: str,
        user_id: uuid.UUID,
        *,
        memory_context: str,
        memory_loaded: bool,
    ) -> None:
        await self._set_json(
            session_id,
            "meta",
            {
                "user_id": str(user_id),
                "memory_context": memory_context,
                "memory_loaded": memory_loaded,
                "plan_created": False,
            },
        )

    async def get_meta(self, session_id: str) -> dict[str, Any] | None:
        meta = await self._get_json(session_id, "meta")
        if meta is not None:
            await self._touch(session_id)
        return meta

    async def update_meta(self, session_id: str, **changes: Any) -> None:
        meta = await self._get_json(session_id, "meta")
        if meta is None:
            raise KeyError(f"Unknown chat session: {session_id}")
        meta.update(changes)
        await self._set_json(session_id, "meta", meta)

    async def save_messages(self, session_id: str, messages: list[BaseMessage]) -> None:
        await self._set_json(session_id, "messages", messages_to_dict(messages))

    async def load_messages(self, session_id: str) -> list[BaseMessage]:
        stored = await self._get_json(session_id, "messages")
        return messages_from_dict(stored) if stored else []

    async def reserve_live_call(self, session_id: str, limit: int) -> bool:
        """Atomically count one live lookup; False once the session limit is reached."""
        key = self._key(session_id, "live-snow")
        count = await self._guard(self._redis.incr(key))
        await self._guard(self._redis.expire(key, self._ttl))
        if count > limit:
            await self._guard(self._redis.decr(key))
            return False
        return True

    async def save_flight_options(
        self, session_id: str, offers: list[dict[str, Any]]
    ) -> None:
        await self._set_json(session_id, "flights", offers)

    async def load_flight_options(self, session_id: str) -> list[dict[str, Any]]:
        return await self._get_json(session_id, "flights") or []

    async def save_hotel_options(
        self, session_id: str, offers: list[dict[str, Any]]
    ) -> None:
        await self._set_json(session_id, "hotels", offers)

    async def load_hotel_options(self, session_id: str) -> list[dict[str, Any]]:
        return await self._get_json(session_id, "hotels") or []

    async def mark_plan_created(self, session_id: str) -> None:
        await self.update_meta(session_id, plan_created=True)

    async def delete(self, session_id: str) -> None:
        keys = [
            self._key(session_id, part)
            for part in ("meta", "messages", "live-snow", "flights", "hotels")
        ]
        await self._guard(self._redis.delete(*keys))
