"""Router /admin/api/* под HTTP Basic auth."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .auth import require_basic_auth

admin_router = APIRouter(
    prefix="/admin/api",
    tags=["admin"],
    dependencies=[Depends(require_basic_auth)],
)


@admin_router.get("/me")
async def admin_me(username: str = Depends(require_basic_auth)) -> dict[str, str]:
    """Smoke-endpoint: возвращает имя текущего admin-пользователя."""
    return {"username": username}
