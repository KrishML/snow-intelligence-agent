"""Bearer-token authentication shared by all API routers."""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.settings import settings

_bearer = HTTPBearer(auto_error=False)


def authenticated_user(token: str | None) -> uuid.UUID:
    if not settings.api_bearer_token:
        raise HTTPException(
            status_code=503,
            detail="API authentication is not configured.",
        )
    if token is None or not hmac.compare_digest(token, settings.api_bearer_token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token.")
    return settings.default_user_id


def require_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
) -> uuid.UUID:
    return authenticated_user(credentials.credentials if credentials else None)


def require_admin(
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> uuid.UUID:
    if user_id not in settings.admin_user_ids:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user_id
