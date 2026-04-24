"""Read-only endpoints /admin/api/meetings[/{id}]."""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Meeting, TranscriptSegmentDB, get_session
from .schemas import (
    AiStatus,
    DriveInfo,
    MeetingDetail,
    MeetingListItem,
    MeetingListResponse,
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
