from typing import Optional

from pydantic import BaseModel


class JoinRequest(BaseModel):
    meeting_url: str
    bot_name: str = "Транскрибатор"
    num_speakers: Optional[int] = None


class MeetingStatus(BaseModel):
    meeting_id: str
    status: str
    meeting_url: str
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None


class TranscriptSegment(BaseModel):
    speaker: Optional[str] = None
    start: float
    end: float
    text: str


class TranscriptResponse(BaseModel):
    meeting_id: str
    meeting_url: str
    duration_seconds: Optional[float] = None
    segments: list[TranscriptSegment]


class SpeakerReviewRequest(BaseModel):
    num_speakers: Optional[int] = None
    min_speakers: Optional[int] = None
    max_speakers: Optional[int] = None
    samples_per_speaker: int = 3
    sample_max_seconds: float = 12.0


class SpeakerSegmentPreview(BaseModel):
    start: float
    end: float


class SpeakerReviewItem(BaseModel):
    speaker_label: str
    current_name: str
    confidence: float
    is_known: bool
    segments: list[SpeakerSegmentPreview]
    sample_count: int


class SpeakerReviewResponse(BaseModel):
    meeting_id: str
    meeting_key: str
    items: list[SpeakerReviewItem]


class SpeakerLabelRequest(BaseModel):
    name: str
    alpha: float = 0.05


class SpeakerLabelResponse(BaseModel):
    meeting_id: str
    meeting_key: str
    speaker_label: str
    previous_name: str
    name: str


class HealthResponse(BaseModel):
    status: str
    active_bots: int
