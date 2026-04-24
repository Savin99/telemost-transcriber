"""Endpoints /admin/api/metrics/* — агрегаты on-the-fly из admin_meta.

Никаких отдельных таблиц: перебираем meetings и суммируем поля из
admin_meta.metrics / admin_meta.ai_status. Для ≤ 5000 встреч — приемлемо.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Meeting, TranscriptSegmentDB, get_session

logger = logging.getLogger(__name__)

metrics_router = APIRouter(prefix="/metrics", tags=["admin:metrics"])


_ALLOWED_RANGES = {7, 30, 90}


def _parse_meta(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _created_date(meeting: Meeting) -> str | None:
    """Берём первые 10 символов created_at (ISO-дата). None, если нет."""
    raw = meeting.created_at
    if not raw:
        return None
    text = str(raw)
    if len(text) < 10:
        return None
    return text[:10]


def _since_iso(days: int) -> str:
    """ISO-строка нижней границы выборки (UTC, now - days)."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _unknown_pct(speakers: dict[str, int]) -> float:
    """% неопознанных спикеров среди сегментов встречи."""
    total = sum(speakers.values())
    if not total:
        return 0.0
    unknown = sum(
        count
        for name, count in speakers.items()
        if not name or str(name).lower().startswith(("speaker_", "unknown"))
    )
    return round(unknown * 100.0 / total, 2)


async def _speakers_count_by_meeting(
    session: AsyncSession, meeting_ids: list[str]
) -> dict[str, dict[str, int]]:
    """Для каждой встречи: {speaker_name -> segment_count}."""
    if not meeting_ids:
        return {}
    stmt = (
        select(
            TranscriptSegmentDB.meeting_id,
            TranscriptSegmentDB.speaker,
            func.count(TranscriptSegmentDB.id).label("cnt"),
        )
        .where(TranscriptSegmentDB.meeting_id.in_(meeting_ids))
        .group_by(TranscriptSegmentDB.meeting_id, TranscriptSegmentDB.speaker)
    )
    rows = (await session.execute(stmt)).all()
    result: dict[str, dict[str, int]] = defaultdict(dict)
    for meeting_id, speaker, cnt in rows:
        name = speaker or "Unknown"
        result[meeting_id][name] = int(cnt)
    return result


