"""
Mocked integration tests for the shared OpenAI async client — task 3.2.

All external OpenAI calls are intercepted with ``unittest.mock.AsyncMock`` via
pytest-mock's ``mocker`` fixture.  No real API keys or network I/O are needed.

Tests cover:
  - complete_json: happy path, APIError, non-JSON response
  - embed: happy path, wrong-dimension response
  - missing API key raises OpenAIClientError at call time
  - complete_vision: reads image file, passes base64 image_url to API

Requirements: 10.3, 11.2
"""

from __future__ import annotations

import struct
import zlib

import openai
import pytest

from app.clients.openai_client import OpenAIClient, OpenAIClientError
from app.settings import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png_bytes() -> bytes:
    """Return a minimal valid 1×1 white PNG byte string."""
    # PNG signature
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        crc = struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return length + tag + data + crc

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    # 1×1 RGB pixel: filter byte 0x00 then 3 bytes for white
    raw_row = b"\x00\xFF\xFF\xFF"
    compressed = zlib.compress(raw_row)
    idat = chunk(b"IDAT", compressed)
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_api_key():
    """Guarantee a non-empty API key for tests that need the client to proceed."""
    original = settings.openai_api_key
    settings.openai_api_key = "test-key"
    yield
    settings.openai_api_key = original


# ---------------------------------------------------------------------------
# Helper to build a mock chat completion response
# ---------------------------------------------------------------------------


def _mock_chat_response(content: str, mocker):
    """Return a mock object shaped like openai.ChatCompletion."""
    mock_msg = mocker.MagicMock()
    mock_msg.content = content
    mock_choice = mocker.MagicMock()
    mock_choice.message = mock_msg
    mock_response = mocker.MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


def _mock_embed_response(embedding: list[float], mocker):
    """Return a mock object shaped like openai.EmbeddingsResponse."""
    mock_datum = mocker.MagicMock()
    mock_datum.embedding = embedding
    mock_response = mocker.MagicMock()
    mock_response.data = [mock_datum]
    return mock_response


# ---------------------------------------------------------------------------
# Tests: complete_json
# ---------------------------------------------------------------------------


async def test_complete_json_returns_parsed_dict(mocker):
    """complete_json should parse the JSON string returned by the API."""
    content = '{"summary": "Great snow", "sentiment": "positive"}'
    mock_response = _mock_chat_response(content, mocker)

    mock_create = mocker.AsyncMock(return_value=mock_response)
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create = mock_create
    mocker.patch("openai.AsyncOpenAI", return_value=mock_client)

    client = OpenAIClient()
    result = await client.complete_json(
        system_prompt="You are a ski conditions analyser.",
        user_prompt="Summarise conditions for Val d'Isère.",
    )

    assert result == {"summary": "Great snow", "sentiment": "positive"}


async def test_complete_json_raises_on_api_error(mocker):
    """complete_json should raise OpenAIClientError when the API returns an error."""
    mock_request = mocker.MagicMock()
    mock_create = mocker.AsyncMock(
        side_effect=openai.APIError("test", request=mock_request, body=None)
    )
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create = mock_create
    mocker.patch("openai.AsyncOpenAI", return_value=mock_client)

    client = OpenAIClient()
    with pytest.raises(OpenAIClientError):
        await client.complete_json(
            system_prompt="sys",
            user_prompt="user",
        )


async def test_complete_json_raises_on_non_json_response(mocker):
    """complete_json should raise OpenAIClientError when the response is not valid JSON."""
    mock_response = _mock_chat_response("not json", mocker)
    mock_create = mocker.AsyncMock(return_value=mock_response)
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create = mock_create
    mocker.patch("openai.AsyncOpenAI", return_value=mock_client)

    client = OpenAIClient()
    with pytest.raises(OpenAIClientError):
        await client.complete_json(
            system_prompt="sys",
            user_prompt="user",
        )


# ---------------------------------------------------------------------------
# Tests: embed
# ---------------------------------------------------------------------------


async def test_embed_returns_list_of_1536_floats(mocker):
    """embed should return exactly 1536 floats on a successful API call."""
    expected = [0.1] * 1536
    mock_response = _mock_embed_response(expected, mocker)
    mock_create = mocker.AsyncMock(return_value=mock_response)
    mock_client = mocker.MagicMock()
    mock_client.embeddings.create = mock_create
    mocker.patch("openai.AsyncOpenAI", return_value=mock_client)

    client = OpenAIClient()
    result = await client.embed("Great powder at Verbier today.")

    assert result == expected
    assert len(result) == 1536


async def test_embed_raises_on_wrong_dimension(mocker):
    """embed should raise OpenAIClientError when the returned vector is not 1536-dimensional."""
    mock_response = _mock_embed_response([0.1] * 512, mocker)
    mock_create = mocker.AsyncMock(return_value=mock_response)
    mock_client = mocker.MagicMock()
    mock_client.embeddings.create = mock_create
    mocker.patch("openai.AsyncOpenAI", return_value=mock_client)

    client = OpenAIClient()
    with pytest.raises(OpenAIClientError):
        await client.embed("some text")


# ---------------------------------------------------------------------------
# Tests: missing API key
# ---------------------------------------------------------------------------


async def test_missing_api_key_raises_at_call_time(mocker):
    """A missing API key should raise OpenAIClientError at call time, not at import."""
    original = settings.openai_api_key
    settings.openai_api_key = ""
    try:
        client = OpenAIClient()
        with pytest.raises(OpenAIClientError):
            await client.embed("test")
    finally:
        settings.openai_api_key = original


# ---------------------------------------------------------------------------
# Tests: complete_vision
# ---------------------------------------------------------------------------


async def test_complete_vision_passes_base64_image_url(mocker, tmp_path):
    """complete_vision should read the image file and pass a base64 image_url to the API."""
    png_path = tmp_path / "webcam.png"
    png_path.write_bytes(_make_png_bytes())

    content = '{"classification": "snow_good", "description": "looks snowy"}'
    mock_response = _mock_chat_response(content, mocker)
    mock_create = mocker.AsyncMock(return_value=mock_response)
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create = mock_create
    mocker.patch("openai.AsyncOpenAI", return_value=mock_client)

    client = OpenAIClient()
    result = await client.complete_vision(
        system_prompt="Classify this webcam image.",
        user_text="What snow conditions are visible?",
        image_path=png_path,
    )

    # Returned dict should have the expected classification
    assert result["classification"] == "snow_good"

    # The call to chat.completions.create must have been made
    mock_create.assert_awaited_once()
    call_kwargs = mock_create.call_args.kwargs
    messages = call_kwargs["messages"]

    # Find the user message (it carries the image_url)
    user_message = next(m for m in messages if m["role"] == "user")
    content_parts = user_message["content"]

    # There must be an image_url entry in the content parts
    image_parts = [p for p in content_parts if p.get("type") == "image_url"]
    assert len(image_parts) == 1, "Expected exactly one image_url content part"

    image_url_value = image_parts[0]["image_url"]["url"]
    assert image_url_value.startswith("data:image/png;base64,"), (
        f"Expected a base64 data URL, got: {image_url_value[:60]}"
    )
