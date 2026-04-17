from typing import Literal, Optional

from pydantic import BaseModel

MeetingLifecycleStatus = Literal[
    "pending",
    "connecting",
    "recording",
    "leaving",
    "transcribing",
    "refining",
    "done",
    "error",
    "cancelled",
]


class JoinRequest(BaseModel):
    meeting_url: str
    bot_name: str = "Транскрибатор"
    num_speakers: Optional[int] = None


class DriveFileInfo(BaseModel):
    file_id: str
    folder_id: str
    filename: str
    web_view_link: str


class MeetingStatus(BaseModel):
    meeting_id: str
    status: MeetingLifecycleStatus
    meeting_url: str
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    transcript_url: Optional[str] = None
    drive_file: Optional[DriveFileInfo] = None


class TranscriptSegment(BaseModel):
    speaker: str
    start: float
    end: float
    text: str


class TranscriptResponse(BaseModel):
    meeting_id: str
    meeting_url: str
    duration_seconds: Optional[float] = None
    transcript_url: str
    drive_file: DriveFileInfo
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


class SpeakerMergedLabel(BaseModel):
    speaker_label: str
    previous_name: str
    name: str
    confidence: float


class SpeakerLabelResponse(BaseModel):
    meeting_id: str
    meeting_key: str
    speaker_label: str
    previous_name: str
    name: str
    merged_labels: list[SpeakerMergedLabel] = []


class HealthResponse(BaseModel):
    status: str
    active_bots: int
