"""Authenticated chat-session API routes."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.auth import authenticated_user as _authenticated_user
from app.auth import require_user
from app.chat.session import ChatSession
from app.chat.store import SessionStoreError

logger = logging.getLogger(__name__)
router = APIRouter()
chat_sessions = ChatSession()


class CreateSessionRequest(BaseModel):
    initial_message: str = Field(default="", max_length=12000)


@router.post("/api/chat/session")
async def create_chat_session(
    user_id: Annotated[uuid.UUID, Depends(require_user)],
    body: CreateSessionRequest | None = None,
) -> dict[str, str]:
    try:
        session_id = await chat_sessions.create_session(
            user_id, initial_message=body.initial_message if body else ""
        )
    except SessionStoreError as exc:
        logger.error("Session store unavailable: %s", exc)
        raise HTTPException(
            status_code=503, detail="Chat session store is unavailable."
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"session_id": session_id}


@router.websocket("/api/chat/session/{session_id}/messages")
async def chat_messages(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    authorization = websocket.headers.get("authorization", "")
    token = authorization[7:] if authorization.lower().startswith("bearer ") else None
    if token is None:
        try:
            auth_message = await websocket.receive_json()
        except (WebSocketDisconnect, ValueError):
            await websocket.close(code=4401, reason="Authentication required")
            return
        token = (
            auth_message.get("token")
            if isinstance(auth_message, dict) and auth_message.get("type") == "auth"
            else None
        )
    try:
        user_id = _authenticated_user(token)
    except HTTPException as exc:
        await websocket.close(code=4401, reason=exc.detail)
        return
    try:
        owned = await chat_sessions.owns_session(session_id, user_id)
    except SessionStoreError:
        await websocket.send_json(
            {"type": "error", "detail": "Chat session store is unavailable."}
        )
        await websocket.close(code=1013, reason="Session store unavailable")
        return
    if not owned:
        await websocket.close(code=4404, reason="Unknown chat session")
        return

    try:
        while True:
            message = await websocket.receive_json()
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                await websocket.send_json(
                    {"type": "error", "detail": "Message content must be non-empty text."}
                )
                continue
            async for item in chat_sessions.send_message(session_id, content):
                # Plain strings are text tokens; dicts are typed events (tool_call, plan_card).
                if isinstance(item, str):
                    await websocket.send_json({"type": "token", "content": item})
                else:
                    await websocket.send_json(jsonable_encoder(item))
            await websocket.send_json({"type": "message_complete"})
    except WebSocketDisconnect:
        return
    except KeyError:
        await websocket.close(code=4404, reason="Unknown chat session")
    except SessionStoreError:
        await websocket.send_json(
            {"type": "error", "detail": "Chat session store is unavailable."}
        )
        await websocket.close(code=1013, reason="Session store unavailable")
    except Exception:
        logger.exception("Chat WebSocket message processing failed")
        await websocket.send_json(
            {"type": "error", "detail": "Message processing failed."}
        )
        await websocket.close(code=1011, reason="Message processing failed")


@router.post("/api/chat/session/{session_id}/end")
async def end_chat_session(
    session_id: str,
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> dict[str, str]:
    try:
        owned = await chat_sessions.owns_session(session_id, user_id)
        if owned:
            await chat_sessions.end_session(session_id)
    except SessionStoreError as exc:
        raise HTTPException(
            status_code=503, detail="Chat session store is unavailable."
        ) from exc
    if not owned:
        raise HTTPException(status_code=404, detail="Unknown chat session.")
    return {"status": "closed"}
