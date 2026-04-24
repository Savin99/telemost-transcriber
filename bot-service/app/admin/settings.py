"""Endpoints /admin/api/settings — read/PATCH overlay без переписывания env.

Хранится одним JSON-файлом в ADMIN_SETTINGS_PATH (default
/root/telemost/admin_settings.json). Запись — atomic через tempfile + os.replace.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from .schemas import AdminSettings, AdminSettingsUpdate

logger = logging.getLogger(__name__)

settings_router = APIRouter(prefix="/settings", tags=["admin:settings"])


_DEFAULT_PATH = "/root/telemost/admin_settings.json"


def _settings_path() -> Path:
    raw = os.getenv("ADMIN_SETTINGS_PATH", "").strip() or _DEFAULT_PATH
    return Path(raw)


def _load_raw() -> dict[str, Any]:
    """Читает JSON с диска. Если файла нет или битый — возвращает {}."""
    path = _settings_path()
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("admin_settings.json read failed (%s); returning empty", exc)
        return {}


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Atomic write: tempfile в том же каталоге + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile в том же dir, чтобы os.replace был на одном FS.
    fd, tmp_path = tempfile.mkstemp(
        prefix=".admin_settings-", suffix=".json.tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Чистим tmp, если что-то упало до os.replace.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _merge_settings(raw: dict[str, Any]) -> AdminSettings:
    """Делает AdminSettings из raw-dict: пустые секции — дефолты."""
    try:
        return AdminSettings.model_validate(raw)
    except Exception as exc:
        logger.warning(
            "admin_settings.json validation failed (%s); using defaults", exc
        )
        return AdminSettings()


@settings_router.get("")
async def get_settings() -> dict[str, Any]:
    """Полный AdminSettings (дефолты + то, что сохранено)."""
    raw = _load_raw()
    return _merge_settings(raw).model_dump()


@settings_router.patch("")
async def patch_settings(payload: AdminSettingsUpdate) -> dict[str, Any]:
    """Partial update: сливает секции, пишет atomic на диск, возвращает полный AdminSettings."""
    raw = _load_raw()
    updates = payload.model_dump(exclude_unset=True)

    for section, section_value in updates.items():
        if section_value is None:
            continue
        existing = raw.get(section)
        if isinstance(existing, dict) and isinstance(section_value, dict):
            existing.update(section_value)
            raw[section] = existing
        else:
            raw[section] = section_value

    # Валидируем перед записью, чтобы не сохранять мусор.
    try:
        merged = AdminSettings.model_validate(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid settings: {exc}")

    try:
        _atomic_write(_settings_path(), raw)
    except OSError as exc:
        logger.error("Failed to persist admin_settings.json: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to persist settings: {exc}"
        )

    return merged.model_dump()
