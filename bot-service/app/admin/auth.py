"""Basic-auth для admin-панели TeleScribe."""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_security = HTTPBasic()


async def require_basic_auth(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(_security),
) -> str:
    """Проверяет Basic-creds по app.state.admin_creds. Возвращает имя."""
    expected = getattr(request.app.state, "admin_creds", None)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin credentials not configured",
        )
    username_ok = secrets.compare_digest(credentials.username, expected[0])
    password_ok = secrets.compare_digest(credentials.password, expected[1])
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
