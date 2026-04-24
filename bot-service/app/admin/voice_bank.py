"""Прокси /admin/api/voice-bank/* на transcriber-service."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

voice_bank_router = APIRouter(prefix="/voice-bank", tags=["admin:voice-bank"])


def _transcriber_url() -> str:
    return os.getenv("TRANSCRIBER_URL", "http://localhost:8001")


class VoiceBankRenameRequest(BaseModel):
    new_name: str


class VoiceBankMergeRequest(BaseModel):
    source: str
    target: str


async def _proxy_request(
    method: str,
    path: str,
    *,
    json: Any = None,
    timeout: float = 60.0,
) -> Any:
    """Прокси-запрос на transcriber; прокидывает status_code/detail."""
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


@voice_bank_router.get("/speakers")
async def list_speakers() -> list[dict[str, Any]]:
    """GET /voice-bank/speakers → список с n_samples/is_known/..."""
    return await _proxy_request("GET", "/voice-bank/speakers")


@voice_bank_router.delete("/{name}")
async def delete_speaker(name: str) -> dict[str, Any]:
    """DELETE /voice-bank/{name}."""
    return await _proxy_request("DELETE", f"/voice-bank/{name}")


@voice_bank_router.post("/{name}/rename")
async def rename_speaker(name: str, request: VoiceBankRenameRequest) -> dict[str, Any]:
    """POST /voice-bank/{name}/rename {new_name}. 409 если занят."""
    return await _proxy_request(
        "POST",
        f"/voice-bank/{name}/rename",
        json={"new_name": request.new_name},
    )


@voice_bank_router.post("/merge")
async def merge_speakers(request: VoiceBankMergeRequest) -> dict[str, Any]:
    """POST /voice-bank/merge {source, target}."""
    return await _proxy_request(
        "POST",
        "/voice-bank/merge",
        json={"source": request.source, "target": request.target},
    )


@voice_bank_router.get("/similarity-matrix")
async def similarity_matrix() -> dict[str, dict[str, float]]:
    """GET /voice-bank/similarity-matrix → попарные cosine."""
    return await _proxy_request("GET", "/voice-bank/similarity-matrix")
