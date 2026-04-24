"""Endpoints /admin/api/meetings[/{id}] — read + mutations + audio."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Meeting, TranscriptSegmentDB, get_session
from .schemas import (
    AiStatus,
    BulkAction,
    BulkResult,
    DriveInfo,
    MeetingDetail,
    MeetingListItem,
    MeetingListResponse,
    MeetingUpdate,
    SegmentUpdate,
    SpeakerAggregate,
    TranscriptSegmentOut,
)

logger = logging.getLogger(__name__)

meetings_router = APIRouter(prefix="/meetings", tags=["admin:meetings"])


def _parse_admin_meta(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        logger.warning("admin_meta JSON parse failed; returning empty dict")
        return {}


def _build_drive(meeting: Meeting) -> DriveInfo | None:
    if not (meeting.drive_file_id or meeting.drive_web_view_link):
        return None
    return DriveInfo(
        md_file_id=meeting.drive_file_id,
        md_web_link=meeting.drive_web_view_link,
        filename=meeting.drive_filename,
        folder_id=meeting.drive_folder_id,
    )


def _derive_filename(meeting: Meeting) -> str | None:
    if meeting.drive_filename:
        return meeting.drive_filename
    if meeting.recording_path:
        return os.path.basename(meeting.recording_path)
    return None


def _meeting_base_fields(
    meeting: Meeting,
    meta: dict[str, Any],
    speakers: list[SpeakerAggregate],
    segment_count: int,
) -> dict[str, Any]:
    ai_status_raw = meta.get("ai_status")
    ai_status = (
        AiStatus.model_validate(ai_status_raw)
        if isinstance(ai_status_raw, dict)
        else None
    )
    unknown_count = sum(
        1
        for s in speakers
        if not s.name or s.name.lower().startswith(("speaker_", "unknown"))
    )
    return {
        "id": meeting.id,
        "title": meta.get("title"),
        "filename": _derive_filename(meeting),
        "duration_sec": meeting.duration_seconds,
        "created_at": meeting.created_at,
        "updated_at": meeting.updated_at,
        "status": meeting.status,
        "tags": list(meta.get("tags") or []),
        "segment_count": segment_count,
        "speakers": speakers,
        "unknown_speaker_count": unknown_count,
        "ai_status": ai_status,
        "drive": _build_drive(meeting),
        "metrics": dict(meta.get("metrics") or {}),
        "summary": meta.get("summary"),
        "error_message": meeting.error_message,
        "deleted_at": meta.get("deleted_at"),
    }


async def _aggregate_speakers_bulk(
    session: AsyncSession, meeting_ids: list[str]
) -> dict[str, tuple[int, dict[str, SpeakerAggregate]]]:
    """Для каждого meeting_id возвращает (total_segment_count, {speaker_name: agg})."""
    if not meeting_ids:
        return {}
    stmt = (
        select(
            TranscriptSegmentDB.meeting_id,
            TranscriptSegmentDB.speaker,
            func.count(TranscriptSegmentDB.id).label("cnt"),
            func.coalesce(
                func.sum(TranscriptSegmentDB.end_time - TranscriptSegmentDB.start_time),
                0.0,
            ).label("dur"),
        )
        .where(TranscriptSegmentDB.meeting_id.in_(meeting_ids))
        .group_by(TranscriptSegmentDB.meeting_id, TranscriptSegmentDB.speaker)
    )
    result = await session.execute(stmt)
    buckets: dict[str, dict[str, SpeakerAggregate]] = defaultdict(dict)
    totals: dict[str, int] = defaultdict(int)
    for meeting_id, speaker, cnt, dur in result.all():
        name = speaker or "Unknown"
        buckets[meeting_id][name] = SpeakerAggregate(
            name=name,
            segment_count=int(cnt),
            speaking_time_sec=float(dur or 0.0),
        )
        totals[meeting_id] += int(cnt)
    return {mid: (totals[mid], buckets[mid]) for mid in buckets}


@meetings_router.get("", response_model=MeetingListResponse)
async def list_meetings(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, description="подстрока в title/filename"),
    tag: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(
        default="-created_at", description="созд: 'created_at' или '-created_at'"
    ),
    include_deleted: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> MeetingListResponse:
    base = select(Meeting)
    if status:
        base = base.where(Meeting.status == status)

    order_col = Meeting.created_at
    base = base.order_by(order_col.desc() if sort.startswith("-") else order_col.asc())

    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (await session.execute(base.offset(offset).limit(limit))).scalars().all()

    meeting_ids = [m.id for m in rows]
    agg = await _aggregate_speakers_bulk(session, meeting_ids)

    items: list[MeetingListItem] = []
    for meeting in rows:
        meta = _parse_admin_meta(meeting.admin_meta)
        if not include_deleted and meta.get("deleted_at"):
            continue
        if q:
            haystack = " ".join(
                [
                    meta.get("title") or "",
                    _derive_filename(meeting) or "",
                ]
            ).lower()
            if q.lower() not in haystack:
                continue
        if tag and tag not in (meta.get("tags") or []):
            continue

        total_cnt, speakers_by_name = agg.get(meeting.id, (0, {}))
        speakers = sorted(
            speakers_by_name.values(),
            key=lambda s: s.speaking_time_sec,
            reverse=True,
        )
        fields = _meeting_base_fields(meeting, meta, speakers, total_cnt)
        items.append(MeetingListItem(**fields))

    return MeetingListResponse(
        items=items,
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


@meetings_router.get("/{meeting_id}", response_model=MeetingDetail)
async def get_meeting(
    meeting_id: str,
    session: AsyncSession = Depends(get_session),
) -> MeetingDetail:
    meeting = await session.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    seg_rows = (
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

    segments = [
        TranscriptSegmentOut(
            index=i,
            speaker=row.speaker,
            start=float(row.start_time),
            end=float(row.end_time),
            text=row.text,
        )
        for i, row in enumerate(seg_rows)
    ]

    speakers_by_name: dict[str, SpeakerAggregate] = {}
    for row in seg_rows:
        name = row.speaker or "Unknown"
        agg = speakers_by_name.get(name)
        dur = float(row.end_time) - float(row.start_time)
        if agg is None:
            speakers_by_name[name] = SpeakerAggregate(
                name=name, segment_count=1, speaking_time_sec=dur
            )
        else:
            speakers_by_name[name] = SpeakerAggregate(
                name=name,
                segment_count=agg.segment_count + 1,
                speaking_time_sec=agg.speaking_time_sec + dur,
            )
    speakers = sorted(
        speakers_by_name.values(), key=lambda s: s.speaking_time_sec, reverse=True
    )

    meta = _parse_admin_meta(meeting.admin_meta)
    fields = _meeting_base_fields(meeting, meta, speakers, len(seg_rows))
    fields["segments"] = segments
    fields["meeting_url"] = meeting.meeting_url
    fields["recording_path"] = meeting.recording_path
    return MeetingDetail(**fields)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_or_404(session: AsyncSession, meeting_id: str) -> Meeting:
    meeting = await session.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@meetings_router.patch("/{meeting_id}", response_model=MeetingDetail)
async def patch_meeting(
    meeting_id: str,
    payload: MeetingUpdate,
    session: AsyncSession = Depends(get_session),
) -> MeetingDetail:
    """Обновить title/tags/summary в admin_meta."""
    meeting = await _get_or_404(session, meeting_id)
    meta = _parse_admin_meta(meeting.admin_meta)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
    meeting.admin_meta = json.dumps(meta, ensure_ascii=False)
    meeting.updated_at = _now_iso()
    await session.commit()
    await session.refresh(meeting)
    return await get_meeting(meeting_id, session)


@meetings_router.delete("/{meeting_id}", status_code=204)
async def soft_delete_meeting(
    meeting_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft-delete: admin_meta.deleted_at = now. Сам row и запись остаются."""
    meeting = await _get_or_404(session, meeting_id)
    meta = _parse_admin_meta(meeting.admin_meta)
    meta["deleted_at"] = _now_iso()
    meeting.admin_meta = json.dumps(meta, ensure_ascii=False)
    meeting.updated_at = _now_iso()
    await session.commit()


