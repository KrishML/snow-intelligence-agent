"""One-time codes that link a Telegram chat to the user's preferences."""

from __future__ import annotations

import secrets
import uuid

from redis.asyncio import Redis

_PREFIX = "snow-agent:telegram-link:"
TTL_SECONDS = 600


class LinkCodeStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def create(self, user_id: uuid.UUID) -> str:
        code = secrets.token_urlsafe(9)
        await self._redis.set(_PREFIX + code, str(user_id), ex=TTL_SECONDS)
        return code

    async def consume(self, code: str) -> uuid.UUID | None:
        """Return the user the code belongs to and invalidate it (single use)."""
        value = await self._redis.getdel(_PREFIX + code)
        return uuid.UUID(value) if value else None
