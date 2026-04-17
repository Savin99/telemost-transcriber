"""Pydantic-схемы публичного контракта /v1/jobs.

Формы строго соответствуют спецификации в README (раздел «Transcribe Service
— API Specification»). Здесь же хранится общий словарь статусов, чтобы и
router, и workflow ссылались на один и тот же перечень.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

JobStatus = Literal[
    "queued",
    "connecting",
    "recording",
    "transcribing",
    "refining",
    "done",
    "error",
    "cancelled",
]

TerminalStatuses: frozenset[str] = frozenset({"done", "error", "cancelled"})
NonTerminalStatuses: frozenset[str] = frozenset(
    {"queued", "connecting", "recording", "transcribing", "refining"}
)

SessionType = Literal[
    "interview",
    "test_task_review",
    "offer_call",
    "personal",
    "meeting",
]


class JobSource(BaseModel):
    type: Literal["telemost"] = "telemost"
    url: str


class JobMetadata(BaseModel):
    session_id: int | str
    session_type: SessionType = "interview"
    language: str = "ru"


class SpeakerInfo(BaseModel):
    """Описание ожидаемого спикера. Ключ в словаре `speakers` — роль."""

    first_name: str
    last_name: str
    tg_id: int | None = None
    person_id: int | None = None
    hh_resume_id: str | None = None
    voice_bank_id: str | None = None
    enrolled: bool = False

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class JobOptions(BaseModel):
    diarize: bool = True
    llm_refine: bool = True
    initial_prompt: str | None = None


class CreateJobRequest(BaseModel):
    source: JobSource
    metadata: JobMetadata
    speakers: dict[str, SpeakerInfo] = Field(default_factory=dict)
    options: JobOptions = Field(default_factory=JobOptions)
    callback_url: str | None = None


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: str


class JobProgress(BaseModel):
    stage: str
    percent: float | None = None


class JobSegment(BaseModel):
    speaker: str
    speaker_role: str | None = None
    start: float
    end: float
    text: str


class EnrolledVoiceprintEntry(BaseModel):
    person_id: int | None = None
    voice_bank_id: str


class JobResult(BaseModel):
    duration_sec: float | None = None
    language: str = "ru"
    segments: list[JobSegment] = Field(default_factory=list)
    audio_url: str | None = None
    audio_retention_days: int = 30
    enrolled_voiceprints: list[EnrolledVoiceprintEntry] = Field(default_factory=list)


class JobError(BaseModel):
    code: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: JobProgress | None = None
    result: JobResult | None = None
    error: JobError | None = None


class JobConflictResponse(BaseModel):
    job_id: str
    status: JobStatus
    error: str


class RateLimitedResponse(BaseModel):
    error: str
    retry_after_sec: int


class AudioExpiredResponse(BaseModel):
    error: str
