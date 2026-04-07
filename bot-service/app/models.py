from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class JoinRequest(BaseModel):
    meeting_url: str
    bot_name: str = "Транскрибатор"


class MeetingStatus(BaseModel):
    meeting_id: UUID
    status: str
    meeting_url: str
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None
    created_at: datetime


class TranscriptSegment(BaseModel):
    speaker: Optional[str] = None
    start: float
    end: float
    text: str


class TranscriptResponse(BaseModel):
    meeting_id: UUID
    meeting_url: str
    duration_seconds: Optional[float] = None
    segments: list[TranscriptSegment]


class HealthResponse(BaseModel):
    status: str
    active_bots: int
