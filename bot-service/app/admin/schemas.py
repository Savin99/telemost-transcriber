"""Pydantic-схемы для /admin/api/*.

Shape совпадает с прототипом TeleScribe (src/mockData.jsx): часть полей
(title, tags, summary, ai_status, metrics, deleted_at) хранится в одной
JSON-колонке `meetings.admin_meta`, остальное — в основной схеме БД.
"""

from __future__ import annotations

from typing import Any, Literal

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


class BulkAction(BaseModel):
    """POST /admin/api/meetings/bulk — батчевая операция над списком id.

    Поддерживаемые действия:
      * delete — soft-delete всех встреч из списка
      * restore — убрать deleted_at у всех встреч
      * tag_add — добавить тег (payload={"tag": "..."}) к admin_meta.tags
      * tag_remove — убрать тег (payload={"tag": "..."}) из admin_meta.tags
    """

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=50)
    action: Literal["delete", "restore", "tag_add", "tag_remove"]
    payload: dict[str, Any] | None = None


class BulkResult(BaseModel):
    """Результат POST /admin/api/meetings/bulk."""

    updated: list[str]
    not_found: list[str]


# --- AdminSettings: read-only overlay поверх env (Фаза 5) -----------------
# PATCH /admin/api/settings не переписывает env.sh, только сохраняет JSON
# в ADMIN_SETTINGS_PATH. Все поля имеют дефолты; extra="allow" на верхнем
# уровне для forward-compat (новые секции не роняют старый клиент).


class AdminSettingsGeneral(BaseModel):
    model_config = ConfigDict(extra="allow")

    bot_name: str = "Транскрибатор"
    timezone: str = "Europe/Moscow"


class AdminSettingsASR(BaseModel):
    model_config = ConfigDict(extra="allow")

    modal_backend: str = "modal"
    num_speakers_default: int = 2


class AdminSettingsDiarization(BaseModel):
    model_config = ConfigDict(extra="allow")

    min_confidence: float = 0.40
    review_threshold: float = 0.70


class AdminSettingsLLM(BaseModel):
    model_config = ConfigDict(extra="allow")

    anthropic_model: str = "claude-sonnet-4-6"
    refiner_enabled: bool = True


class AdminSettingsVoiceBank(BaseModel):
    model_config = ConfigDict(extra="allow")

    fuzzy_dedup_threshold: float = 0.85
    high_confidence_threshold: float = 0.70


class AdminSettingsIntegrations(BaseModel):
    model_config = ConfigDict(extra="allow")

    gdrive_root_folder_id: str = ""


class AdminSettingsAdvanced(BaseModel):
    model_config = ConfigDict(extra="allow")

    debug_mode: bool = False
    log_level: str = "INFO"


class AdminSettings(BaseModel):
    """Полный overlay admin-настроек. Все секции имеют дефолты."""

    model_config = ConfigDict(extra="allow")

    general: AdminSettingsGeneral = Field(default_factory=AdminSettingsGeneral)
    asr: AdminSettingsASR = Field(default_factory=AdminSettingsASR)
    diarization: AdminSettingsDiarization = Field(
        default_factory=AdminSettingsDiarization
    )
    llm: AdminSettingsLLM = Field(default_factory=AdminSettingsLLM)
    voice_bank: AdminSettingsVoiceBank = Field(default_factory=AdminSettingsVoiceBank)
    integrations: AdminSettingsIntegrations = Field(
        default_factory=AdminSettingsIntegrations
    )
    advanced: AdminSettingsAdvanced = Field(default_factory=AdminSettingsAdvanced)


class AdminSettingsUpdate(BaseModel):
    """PATCH /admin/api/settings: все поля опциональны, extra=allow для forward-compat."""

    model_config = ConfigDict(extra="allow")

    general: dict[str, Any] | None = None
    asr: dict[str, Any] | None = None
    diarization: dict[str, Any] | None = None
    llm: dict[str, Any] | None = None
    voice_bank: dict[str, Any] | None = None
    integrations: dict[str, Any] | None = None
    advanced: dict[str, Any] | None = None
