"""Endpoint /admin/api/meetings/{id}/export — экспорт транскрипта.

Формирует артефакт on-the-fly в одном из форматов: md / txt / json.
Хранилище не трогаем — рендерим ответ прямо из БД-сегментов.
"""

from __future__ import annotations

import io
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Meeting, TranscriptSegmentDB, get_session
from .meetings import _derive_filename, _parse_admin_meta

logger = logging.getLogger(__name__)

export_router = APIRouter(prefix="/meetings", tags=["admin:export"])

# Разрешённые форматы экспорта — любые другие отдают 422.
ExportFormat = Literal["md", "txt", "json"]
_ALLOWED_FORMATS: frozenset[str] = frozenset({"md", "txt", "json"})

# Emoji для markdown-заголовков спикеров. Держим компактный цикл:
# первый появившийся спикер получает первый emoji, и т.д.
_SPEAKER_EMOJI: tuple[str, ...] = (
    "🧑",
    "👩",
    "🧔",
    "👨",
    "🧑‍💻",
    "👩‍💻",
    "🧑‍🔬",
    "👨‍🔬",
)


def _fmt_hms(seconds: float) -> str:
    """Форматирует секунды в `hh:mm:ss`."""
    total = int(max(0.0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _safe_filename(name: str) -> str:
    """Убирает из имени символы, опасные для Content-Disposition/FS.

    Сохраняем non-ASCII символы (кириллица и т.п.) — они уйдут
    через RFC 5987 `filename*=UTF-8''` в Content-Disposition.
    """
    cleaned = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", name).strip()
    # Убираем ведущие точки/пробелы (FS-edge cases) и дубли _
    cleaned = re.sub(r"_+", "_", cleaned).strip("._ ")
    return cleaned or "meeting"


def _ascii_fallback(name: str) -> str:
    """ASCII-only имя для старого `filename=` параметра.

    Все non-ASCII символы заменяем `_`, чтобы HTTP-хедер был декодируем.
    """
    ascii_name = re.sub(r"[^\x20-\x7e]+", "_", name)
    ascii_name = re.sub(r"_+", "_", ascii_name).strip("._ ")
    return ascii_name or "meeting"


def _content_disposition(filename: str) -> str:
    """Собирает Content-Disposition с RFC 5987 для non-ASCII имён."""
    ascii_name = _ascii_fallback(filename)
    # Экранируем " в ASCII-fallback
    safe_ascii = ascii_name.replace('"', "_")
    # Если имя уже ASCII — достаточно filename=
    if filename == ascii_name:
        return f'attachment; filename="{safe_ascii}"'
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded}"


def _pick_title(meeting: Meeting, meta: dict[str, Any]) -> str:
    """Заголовок для экспорта: admin_meta.title → filename → id."""
    title = meta.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    filename = _derive_filename(meeting)
    if filename:
        # Обрезаем расширение (.md/.wav), если оно есть.
        dot = filename.rfind(".")
        if dot > 0:
            return filename[:dot]
        return filename
    return meeting.id


async def _load_segments(
    session: AsyncSession, meeting_id: str
) -> list[TranscriptSegmentDB]:
    rows = (
        (
            await session.execute(
                select(TranscriptSegmentDB)
                .where(TranscriptSegmentDB.meeting_id == meeting_id)
                .order_by(TranscriptSegmentDB.start_time)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def _render_txt(segments: list[TranscriptSegmentDB]) -> str:
    """Строка на сегмент: `[hh:mm:ss] Speaker: text`."""
    lines: list[str] = []
    for row in segments:
        speaker = row.speaker or "Unknown"
        ts = _fmt_hms(float(row.start_time))
        lines.append(f"[{ts}] {speaker}: {row.text}")
    return "\n".join(lines) + ("\n" if lines else "")


def _render_md(
    meeting: Meeting,
    meta: dict[str, Any],
    segments: list[TranscriptSegmentDB],
    title: str,
) -> str:
    """Markdown: заголовок + секции per speaker с timestamp'ами."""
    buf = io.StringIO()
    buf.write(f"# {title}\n\n")
    buf.write(f"- **ID:** `{meeting.id}`\n")
    if meeting.created_at:
        buf.write(f"- **Created:** {meeting.created_at}\n")
    if meeting.duration_seconds:
        buf.write(f"- **Duration:** {_fmt_hms(float(meeting.duration_seconds))}\n")
    tags = meta.get("tags") or []
    if isinstance(tags, list) and tags:
        buf.write(f"- **Tags:** {', '.join(str(t) for t in tags)}\n")
    buf.write("\n")

    if not segments:
        buf.write("_(нет сегментов)_\n")
        return buf.getvalue()

    # Стабильный маппинг speaker → emoji: по порядку появления.
    emoji_map: dict[str, str] = {}
    for row in segments:
        name = row.speaker or "Unknown"
        if name not in emoji_map:
            emoji_map[name] = _SPEAKER_EMOJI[len(emoji_map) % len(_SPEAKER_EMOJI)]

    # Группируем подряд идущие сегменты одного спикера в одну секцию.
    current: str | None = None
    for row in segments:
        name = row.speaker or "Unknown"
        if name != current:
            if current is not None:
                buf.write("\n")
            buf.write(f"## {emoji_map[name]} {name}\n\n")
            current = name
        ts = _fmt_hms(float(row.start_time))
        buf.write(f"- `[{ts}]` {row.text}\n")

    return buf.getvalue()


def _build_speakers_aggregate(
    segments: list[TranscriptSegmentDB],
) -> list[dict[str, Any]]:
    """Для json-экспорта — тот же shape, что SpeakerAggregate."""
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"name": "", "segment_count": 0, "speaking_time_sec": 0.0}
    )
    for row in segments:
        name = row.speaker or "Unknown"
        agg = buckets[name]
        agg["name"] = name
        agg["segment_count"] += 1
        agg["speaking_time_sec"] += float(row.end_time) - float(row.start_time)
    return sorted(buckets.values(), key=lambda s: s["speaking_time_sec"], reverse=True)


def _render_json(
    meeting: Meeting,
    meta: dict[str, Any],
    segments: list[TranscriptSegmentDB],
    title: str,
) -> str:
    """JSON-shape: аналог GET /meetings/{id} + полный список segments."""
    payload: dict[str, Any] = {
        "id": meeting.id,
        "title": title,
        "filename": _derive_filename(meeting),
        "status": meeting.status,
        "duration_sec": meeting.duration_seconds,
        "created_at": meeting.created_at,
        "updated_at": meeting.updated_at,
        "meeting_url": meeting.meeting_url,
        "recording_path": meeting.recording_path,
        "tags": list(meta.get("tags") or []),
        "summary": meta.get("summary"),
        "metrics": dict(meta.get("metrics") or {}),
        "ai_status": meta.get("ai_status"),
        "deleted_at": meta.get("deleted_at"),
        "speakers": _build_speakers_aggregate(segments),
        "segment_count": len(segments),
        "segments": [
            {
                "index": i,
                "speaker": row.speaker,
                "start": float(row.start_time),
                "end": float(row.end_time),
                "text": row.text,
            }
            for i, row in enumerate(segments)
        ],
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


_MEDIA_TYPES: dict[str, str] = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


@export_router.get("/{meeting_id}/export")
async def export_meeting(
    meeting_id: str,
    format: str = Query(default="md", description="md | txt | json"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Экспортирует транскрипт встречи в одном из форматов."""
    fmt = format.lower().strip()
    if fmt not in _ALLOWED_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported format '{format}'. Allowed: md, txt, json",
        )

    meeting = await session.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    meta = _parse_admin_meta(meeting.admin_meta)
    segments = await _load_segments(session, meeting_id)
    title = _pick_title(meeting, meta)

    if fmt == "txt":
        body = _render_txt(segments)
    elif fmt == "md":
        body = _render_md(meeting, meta, segments, title)
    else:  # json
        body = _render_json(meeting, meta, segments, title)

    filename = f"{_safe_filename(title)}.{fmt}"
    return Response(
        content=body,
        media_type=_MEDIA_TYPES[fmt],
        headers={
            "Content-Disposition": _content_disposition(filename),
        },
    )
