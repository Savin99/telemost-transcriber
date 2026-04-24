"""Router /admin/api/* под HTTP Basic auth."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .auth import require_basic_auth
from .meetings import meetings_router
from .metrics import metrics_router
from .review import review_router
from .settings import settings_router
from .voice_bank import voice_bank_router

admin_router = APIRouter(
    prefix="/admin/api",
    tags=["admin"],
    dependencies=[Depends(require_basic_auth)],
)


@admin_router.get("/me")
async def admin_me(username: str = Depends(require_basic_auth)) -> dict[str, str]:
    """Smoke-endpoint: возвращает имя текущего admin-пользователя."""
    return {"username": username}


admin_router.include_router(meetings_router)
admin_router.include_router(voice_bank_router)
admin_router.include_router(review_router)
admin_router.include_router(metrics_router)
admin_router.include_router(settings_router)
