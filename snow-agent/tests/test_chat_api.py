from __future__ import annotations

from fastapi.testclient import TestClient

from app.chat import api
from app.main import app
from app.settings import settings


class FakeChatSessions:
    def __init__(self):
        self.ended = []

    async def create_session(self, user_id, initial_message=""):
        return "session-test"

    async def owns_session(self, session_id, user_id):
        return session_id == "session-test"

    async def send_message(self, session_id, content):
        yield f"Echo: {content}"

    async def end_session(self, session_id):
        self.ended.append(session_id)


def test_chat_session_requires_bearer_auth(monkeypatch):
    monkeypatch.setattr(settings, "api_bearer_token", "test-token")
    monkeypatch.setattr(api, "chat_sessions", FakeChatSessions())

    with TestClient(app) as client:
        response = client.post("/api/chat/session")

    assert response.status_code == 401


def test_authenticated_chat_session_can_stream_and_end(monkeypatch):
    monkeypatch.setattr(settings, "api_bearer_token", "test-token")
    fake_sessions = FakeChatSessions()
    monkeypatch.setattr(api, "chat_sessions", fake_sessions)

    with TestClient(app) as client:
        created = client.post(
            "/api/chat/session",
            headers={"Authorization": "Bearer test-token"},
            json={"initial_message": "Snowy weekend trips"},
        )
        assert created.status_code == 200
        assert created.json() == {"session_id": "session-test"}

        with client.websocket_connect(
            "/api/chat/session/session-test/messages"
        ) as websocket:
            websocket.send_json({"type": "auth", "token": "test-token"})
            websocket.send_json({"content": "Tell me about Zermatt"})
            assert websocket.receive_json() == {
                "type": "token",
                "content": "Echo: Tell me about Zermatt",
            }
            assert websocket.receive_json() == {"type": "message_complete"}

        ended = client.post(
            "/api/chat/session/session-test/end",
            headers={"Authorization": "Bearer test-token"},
        )

    assert ended.status_code == 200
    assert ended.json() == {"status": "closed"}
    assert fake_sessions.ended == ["session-test"]


def test_chat_session_rejects_misconfigured_auth(monkeypatch):
    monkeypatch.setattr(settings, "api_bearer_token", "")

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/session",
            headers={"Authorization": "Bearer anything"},
        )

    assert response.status_code == 503
