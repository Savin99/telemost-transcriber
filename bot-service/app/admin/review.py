"""Прокси /admin/api/review/* на transcriber-service."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

review_router = APIRouter(prefix="/review", tags=["admin:review"])


def _transcriber_url() -> str:
    return os.getenv("TRANSCRIBER_URL", "http://localhost:8001")


class ReviewApplyRequest(BaseModel):
    """Payload для apply: name — итоговое имя спикера; alpha — EMA коэффициент."""

    name: str
    alpha: float = 0.05


async def _proxy_request(
    method: str,
    path: str,
    *,
    json: Any = None,
    timeout: float = 600.0,
) -> Any:
    url = f"{_transcriber_url()}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.request(method, url, json=json)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=exc.response.text,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc))
    if response.content:
        return response.json()
    return None


@review_router.get("")
async def list_review_queue() -> list[dict[str, Any]]:
    """GET /admin/api/review → прокси /review-queue."""
    return await _proxy_request("GET", "/review-queue", timeout=60.0)


@review_router.post("/{meeting_id}/{cluster_label}/apply")
async def apply_review_label(
    meeting_id: str,
    cluster_label: str,
    request: ReviewApplyRequest,
) -> dict[str, Any]:
    """POST /admin/api/review/{meeting_id}/{cluster_label}/apply.

    Здесь ``meeting_id`` — это meeting_key из review-очереди (тот же, что
    используется в transcriber), а ``cluster_label`` — метка кластера вида
    ``SPEAKER_00``. Под капотом идёт POST на transcriber
    ``/speaker-review/{meeting_key}/{speaker_label}/label`` (пайплайн
    learn_from_diarization_label).
    """
    return await _proxy_request(
        "POST",
        f"/speaker-review/{meeting_id}/{cluster_label}/label",
        json={"name": request.name, "alpha": request.alpha},
    )