@metrics_router.get("/daily")
async def metrics_daily(
    range: int = Query(default=7, description="Сколько дней: 7/30/90"),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Агрегация по датам за последние N дней."""
    if range not in _ALLOWED_RANGES:
        # Молча приводим к ближайшему допустимому (UI шлёт только 7/30/90).
        range = 7

    since = _since_iso(range)
    stmt = select(Meeting).where(Meeting.created_at >= since)
    meetings = (await session.execute(stmt)).scalars().all()
    meeting_ids = [m.id for m in meetings]
    speakers_map = await _speakers_count_by_meeting(session, meeting_ids)

    # Агрегат: date -> {meetings, modal, claude, unknown_pct_sum, samples}
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "meetings": 0,
            "modal_cost_usd": 0.0,
            "claude_cost_usd": 0.0,
            "unknown_pct_sum": 0.0,
            "unknown_samples": 0,
        }
    )
    for meeting in meetings:
        date = _created_date(meeting)
        if not date:
            continue
        meta = _parse_meta(meeting.admin_meta)
        # Пропускаем soft-deleted — иначе метрики искажаются.
        if meta.get("deleted_at"):
            continue
        metrics = meta.get("metrics") or {}
        bucket = buckets[date]
        bucket["meetings"] += 1
        try:
            bucket["modal_cost_usd"] += float(metrics.get("modal_cost_usd") or 0.0)
        except (TypeError, ValueError):
            pass
        try:
            bucket["claude_cost_usd"] += float(metrics.get("claude_cost_usd") or 0.0)
        except (TypeError, ValueError):
            pass
        speakers = speakers_map.get(meeting.id, {})
        if speakers:
            bucket["unknown_pct_sum"] += _unknown_pct(speakers)
            bucket["unknown_samples"] += 1

    items: list[dict[str, Any]] = []
    for date in sorted(buckets.keys()):
        b = buckets[date]
        avg_unknown = (
            round(b["unknown_pct_sum"] / b["unknown_samples"], 2)
            if b["unknown_samples"]
            else 0.0
        )
        items.append(
            {
                "date": date,
                "meetings": int(b["meetings"]),
                "modal_cost_usd": round(b["modal_cost_usd"], 6),
                "claude_cost_usd": round(b["claude_cost_usd"], 6),
                "avg_unknown_pct": avg_unknown,
            }
        )
    return items


@metrics_router.get("/projection")
async def metrics_projection(
    session: AsyncSession = Depends(get_session),
) -> dict[str, float]:
    """Exp-avg за 7/30 дней → прогноз на месяц/год."""
    # Берём все, что попадает в 30-дневное окно (этого хватает для обоих срезов).
    since_30 = _since_iso(30)
    stmt = select(Meeting).where(Meeting.created_at >= since_30)
    meetings = (await session.execute(stmt)).scalars().all()

    since_7 = _since_iso(7)
    total_7 = 0.0
    total_30 = 0.0
    for meeting in meetings:
        meta = _parse_meta(meeting.admin_meta)
        if meta.get("deleted_at"):
            continue
        metrics = meta.get("metrics") or {}
        try:
            cost = float(metrics.get("modal_cost_usd") or 0.0) + float(
                metrics.get("claude_cost_usd") or 0.0
            )
        except (TypeError, ValueError):
            cost = 0.0
        total_30 += cost
        if str(meeting.created_at or "") >= since_7:
            total_7 += cost

    daily_7d = round(total_7 / 7.0, 6) if total_7 else 0.0
    daily_30d = round(total_30 / 30.0, 6) if total_30 else 0.0
    # Для прогноза на месяц/год используем более короткое окно (скорость «сейчас»).
    base_daily = daily_7d or daily_30d
    return {
        "daily_7d": daily_7d,
        "daily_30d": daily_30d,
        "monthly": round(base_daily * 30.0, 6),
        "yearly": round(base_daily * 365.0, 6),
    }


@metrics_router.get("/quality")
async def metrics_quality(
    session: AsyncSession = Depends(get_session),
) -> dict[str, float]:
    """Refiner-applied %, median confidence, % unknown speakers — за 30 дней."""
    since = _since_iso(30)
    stmt = select(Meeting).where(Meeting.created_at >= since)
    meetings = (await session.execute(stmt)).scalars().all()
    meeting_ids = [
        m.id for m in meetings if not _parse_meta(m.admin_meta).get("deleted_at")
    ]
    speakers_map = await _speakers_count_by_meeting(session, meeting_ids)

    total = 0
    refiner_applied = 0
    confidences: list[float] = []
    unknown_samples: list[float] = []

    for meeting in meetings:
        meta = _parse_meta(meeting.admin_meta)
        if meta.get("deleted_at"):
            continue
        total += 1
        ai_status = meta.get("ai_status") or {}
        # refiner считается "применённым", если хотя бы один из двух вернул
        # статус, начинающийся на "applied".
        spk = str(ai_status.get("speaker_refinement") or "")
        txt = str(ai_status.get("transcript_refinement") or "")
        if spk.startswith("applied") or txt.startswith("applied"):
            refiner_applied += 1
        # pyannote confidence хранится как metrics.pyannote_confidence (если есть).
        metrics = meta.get("metrics") or {}
        conf = metrics.get("pyannote_confidence")
        try:
            if conf is not None:
                confidences.append(float(conf))
        except (TypeError, ValueError):
            pass
        speakers = speakers_map.get(meeting.id, {})
        if speakers:
            unknown_samples.append(_unknown_pct(speakers))

    def _median(values: list[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return round(ordered[mid], 4)
        return round((ordered[mid - 1] + ordered[mid]) / 2.0, 4)

    refiner_pct = round(refiner_applied * 100.0 / total, 2) if total else 0.0
    unknown_pct = (
        round(sum(unknown_samples) / len(unknown_samples), 2)
        if unknown_samples
        else 0.0
    )
    return {
        "refiner_applied_pct": refiner_pct,
        "pyannote_median_confidence": _median(confidences),
        "unknown_speaker_pct": unknown_pct,
    }
