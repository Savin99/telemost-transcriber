"""Pydantic-схемы для /admin/api/*.

Shape совпадает с прототипом TeleScribe (src/mockData.jsx): часть полей
(title, tags, summary, ai_status, metrics, deleted_at) хранится в одной
JSON-колонке `meetings.admin_meta`, остальное — в основной схеме БД.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AdminMe(BaseModel):
    username: str


class SpeakerAggregate(BaseModel):
    """Агрегат по спикеру внутри встречи (из transcript_segments)."""

    name: str
    segment_count: int
    speaking_time_sec: float


class DriveInfo(BaseModel):
    md_file_id: str | None = None
    md_web_link: str | None = None
    filename: str | None = None
    folder_id: str | None = None


class AiStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    speaker_refinement: str | None = None
    transcript_refinement: str | None = None
    changes_applied: int | None = None


class MeetingListItem(BaseModel):
    """Элемент GET /admin/api/meetings — без segments, с агрегатом по спикерам."""

    id: str
    title: str | None = None
    filename: str | None = None
    duration_sec: float | None = None
    created_at: str | None = None
    updated_at: str | None = None
    status: str
    tags: list[str] = Field(default_factory=list)
    segment_count: int = 0
    speakers: list[SpeakerAggregate] = Field(default_factory=list)
    unknown_speaker_count: int = 0
    ai_status: AiStatus | None = None
    drive: DriveInfo | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] | None = None
    error_message: str | None = None
    deleted_at: str | None = None


class MeetingListResponse(BaseModel):
    items: list[MeetingListItem]
    total: int
    limit: int
    offset: int


class TranscriptSegmentOut(BaseModel):
    index: int
    speaker: str | None
    start: float
    end: float
    text: str


class MeetingDetail(MeetingListItem):
    """GET /admin/api/meetings/{id} — полный список сегментов inline."""

    segments: list[TranscriptSegmentOut] = Field(default_factory=list)
    meeting_url: str | None = None
    recording_path: str | None = None


class SegmentUpdate(BaseModel):
    """PATCH /admin/api/meetings/{id}/segments/{index}."""

    speaker: str | None = None
    text: str | None = None


class MeetingUpdate(BaseModel):
    """PATCH /admin/api/meetings/{id}. Пишет только в admin_meta."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    tags: list[str] | None = None
    summary: dict[str, Any] | None = None
