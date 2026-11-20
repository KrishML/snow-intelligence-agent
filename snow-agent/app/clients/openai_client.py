"""Shared async OpenAI client for Phase 2 components.

Provides three async methods:
  - complete_json()   — GPT-4o-mini structured JSON output
  - complete_vision() — GPT-4o vision with base64 image
  - embed()           — text-embedding-3-small, returns list[float] of length 1536
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import openai

from app.settings import settings

logger = logging.getLogger(__name__)

_EMBEDDING_DIM = 1536


class OpenAIClient:
    """Thin async wrapper around the OpenAI Python SDK.

    All methods raise ``OpenAIClientError`` on unrecoverable failures.
    A missing or empty ``settings.openai_api_key`` causes ``OpenAIClientError``
    to be raised at call time (not at import time) so the app starts without keys.
    """

    def _get_client(self) -> openai.AsyncOpenAI:
        if not settings.openai_api_key:
            raise OpenAIClientError("OPENAI_API_KEY is not configured")
        return openai.AsyncOpenAI(api_key=settings.openai_api_key)

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Call GPT-4o-mini (or the configured model) requesting a JSON object.

        Returns the parsed dict.  Raises ``OpenAIClientError`` on API error or
        if the response cannot be parsed as JSON.
        """
        effective_model = model or settings.openai_model_qualitative
        client = self._get_client()
        try:
            response = await client.chat.completions.create(
                model=effective_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                timeout=settings.openai_vision_timeout_seconds,
            )
            raw = response.choices[0].message.content or "{}"
            return json.loads(raw)
        except openai.OpenAIError as exc:
            logger.error("OpenAI complete_json failed: %s", exc)
            raise OpenAIClientError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            logger.error("OpenAI complete_json returned non-JSON: %s", exc)
            raise OpenAIClientError(f"Non-JSON response: {exc}") from exc

    async def complete_vision(
        self,
        system_prompt: str,
        user_text: str,
        image_path: str | Path,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Call GPT-4o vision with a local PNG image encoded as base64.

        Returns the parsed JSON dict from the model response.
        Raises ``OpenAIClientError`` on API error or JSON parse failure.
        """
        effective_model = model or settings.openai_model_chat
        client = self._get_client()

        image_data = Path(image_path).read_bytes()
        b64 = base64.b64encode(image_data).decode("utf-8")
        data_url = f"data:image/png;base64,{b64}"

        try:
            response = await client.chat.completions.create(
                model=effective_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
                timeout=settings.openai_vision_timeout_seconds,
            )
            raw = response.choices[0].message.content or "{}"
            return json.loads(raw)
        except openai.OpenAIError as exc:
            logger.error("OpenAI complete_vision failed: %s", exc)
            raise OpenAIClientError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            logger.error("OpenAI complete_vision returned non-JSON: %s", exc)
            raise OpenAIClientError(f"Non-JSON response: {exc}") from exc

    async def embed(self, text: str) -> list[float]:
        """Embed *text* using text-embedding-3-small.

        Returns a list of exactly 1536 floats.
        Raises ``OpenAIClientError`` on API failure.
        """
        client = self._get_client()
        try:
            response = await client.embeddings.create(
                model=settings.openai_embedding_model,
                input=text,
                encoding_format="float",
            )
            vector = response.data[0].embedding
            if len(vector) != _EMBEDDING_DIM:
                raise OpenAIClientError(
                    f"Unexpected embedding dimension: {len(vector)} (expected {_EMBEDDING_DIM})"
                )
            return vector
        except openai.OpenAIError as exc:
            logger.error("OpenAI embed failed: %s", exc)
            raise OpenAIClientError(str(exc)) from exc

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in one request, preserving input order."""
        if not texts:
            return []
        client = self._get_client()
        try:
            response = await client.embeddings.create(
                model=settings.openai_embedding_model,
                input=texts,
                encoding_format="float",
                timeout=settings.openai_vision_timeout_seconds,
            )
            vectors = [
                datum.embedding
                for datum in sorted(response.data, key=lambda item: item.index)
            ]
            if len(vectors) != len(texts):
                raise OpenAIClientError(
                    f"Unexpected embedding count: {len(vectors)} (expected {len(texts)})"
                )
            if any(len(vector) != _EMBEDDING_DIM for vector in vectors):
                raise OpenAIClientError(
                    f"Embedding response must contain vectors of dimension {_EMBEDDING_DIM}"
                )
            return vectors
        except openai.OpenAIError as exc:
            logger.error("OpenAI batch embedding failed: %s", exc)
            raise OpenAIClientError(str(exc)) from exc


class OpenAIClientError(Exception):
    """Raised when an OpenAI API call fails unrecoverably."""