@meetings_router.post("/{meeting_id}/restore", response_model=MeetingDetail)
async def restore_meeting(
    meeting_id: str,
    session: AsyncSession = Depends(get_session),
) -> MeetingDetail:
    """Отмена soft-delete."""
    meeting = await _get_or_404(session, meeting_id)
    meta = _parse_admin_meta(meeting.admin_meta)
    meta.pop("deleted_at", None)
    meeting.admin_meta = json.dumps(meta, ensure_ascii=False)
    meeting.updated_at = _now_iso()
    await session.commit()
    return await get_meeting(meeting_id, session)


@meetings_router.patch(
    "/{meeting_id}/segments/{index}",
    response_model=TranscriptSegmentOut,
)
async def patch_segment(
    meeting_id: str,
    index: int,
    payload: SegmentUpdate,
    session: AsyncSession = Depends(get_session),
) -> TranscriptSegmentOut:
    """Обновить speaker/text у сегмента. index = позиция по start_time asc."""
    await _get_or_404(session, meeting_id)
    seg_rows = (
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
    if index < 0 or index >= len(seg_rows):
        raise HTTPException(status_code=404, detail="Segment not found")

    segment = seg_rows[index]
    updates = payload.model_dump(exclude_unset=True)
    if "speaker" in updates:
        segment.speaker = updates["speaker"]
    if "text" in updates:
        if not updates["text"] or not updates["text"].strip():
            raise HTTPException(status_code=422, detail="text cannot be empty")
        segment.text = updates["text"]
    await session.commit()
    await session.refresh(segment)
    return TranscriptSegmentOut(
        index=index,
        speaker=segment.speaker,
        start=float(segment.start_time),
        end=float(segment.end_time),
        text=segment.text,
    )


# Статусы, из которых допустим retry — встреча должна быть в финальном состоянии.
_RETRY_ALLOWED_STATUSES: frozenset[str] = frozenset({"done", "error"})


@meetings_router.post("/bulk", response_model=BulkResult)
async def bulk_meetings(
    payload: BulkAction,
    session: AsyncSession = Depends(get_session),
) -> BulkResult:
    """Батчевая операция: delete/restore/tag_add/tag_remove по списку id.

    retry в bulk НЕ делаем — слишком опасно массово бить workflow.
    """
    # Валидация payload для tag_* — тег должен быть непустой строкой.
    tag_value: str | None = None
    if payload.action in {"tag_add", "tag_remove"}:
        raw_tag = (payload.payload or {}).get("tag")
        if not isinstance(raw_tag, str) or not raw_tag.strip():
            raise HTTPException(
                status_code=422,
                detail="payload.tag (non-empty string) is required for tag_add/tag_remove",
            )
        tag_value = raw_tag.strip()

    # Загружаем все встречи разом, сохраняя исходный порядок id для not_found.
    rows = (
        (await session.execute(select(Meeting).where(Meeting.id.in_(payload.ids))))
        .scalars()
        .all()
    )
    by_id: dict[str, Meeting] = {m.id: m for m in rows}

    updated: list[str] = []
    not_found: list[str] = []
    now = _now_iso()

    for meeting_id in payload.ids:
        meeting = by_id.get(meeting_id)
        if meeting is None:
            not_found.append(meeting_id)
            continue

        meta = _parse_admin_meta(meeting.admin_meta)
        if payload.action == "delete":
            meta["deleted_at"] = now
        elif payload.action == "restore":
            meta.pop("deleted_at", None)
        elif payload.action == "tag_add":
            assert tag_value is not None
            tags = list(meta.get("tags") or [])
            if tag_value not in tags:
                tags.append(tag_value)
            meta["tags"] = tags
        elif payload.action == "tag_remove":
            assert tag_value is not None
            tags = [t for t in (meta.get("tags") or []) if t != tag_value]
            meta["tags"] = tags

        meeting.admin_meta = json.dumps(meta, ensure_ascii=False)
        meeting.updated_at = now
        updated.append(meeting_id)

    await session.commit()
    return BulkResult(updated=updated, not_found=not_found)


@meetings_router.post("/{meeting_id}/retry", response_model=MeetingDetail)
async def retry_meeting(
    meeting_id: str,
    session: AsyncSession = Depends(get_session),
) -> MeetingDetail:
    """Перезапустить обработку встречи.

    Требования:
      * встреча существует (иначе 404);
      * нет активной сессии (meeting_id in active_sessions → 409);
      * статус в {done, error} (иначе 409 — не дёргаем незавершённые).

    _bot_workflow и active_sessions импортируются лениво, чтобы избежать
    циклического импорта (main.py импортирует admin_router).
    """
    # Локальный импорт обязателен: main.py импортирует admin_router на верхнем
    # уровне, поэтому импорт на уровне модуля создал бы цикл.
    from ..main import _bot_workflow, active_sessions

    meeting = await _get_or_404(session, meeting_id)

    if meeting_id in active_sessions:
        raise HTTPException(
            status_code=409,
            detail="Meeting has an active session; retry is not allowed",
        )
    if meeting.status not in _RETRY_ALLOWED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Meeting status {meeting.status!r} is not final; "
                f"retry allowed only from {sorted(_RETRY_ALLOWED_STATUSES)}"
            ),
        )

    # Сбрасываем статус и ошибку, коммитим — чтобы фоновый workflow увидел pending.
    meeting.status = "pending"
    meeting.error_message = None
    meeting.updated_at = _now_iso()
    await session.commit()

    # Регистрируем сессию и запускаем workflow в фоне — повторяем паттерн /join.
    active_sessions[meeting_id] = {
        "task": None,
        "session": None,
        "capture": None,
        "stop_requested": False,
        "stop_before_recording": False,
        "recording_started": False,
    }
    task = asyncio.create_task(
        _bot_workflow(meeting_id, meeting.meeting_url, meeting.bot_name, None)
    )
    active_sessions[meeting_id]["task"] = task

    return await get_meeting(meeting_id, session)


@meetings_router.get("/{meeting_id}/audio")
async def get_meeting_audio(
    meeting_id: str,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Стримит recording_path с поддержкой Range (Starlette встроенно)."""
    meeting = await _get_or_404(session, meeting_id)
    if not meeting.recording_path or not os.path.isfile(meeting.recording_path):
        raise HTTPException(status_code=404, detail="Recording not available")
    filename = os.path.basename(meeting.recording_path)
    return FileResponse(
        meeting.recording_path,
        media_type="audio/wav",
        filename=filename,
    )
