import asyncio
import importlib
import json
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .api_v1_models import (
    AudioExpiredResponse,
    CreateJobRequest,
    EnrolledVoiceprintEntry,
    JobConflictResponse,
    JobCreatedResponse,
    JobError,
    JobProgress,
    JobResult,
    JobSegment,
    JobStatusResponse,
    NonTerminalStatuses,
    RateLimitedResponse,
    TerminalStatuses,
)
from .audio_capture import AudioCapture
from .database import (
    Meeting,
    TranscriptSegmentDB,
    async_session,
    get_session,
    init_db,
    update_meeting_status,
)
from .models import (
    DriveFileInfo,
    HealthResponse,
    JoinRequest,
    MeetingStatus,
    SpeakerMergedLabel,
    SpeakerLabelRequest,
    SpeakerLabelResponse,
    SpeakerReviewItem,
    SpeakerReviewRequest,
    SpeakerReviewResponse,
    SpeakerSegmentPreview,
    TranscriptResponse,
    TranscriptSegment,
)
from .telemost import TelemostSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def validate_required_env() -> None:
    """Падаем в lifespan, если включённые фичи требуют недостающих env."""
    missing: list[str] = []

    if (
        _env_bool("MEETING_METADATA_LLM_ENABLED", False)
        and not os.getenv("ANTHROPIC_API_KEY", "").strip()
    ):
        missing.append(
            "ANTHROPIC_API_KEY is required when MEETING_METADATA_LLM_ENABLED is true"
        )

    if missing:
        for error in missing:
            logger.error("Startup env check failed: %s", error)
        raise RuntimeError(
            "Missing required environment variables: " + "; ".join(missing)
        )


TRANSCRIBER_URL = os.getenv("TRANSCRIBER_URL", "http://transcriber:8001")
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", "/app/recordings")
API_KEY_HEADER = "X-API-Key"
LEGACY_STATUS_ALIASES = {"joining": "pending"}
DEFAULT_UNKNOWN_SPEAKER = "Unknown Speaker 1"

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
AUDIO_RETENTION_DAYS = int(os.getenv("AUDIO_RETENTION_DAYS", "30"))
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "4"))
CLEANUP_INTERVAL_SEC = int(os.getenv("AUDIO_CLEANUP_INTERVAL_SEC", "3600"))

# Internal Meeting.status -> external /v1 status
V1_STATUS_MAP: dict[str, str] = {
    "pending": "queued",
    "connecting": "connecting",
    "recording": "recording",
    "leaving": "transcribing",
    "transcribing": "transcribing",
    "refining": "refining",
    "done": "done",
    "error": "error",
    "cancelled": "cancelled",
}

# Активные сессии:
# meeting_id -> {
#   "task": asyncio.Task | None,
#   "session": TelemostSession | None,
#   "capture": AudioCapture | None,
#   "stop_requested": bool,
#   "stop_before_recording": bool,
#   "recording_started": bool,
# }
active_sessions: dict[str, dict] = {}


def _load_service_api_key() -> str:
    api_key = os.getenv("TELEMOST_SERVICE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TELEMOST_SERVICE_API_KEY must be set")
    return api_key


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _is_public_v1_path(path: str) -> bool:
    return path.startswith("/v1/") or path.startswith("/audio/")


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias=API_KEY_HEADER),
    authorization: str | None = Header(default=None),
):
    """Единая точка авторизации.

    - Публичный /v1 (и /audio) принимают ТОЛЬКО Authorization: Bearer и
      возвращают 401 на неудачу (по спецификации).
    - Легаси-роуты (/join, /status, …) принимают X-API-Key (как исторически)
      или Authorization: Bearer, возвращают 403 на неудачу.
    """
    expected_api_key = (
        getattr(app.state, "service_api_key", None) or _load_service_api_key()
    )
    bearer_token = _extract_bearer(authorization)
    bearer_matches = bool(bearer_token) and secrets.compare_digest(
        bearer_token, expected_api_key
    )

    if _is_public_v1_path(request.url.path):
        if not bearer_matches:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return

    x_api_key_matches = bool(x_api_key) and secrets.compare_digest(
        x_api_key, expected_api_key
    )
    if not (x_api_key_matches or bearer_matches):
        raise HTTPException(status_code=403, detail="Forbidden")


def _normalize_status(status: str | None) -> str:
    if not status:
        return "pending"
    return LEGACY_STATUS_ALIASES.get(status, status)


def _format_timestamp(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc)
    else:
        raw = str(value).strip()
        if not raw:
            return None
        normalized = raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _build_meeting_status(
    meeting: Meeting,
    *,
    duration_seconds: float | None = None,
) -> MeetingStatus:
    normalized_status = _normalize_status(meeting.status)
    drive_file = _build_drive_file(meeting) if normalized_status == "done" else None
    return MeetingStatus(
        meeting_id=meeting.id,
        status=normalized_status,
        meeting_url=meeting.meeting_url,
        duration_seconds=duration_seconds
        if duration_seconds is not None
        else meeting.duration_seconds,
        error_message=meeting.error_message if normalized_status == "error" else None,
        created_at=_format_timestamp(meeting.created_at),
        transcript_url=meeting.transcript_url if normalized_status == "done" else None,
        drive_file=drive_file,
    )


def _normalize_transcript_segments(segments: list[dict]) -> list[dict]:
    normalized_segments: list[dict] = []
    missing_speaker_name = DEFAULT_UNKNOWN_SPEAKER
    for segment in segments:
        speaker = str(segment.get("speaker") or "").strip() or missing_speaker_name
        normalized_segments.append(
            {
                "speaker": speaker,
                "start": float(segment["start"]),
                "end": float(segment["end"]),
                "text": str(segment["text"]),
            }
        )
    return normalized_segments


def _build_drive_file(meeting: Meeting) -> DriveFileInfo | None:
    if not (
        meeting.drive_file_id
        and meeting.drive_folder_id
        and meeting.drive_filename
        and meeting.drive_web_view_link
    ):
        return None
    return DriveFileInfo(
        file_id=str(meeting.drive_file_id),
        folder_id=str(meeting.drive_folder_id),
        filename=str(meeting.drive_filename),
        web_view_link=str(meeting.drive_web_view_link),
    )


def _build_transcript_payload(
    *,
    meeting_id: str,
    meeting_url: str,
    duration_seconds: float | None,
    segments: list[dict],
    ai_status: dict | None = None,
) -> dict:
    effective_duration = duration_seconds
    if effective_duration is None and segments:
        effective_duration = float(segments[-1]["end"])
    payload = {
        "meeting_id": meeting_id,
        "meeting_url": meeting_url,
        "duration_seconds": effective_duration,
        "segments": segments,
    }
    if ai_status:
        payload["ai_status"] = ai_status
    return payload


def _load_tg_bot_module(module_name: str):
    tg_bot_dir = Path(__file__).resolve().parents[2] / "tg-bot"
    tg_bot_dir_str = str(tg_bot_dir)
    if tg_bot_dir_str not in sys.path:
        sys.path.insert(0, tg_bot_dir_str)
    return importlib.import_module(module_name)


def _upload_transcript_to_drive_sync(
    transcript: dict,
    *,
    source_filename: str | None = None,
) -> dict[str, str]:
    gdrive_module = _load_tg_bot_module("gdrive")
    result = gdrive_module.upload_transcript_md(
        transcript=transcript,
        source_filename=source_filename,
    )
    required_keys = ("file_id", "folder_id", "filename", "web_view_link")
    if not isinstance(result, dict) or any(
        not result.get(key) for key in required_keys
    ):
        raise RuntimeError("Google Drive upload failed or returned incomplete metadata")
    return {key: str(result[key]) for key in required_keys}


async def _upload_transcript_to_drive(
    transcript: dict,
    *,
    source_filename: str | None = None,
) -> dict[str, str]:
    return await asyncio.to_thread(
        _upload_transcript_to_drive_sync,
        transcript,
        source_filename=source_filename,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service_api_key = _load_service_api_key()
    validate_required_env()
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    await init_db()
    cleanup_task = asyncio.create_task(_audio_retention_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        for meeting_id in list(active_sessions.keys()):
            await _stop_session(meeting_id)


app = FastAPI(
    title="Telemost Bot",
    lifespan=lifespan,
    dependencies=[Depends(require_api_key)],
)


async def _bot_workflow(
    meeting_id: str,
    meeting_url: str,
    bot_name: str,
    num_speakers: int | None = None,
):
    """Основной workflow бота: вход → запись → транскрипция."""
    telemost: TelemostSession | None = None
    capture: AudioCapture | None = None
    recording_path = os.path.join(RECORDINGS_DIR, f"{meeting_id}.wav")

    async with async_session() as session:
        try:
            # 1. Вход в Телемост
            telemost = TelemostSession(meeting_url, bot_name)
            capture = AudioCapture(recording_path, session_id=meeting_id)
            info = active_sessions.setdefault(meeting_id, {})
            info["session"] = telemost
            info["capture"] = capture

            await telemost.join()
            info = active_sessions.get(meeting_id)
            if info and info.get("stop_requested"):
                info["stop_before_recording"] = True
                raise asyncio.CancelledError

            # 2. Запуск записи (PulseAudio per-session sink + FFmpeg)
            await update_meeting_status(
                session, meeting_id, "recording", error_message=None
            )
            await capture.start()
            info = active_sessions.get(meeting_id)
            if info:
                info["recording_started"] = True
                if info.get("stop_requested"):
                    telemost._meeting_ended.set()

            # 3. Ожидание завершения встречи
            await telemost.wait_for_end()

            # 4. Остановка записи
            duration = await capture.stop()
            info = active_sessions.get(meeting_id)
            if info:
                info["recording_started"] = False
            await update_meeting_status(
                session,
                meeting_id,
                "leaving",
                recording_path=recording_path,
                duration_seconds=duration,
                error_message=None,
            )

            # 5. Закрытие браузера
            await telemost.leave()

            # 6. Отправка на транскрипцию
            await update_meeting_status(
                session, meeting_id, "transcribing", error_message=None
            )
            transcribe_result = await _transcribe(
                recording_path, num_speakers=num_speakers
            )
            segments = _normalize_transcript_segments(transcribe_result["segments"])
            ai_status = transcribe_result.get("ai_status")
            transcript_payload = _build_transcript_payload(
                meeting_id=meeting_id,
                meeting_url=meeting_url,
                duration_seconds=duration,
                segments=segments,
                ai_status=ai_status,
            )
            drive_file = await _upload_transcript_to_drive(
                transcript_payload,
                source_filename=os.path.basename(recording_path),
            )

            # 7. Сохранение результатов
            for seg in segments:
                db_segment = TranscriptSegmentDB(
                    meeting_id=meeting_id,
                    speaker=seg["speaker"],
                    start_time=seg["start"],
                    end_time=seg["end"],
                    text=seg["text"],
                )
                session.add(db_segment)
            meeting = await _get_meeting_or_404(session, meeting_id)
            meeting.status = "done"
            meeting.duration_seconds = transcript_payload["duration_seconds"]
            meeting.error_message = None
            meeting.transcript_url = drive_file["web_view_link"]
            meeting.drive_file_id = drive_file["file_id"]
            meeting.drive_folder_id = drive_file["folder_id"]
            meeting.drive_filename = drive_file["filename"]
            meeting.drive_web_view_link = drive_file["web_view_link"]
            await session.commit()
            logger.info("Meeting %s processed successfully", meeting_id)

        except asyncio.CancelledError:
            logger.info(
                "Meeting %s was stopped before recording pipeline finished", meeting_id
            )
            await session.rollback()

            info = active_sessions.get(meeting_id) or {}
            had_recording = bool(info.get("recording_started"))
            if not had_recording and capture and capture.duration_seconds is not None:
                had_recording = True
            duration = None
            if capture:
                with suppress(Exception):
                    duration = await capture.stop()
            if telemost:
                with suppress(Exception):
                    await telemost.leave()

            async with async_session() as cancel_session:
                await update_meeting_status(
                    cancel_session,
                    meeting_id,
                    "error",
                    recording_path=recording_path,
                    duration_seconds=duration,
                    transcript_url=None,
                    drive_file_id=None,
                    drive_folder_id=None,
                    drive_filename=None,
                    drive_web_view_link=None,
                    error_message=(
                        "Meeting was stopped before recording started"
                        if info.get("stop_before_recording") or not had_recording
                        else "Meeting processing was interrupted before transcript was ready"
                    ),
                )
        except Exception as e:
            logger.exception("Error processing meeting %s", meeting_id)
            await session.rollback()
            if capture:
                with suppress(Exception):
                    await capture.stop()
            if telemost:
                with suppress(Exception):
                    await telemost.leave()
            async with async_session() as err_session:
                await update_meeting_status(
                    err_session,
                    meeting_id,
                    "error",
                    transcript_url=None,
                    drive_file_id=None,
                    drive_folder_id=None,
                    drive_filename=None,
                    drive_web_view_link=None,
                    error_message=str(e),
                )
        finally:
            info = active_sessions.get(meeting_id)
            if info:
                info["recording_started"] = False
            active_sessions.pop(meeting_id, None)


async def _transcribe(
    recording_path: str,
    num_speakers: int | None = None,
    *,
    speakers_hint: list[dict] | None = None,
    auto_enroll_unknown: bool = False,
    initial_prompt: str | None = None,
) -> dict:
    """Отправить запись на транскрипцию. Возвращает полный JSON-ответ."""
    payload: dict = {"audio_path": recording_path}
    if num_speakers is not None:
        payload["num_speakers"] = num_speakers
    if speakers_hint:
        payload["speakers_hint"] = speakers_hint
    if auto_enroll_unknown:
        payload["auto_enroll_unknown"] = True
    if initial_prompt:
        payload["initial_prompt"] = initial_prompt
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            f"{TRANSCRIBER_URL}/transcribe",
            json=payload,
        )
        response.raise_for_status()
        return response.json()


async def _get_meeting_or_404(session: AsyncSession, meeting_id: str) -> Meeting:
    meeting = await session.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


async def _stop_session(meeting_id: str):
    """Остановить активную сессию."""
    info = active_sessions.get(meeting_id)
    if not info:
        return

    info["stop_requested"] = True
    info["stop_before_recording"] = not bool(info.get("recording_started"))
    capture: AudioCapture | None = info.get("capture")
    telemost: TelemostSession | None = info.get("session")
    task: asyncio.Task | None = info.get("task")

    duration = None
    if capture:
        duration = await capture.stop()
        info["recording_started"] = False
    if telemost:
        await telemost.leave()
    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    return duration


@app.post("/join", response_model=MeetingStatus, response_model_exclude_none=True)
async def join_meeting(
    request: JoinRequest,
    session: AsyncSession = Depends(get_session),
):
    """Подключить бота к встрече."""
    meeting = Meeting(
        meeting_url=request.meeting_url,
        bot_name=request.bot_name,
        status="pending",
    )
    session.add(meeting)
    await session.commit()
    await session.refresh(meeting)

    meeting_id = meeting.id

    active_sessions[meeting_id] = {
        "task": None,
        "session": None,
        "capture": None,
        "stop_requested": False,
        "stop_before_recording": False,
        "recording_started": False,
    }

    # Запуск workflow в фоне
    task = asyncio.create_task(
        _bot_workflow(
            meeting_id, request.meeting_url, request.bot_name, request.num_speakers
        )
    )
    active_sessions[meeting_id]["task"] = task

    return _build_meeting_status(meeting)


@app.post("/leave/{meeting_id}", response_model=MeetingStatus)
async def leave_meeting(
    meeting_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Отключить бота от встречи и запустить транскрипцию."""
    meeting = await _get_meeting_or_404(session, meeting_id)
    current_status = _normalize_status(meeting.status)

    if current_status in {"leaving", "transcribing", "done", "error"}:
        return _build_meeting_status(meeting)

    # Отмечаем stop всегда; дальнейшая логика зависит от стадии запуска.
    info = active_sessions.get(meeting_id)
    if not info:
        return _build_meeting_status(meeting)

    info["stop_requested"] = True
    task: asyncio.Task | None = info.get("task")
    recording_started = bool(info.get("recording_started"))
    telemost: TelemostSession | None = info.get("session")

    # Если запись уже идет, workflow завершится через событие окончания.
    if recording_started and telemost:
        telemost._meeting_ended.set()
    # Если запись ещё не стартовала — отменяем задачу сразу.
    elif task and not task.done():
        info["stop_before_recording"] = True
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    await session.refresh(meeting)
    return _build_meeting_status(meeting)


@app.get("/status/{meeting_id}", response_model=MeetingStatus)
async def get_status(
    meeting_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Получить статус встречи."""
    meeting = await _get_meeting_or_404(session, meeting_id)

    # Если бот активен, добавить текущую длительность записи
    duration = meeting.duration_seconds
    if meeting_id in active_sessions:
        capture = active_sessions[meeting_id].get("capture")
        if capture and capture.duration_seconds:
            duration = capture.duration_seconds

    return _build_meeting_status(meeting, duration_seconds=duration)


@app.get("/transcripts/{meeting_id}", response_model=TranscriptResponse)
async def get_transcript(
    meeting_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Получить транскрипт встречи."""
    meeting = await _get_meeting_or_404(session, meeting_id)
    if _normalize_status(meeting.status) != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Transcript is not ready; current status is {_normalize_status(meeting.status)}",
        )

    result = await session.execute(
        select(TranscriptSegmentDB)
        .where(TranscriptSegmentDB.meeting_id == meeting_id)
        .order_by(TranscriptSegmentDB.start_time)
    )
    segments = result.scalars().all()
    drive_file = _build_drive_file(meeting)
    if not meeting.transcript_url or not drive_file:
        raise HTTPException(
            status_code=500, detail="Transcript Drive upload metadata is missing"
        )

    return TranscriptResponse(
        meeting_id=meeting.id,
        meeting_url=meeting.meeting_url,
        duration_seconds=meeting.duration_seconds,
        transcript_url=meeting.transcript_url,
        drive_file=drive_file,
        segments=[
            TranscriptSegment(
                speaker=str(seg.speaker or DEFAULT_UNKNOWN_SPEAKER),
                start=seg.start_time,
                end=seg.end_time,
                text=seg.text,
            )
            for seg in segments
        ],
    )


@app.get("/meetings", response_model=list[MeetingStatus])
async def list_meetings(
    status: str | None = None,
    limit: int = 10,
    session: AsyncSession = Depends(get_session),
):
    limit = max(1, min(limit, 50))
    query = select(Meeting).order_by(Meeting.created_at.desc()).limit(limit)
    if status:
        if status == "pending":
            query = query.where(Meeting.status.in_(["pending", "joining"]))
        else:
            query = query.where(Meeting.status == status)

    result = await session.execute(query)
    meetings = result.scalars().all()

    return [_build_meeting_status(meeting) for meeting in meetings]


@app.post(
    "/meetings/{meeting_id}/speaker-review",
    response_model=SpeakerReviewResponse,
)
async def review_unknown_speakers(
    meeting_id: str,
    request: SpeakerReviewRequest,
    session: AsyncSession = Depends(get_session),
):
    meeting = await _get_meeting_or_404(session, meeting_id)
    if not meeting.recording_path:
        raise HTTPException(status_code=400, detail="Recording path is not available")

    payload = {
        "audio_path": meeting.recording_path,
        "num_speakers": request.num_speakers,
        "min_speakers": request.min_speakers,
        "max_speakers": request.max_speakers,
        "samples_per_speaker": request.samples_per_speaker,
        "sample_max_seconds": request.sample_max_seconds,
    }
    async with httpx.AsyncClient(timeout=600) as client:
        try:
            response = await client.post(
                f"{TRANSCRIBER_URL}/speaker-review",
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code, detail=e.response.text
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    data = response.json()
    return SpeakerReviewResponse(
        meeting_id=meeting_id,
        meeting_key=data["meeting_key"],
        items=[
            SpeakerReviewItem(
                speaker_label=item["speaker_label"],
                current_name=item["current_name"],
                confidence=item["confidence"],
                is_known=item["is_known"],
                segments=[
                    SpeakerSegmentPreview(start=segment["start"], end=segment["end"])
                    for segment in item.get("segments", [])
                ],
                sample_count=item["sample_count"],
            )
            for item in data.get("items", [])
        ],
    )


@app.get(
    "/meetings/{meeting_id}/speaker-review/{meeting_key}/{speaker_label}/samples/{sample_index}"
)
async def download_speaker_review_sample(
    meeting_id: str,
    meeting_key: str,
    speaker_label: str,
    sample_index: int,
    session: AsyncSession = Depends(get_session),
):
    await _get_meeting_or_404(session, meeting_id)
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            response = await client.get(
                f"{TRANSCRIBER_URL}/speaker-review/{meeting_key}/{speaker_label}/samples/{sample_index}"
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code, detail=e.response.text
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "audio/wav"),
        headers={
            "Content-Disposition": response.headers.get(
                "content-disposition",
                f'attachment; filename="{speaker_label}_{sample_index}.wav"',
            )
        },
    )


@app.post(
    "/meetings/{meeting_id}/speaker-review/{meeting_key}/{speaker_label}/label",
    response_model=SpeakerLabelResponse,
)
async def label_speaker_review(
    meeting_id: str,
    meeting_key: str,
    speaker_label: str,
    request: SpeakerLabelRequest,
    session: AsyncSession = Depends(get_session),
):
    await _get_meeting_or_404(session, meeting_id)
    async with httpx.AsyncClient(timeout=600) as client:
        try:
            response = await client.post(
                f"{TRANSCRIBER_URL}/speaker-review/{meeting_key}/{speaker_label}/label",
                json={"name": request.name, "alpha": request.alpha},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code, detail=e.response.text
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    data = response.json()
    rename_sources = [data["previous_name"]]
    rename_sources.extend(
        item["previous_name"] for item in data.get("merged_labels", [])
    )
    for previous_name in {
        name for name in rename_sources if name and name != data["name"]
    }:
        await session.execute(
            update(TranscriptSegmentDB)
            .where(TranscriptSegmentDB.meeting_id == meeting_id)
            .where(TranscriptSegmentDB.speaker == previous_name)
            .values(speaker=data["name"])
        )
    await session.commit()

    return SpeakerLabelResponse(
        meeting_id=meeting_id,
        meeting_key=data["meeting_key"],
        speaker_label=data["speaker_label"],
        previous_name=data["previous_name"],
        name=data["name"],
        merged_labels=[
            SpeakerMergedLabel(
                speaker_label=item["speaker_label"],
                previous_name=item["previous_name"],
                name=item["name"],
                confidence=float(item["confidence"]),
            )
            for item in data.get("merged_labels", [])
        ],
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check."""
    return HealthResponse(status="ok", active_bots=len(active_sessions))


# ============================================================
# Public /v1/jobs API — см. api_v1_models.py и docs/README
# ============================================================


def _v1_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _v1_retention_expires_at(created_iso: str | None = None) -> str:
    base = datetime.now(timezone.utc)
    if created_iso:
        try:
            parsed = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            base = parsed.astimezone(timezone.utc)
    return (base + timedelta(days=AUDIO_RETENTION_DAYS)).isoformat()


def _v1_map_status(internal_status: str | None) -> str:
    normalized = _normalize_status(internal_status)
    return V1_STATUS_MAP.get(normalized, normalized)


def _v1_speakers_hint_from_payload(
    speakers_payload: dict[str, dict],
) -> list[dict]:
    """Прокидывается в transcriber-service: имя + voice_bank_id + enrolled."""
    hints: list[dict] = []
    for role, raw in speakers_payload.items():
        if not raw:
            continue
        first_name = (raw.get("first_name") or "").strip()
        last_name = (raw.get("last_name") or "").strip()
        display_name = " ".join(part for part in (first_name, last_name) if part)
        if not display_name:
            continue
        hints.append(
            {
                "display_name": display_name,
                "enrolled": bool(raw.get("enrolled")),
                "voice_bank_id": raw.get("voice_bank_id"),
                "role": role,
                "person_id": raw.get("person_id"),
            }
        )
    return hints


def _v1_speaker_role_for(
    speaker: str,
    speakers_payload: dict[str, dict],
    enrolled: list[dict],
) -> str | None:
    if not speaker:
        return None
    for entry in enrolled:
        if entry.get("display_name") == speaker and entry.get("role"):
            return entry["role"]
    for role, raw in (speakers_payload or {}).items():
        if not raw:
            continue
        first = (raw.get("first_name") or "").strip()
        last = (raw.get("last_name") or "").strip()
        display_name = " ".join(part for part in (first, last) if part)
        if display_name and display_name == speaker:
            return role
    return None


def _v1_parse_json_field(raw: str | None) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _v1_audio_is_available(meeting: Meeting) -> bool:
    if not meeting.recording_path:
        return False
    try:
        if not os.path.exists(meeting.recording_path):
            return False
    except OSError:
        return False
    expires_at = meeting.audio_retention_expires_at
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expiry:
            return False
    return True


def _v1_build_result(
    meeting: Meeting, segments: list[TranscriptSegmentDB]
) -> JobResult:
    speakers_payload = _v1_parse_json_field(meeting.speakers_payload) or {}
    enrolled = _v1_parse_json_field(meeting.enrolled_voiceprints) or []
    result_segments: list[JobSegment] = []
    for segment in segments:
        speaker = str(segment.speaker or DEFAULT_UNKNOWN_SPEAKER)
        result_segments.append(
            JobSegment(
                speaker=speaker,
                speaker_role=_v1_speaker_role_for(speaker, speakers_payload, enrolled),
                start=float(segment.start_time),
                end=float(segment.end_time),
                text=str(segment.text),
            )
        )
    audio_url: str | None = None
    if _v1_audio_is_available(meeting):
        audio_url = f"{PUBLIC_BASE_URL}/audio/{meeting.id}.wav"
    return JobResult(
        duration_sec=meeting.duration_seconds,
        language=meeting.language or "ru",
        segments=result_segments,
        audio_url=audio_url,
        audio_retention_days=AUDIO_RETENTION_DAYS,
        enrolled_voiceprints=[
            EnrolledVoiceprintEntry(
                person_id=entry.get("person_id"),
                voice_bank_id=str(entry["voice_bank_id"]),
            )
            for entry in enrolled
            if isinstance(entry, dict) and entry.get("voice_bank_id")
        ],
    )


def _v1_build_progress(meeting: Meeting) -> JobProgress | None:
    external_status = _v1_map_status(meeting.status)
    if external_status in {"done", "error", "cancelled", "queued"}:
        return None
    stage = meeting.progress_stage or external_status
    percent = (
        float(meeting.progress_percent)
        if meeting.progress_percent is not None
        else None
    )
    return JobProgress(stage=stage, percent=percent)


async def _v1_build_status_payload(
    meeting: Meeting, session: AsyncSession
) -> JobStatusResponse:
    external_status = _v1_map_status(meeting.status)
    payload = JobStatusResponse(
        job_id=meeting.id,
        status=external_status,
        progress=_v1_build_progress(meeting),
    )
    if external_status == "done":
        result = await session.execute(
            select(TranscriptSegmentDB)
            .where(TranscriptSegmentDB.meeting_id == meeting.id)
            .order_by(TranscriptSegmentDB.start_time)
        )
        payload.result = _v1_build_result(meeting, list(result.scalars().all()))
    elif external_status == "error":
        payload.error = JobError(
            code=meeting.error_code or "internal_error",
            message=meeting.error_message or "Unknown error",
        )
    return payload


async def _bot_workflow_v1(
    meeting_id: str,
    meeting_url: str,
    bot_name: str,
    *,
    speakers_hint: list[dict],
    auto_enroll_unknown: bool,
    initial_prompt: str | None,
    llm_refine: bool,
):
    """Публичный /v1 workflow без Google-Drive и со спец-статусами."""
    telemost: TelemostSession | None = None
    capture: AudioCapture | None = None
    recording_path = os.path.join(RECORDINGS_DIR, f"{meeting_id}.wav")

    async with async_session() as session:
        try:
            telemost = TelemostSession(meeting_url, bot_name)
            capture = AudioCapture(recording_path, session_id=meeting_id)
            info = active_sessions.setdefault(meeting_id, {})
            info["session"] = telemost
            info["capture"] = capture

            await update_meeting_status(
                session,
                meeting_id,
                "connecting",
                error_message=None,
                progress_stage="connecting",
                progress_percent=None,
            )
            await telemost.join()
            info = active_sessions.get(meeting_id)
            if info and info.get("stop_requested"):
                info["stop_before_recording"] = True
                raise asyncio.CancelledError

            await update_meeting_status(
                session,
                meeting_id,
                "recording",
                error_message=None,
                progress_stage="recording",
                progress_percent=None,
            )
            await capture.start()
            info = active_sessions.get(meeting_id)
            if info:
                info["recording_started"] = True
                if info.get("stop_requested"):
                    telemost._meeting_ended.set()

            await telemost.wait_for_end()

            duration = await capture.stop()
            info = active_sessions.get(meeting_id)
            if info:
                info["recording_started"] = False
            await telemost.leave()

            await update_meeting_status(
                session,
                meeting_id,
                "transcribing",
                recording_path=recording_path,
                duration_seconds=duration,
                error_message=None,
                progress_stage="transcribing",
                progress_percent=None,
            )

            transcribe_result = await _transcribe(
                recording_path,
                speakers_hint=speakers_hint or None,
                auto_enroll_unknown=auto_enroll_unknown,
                initial_prompt=initial_prompt,
            )
            segments = _normalize_transcript_segments(
                transcribe_result.get("segments", [])
            )
            ai_status = transcribe_result.get("ai_status") or {}
            enrolled_voiceprints_raw = transcribe_result.get("enrolled_voiceprints", [])

            if llm_refine and (
                (ai_status.get("speaker_refinement") or "disabled") != "disabled"
                or (ai_status.get("transcript_refinement") or "disabled") != "disabled"
            ):
                await update_meeting_status(
                    session,
                    meeting_id,
                    "refining",
                    error_message=None,
                    progress_stage="refining",
                    progress_percent=None,
                )

            for seg in segments:
                db_segment = TranscriptSegmentDB(
                    meeting_id=meeting_id,
                    speaker=seg["speaker"],
                    start_time=seg["start"],
                    end_time=seg["end"],
                    text=seg["text"],
                )
                session.add(db_segment)

            effective_duration = (
                duration
                if duration is not None
                else (segments[-1]["end"] if segments else None)
            )
            transcript_payload = _build_transcript_payload(
                meeting_id=meeting_id,
                meeting_url=meeting_url,
                duration_seconds=effective_duration,
                segments=segments,
                ai_status=ai_status,
            )
            # Upload в Drive — best-effort: внешний потребитель получает сегменты
            # через JSON, но исторически транскрипт параллельно сохраняется в Drive.
            drive_file: dict[str, str] | None = None
            try:
                drive_file = await _upload_transcript_to_drive(
                    transcript_payload,
                    source_filename=os.path.basename(recording_path),
                )
            except Exception as drive_exc:
                logger.warning(
                    "Drive upload skipped for v1 meeting %s: %s",
                    meeting_id,
                    drive_exc,
                )

            meeting = await _get_meeting_or_404(session, meeting_id)
            meeting.status = "done"
            meeting.duration_seconds = effective_duration
            meeting.error_message = None
            meeting.error_code = None
            meeting.progress_stage = None
            meeting.progress_percent = None
            meeting.enrolled_voiceprints = json.dumps(
                [
                    {
                        "voice_bank_id": entry.get("voice_bank_id"),
                        "display_name": entry.get("display_name"),
                        "person_id": entry.get("person_id"),
                        "role": entry.get("role"),
                    }
                    for entry in enrolled_voiceprints_raw
                    if entry and entry.get("voice_bank_id")
                ],
                ensure_ascii=False,
            )
            meeting.audio_retention_expires_at = _v1_retention_expires_at(
                meeting.created_at
            )
            if drive_file:
                meeting.transcript_url = drive_file["web_view_link"]
                meeting.drive_file_id = drive_file["file_id"]
                meeting.drive_folder_id = drive_file["folder_id"]
                meeting.drive_filename = drive_file["filename"]
                meeting.drive_web_view_link = drive_file["web_view_link"]
            await session.commit()
            logger.info("v1 meeting %s processed successfully", meeting_id)

        except asyncio.CancelledError:
            logger.info(
                "v1 meeting %s was stopped before pipeline finished", meeting_id
            )
            await session.rollback()
            info = active_sessions.get(meeting_id) or {}
            terminal_status = info.get("cancel_terminal_status", "error")
            duration = None
            if capture:
                with suppress(Exception):
                    duration = await capture.stop()
            if telemost:
                with suppress(Exception):
                    await telemost.leave()
            async with async_session() as cancel_session:
                await update_meeting_status(
                    cancel_session,
                    meeting_id,
                    terminal_status,
                    recording_path=recording_path,
                    duration_seconds=duration,
                    error_message=(
                        None
                        if terminal_status == "cancelled"
                        else "Meeting was stopped before recording finished"
                    ),
                    error_code=(
                        None if terminal_status == "cancelled" else "cancelled_internal"
                    ),
                    progress_stage=None,
                    progress_percent=None,
                )
        except Exception as exc:
            logger.exception("v1 meeting %s failed", meeting_id)
            await session.rollback()
            if capture:
                with suppress(Exception):
                    await capture.stop()
            if telemost:
                with suppress(Exception):
                    await telemost.leave()
            async with async_session() as err_session:
                await update_meeting_status(
                    err_session,
                    meeting_id,
                    "error",
                    error_message=str(exc),
                    error_code=_classify_error(exc),
                    progress_stage=None,
                    progress_percent=None,
                )
        finally:
            info = active_sessions.get(meeting_id)
            if info:
                info["recording_started"] = False
            active_sessions.pop(meeting_id, None)


def _classify_error(exc: BaseException) -> str:
    message = str(exc).lower()
    if "403" in message or "unreachable" in message or "timeout" in message:
        return "telemost_unreachable"
    if "transcrib" in message:
        return "transcription_failed"
    return "internal_error"


async def _count_non_terminal_jobs(session: AsyncSession) -> int:
    stmt = select(Meeting).where(
        Meeting.status.in_(list(NonTerminalStatuses) + ["pending", "leaving"])
    )
    result = await session.execute(stmt)
    return len(list(result.scalars().all()))


async def _find_existing_job_by_session(
    session: AsyncSession, session_id: str
) -> Meeting | None:
    stmt = select(Meeting).where(Meeting.session_id == session_id)
    result = await session.execute(stmt)
    meetings = list(result.scalars().all())
    if not meetings:
        return None
    meetings.sort(key=lambda m: m.created_at or "", reverse=True)
    for meeting in meetings:
        if _normalize_status(meeting.status) not in TerminalStatuses:
            return meeting
    return meetings[0]


@app.post(
    "/v1/jobs",
    status_code=202,
)
async def v1_create_job(
    request: CreateJobRequest,
    session: AsyncSession = Depends(get_session),
):
    session_id_raw = str(request.metadata.session_id).strip()
    if not session_id_raw:
        raise HTTPException(status_code=422, detail="metadata.session_id is required")

    existing = await _find_existing_job_by_session(session, session_id_raw)
    if (
        existing is not None
        and _normalize_status(existing.status) not in TerminalStatuses
    ):
        return JSONResponse(
            status_code=409,
            content=JobConflictResponse(
                job_id=existing.id,
                status=_v1_map_status(existing.status),
                error="session_already_processing",
            ).model_dump(),
        )

    active_count = await _count_non_terminal_jobs(session)
    if active_count >= MAX_CONCURRENT_JOBS:
        return JSONResponse(
            status_code=429,
            content=RateLimitedResponse(
                error="rate_limited", retry_after_sec=15
            ).model_dump(),
        )

    speakers_payload = {
        role: speaker.model_dump(exclude_none=False)
        for role, speaker in request.speakers.items()
    }
    speakers_hint = _v1_speakers_hint_from_payload(speakers_payload)
    has_unenrolled = any(not hint["enrolled"] for hint in speakers_hint)

    created_at = _v1_now_iso()
    meeting = Meeting(
        meeting_url=request.source.url,
        bot_name=os.getenv("BOT_NAME", "Транскрибатор"),
        status="pending",
        source="v1",
        session_id=session_id_raw,
        session_type=request.metadata.session_type,
        language=request.metadata.language or "ru",
        speakers_payload=json.dumps(speakers_payload, ensure_ascii=False),
        options_json=json.dumps(request.options.model_dump(), ensure_ascii=False),
        initial_prompt=request.options.initial_prompt,
        callback_url=request.callback_url,
        audio_retention_expires_at=_v1_retention_expires_at(created_at),
        progress_stage="queued",
        created_at=created_at,
    )
    session.add(meeting)
    await session.commit()
    await session.refresh(meeting)

    meeting_id = meeting.id
    active_sessions[meeting_id] = {
        "task": None,
        "session": None,
        "capture": None,
        "stop_requested": False,
        "stop_before_recording": False,
        "recording_started": False,
        "cancel_terminal_status": "error",
        "source": "v1",
    }

    task = asyncio.create_task(
        _bot_workflow_v1(
            meeting_id,
            request.source.url,
            meeting.bot_name,
            speakers_hint=speakers_hint,
            auto_enroll_unknown=has_unenrolled,
            initial_prompt=request.options.initial_prompt,
            llm_refine=request.options.llm_refine,
        )
    )
    active_sessions[meeting_id]["task"] = task

    return JSONResponse(
        status_code=202,
        content=JobCreatedResponse(
            job_id=meeting_id,
            status="queued",
            created_at=created_at,
        ).model_dump(),
    )


@app.get(
    "/v1/jobs/{job_id}",
    response_model=JobStatusResponse,
    response_model_exclude_none=True,
)
async def v1_get_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    meeting = await session.get(Meeting, job_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Job not found")
    return await _v1_build_status_payload(meeting, session)


@app.delete(
    "/v1/jobs/{job_id}",
    status_code=204,
)
async def v1_cancel_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    meeting = await session.get(Meeting, job_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Job not found")

    current_status = _normalize_status(meeting.status)
    if current_status in TerminalStatuses:
        return Response(status_code=204)

    info = active_sessions.get(job_id)
    if info is not None:
        info["cancel_terminal_status"] = "cancelled"

    await _stop_session(job_id)

    # Если цикл не успел зафиксировать статус (например, в pending до старта) —
    # делаем это самостоятельно.
    await session.refresh(meeting)
    if _normalize_status(meeting.status) not in TerminalStatuses:
        await update_meeting_status(
            session,
            job_id,
            "cancelled",
            error_message=None,
            error_code=None,
            progress_stage=None,
            progress_percent=None,
        )
    return Response(status_code=204)


@app.get(
    "/audio/{job_id}.wav",
)
async def v1_download_audio(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    meeting = await session.get(Meeting, job_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Job not found")
    if not _v1_audio_is_available(meeting):
        return JSONResponse(
            status_code=410,
            content=AudioExpiredResponse(error="audio_expired").model_dump(),
        )
    return FileResponse(
        meeting.recording_path,
        media_type="audio/wav",
        filename=f"{job_id}.wav",
    )


async def _audio_retention_cleanup_loop():
    """Периодическая очистка аудио за пределами retention."""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SEC)
            await _run_audio_cleanup()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Audio retention cleanup failed")


async def _run_audio_cleanup() -> int:
    removed = 0
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        stmt = select(Meeting).where(
            Meeting.recording_path.is_not(None),
            Meeting.audio_retention_expires_at.is_not(None),
        )
        result = await session.execute(stmt)
        meetings = list(result.scalars().all())
    for meeting in meetings:
        expires_raw = meeting.audio_retention_expires_at
        if not expires_raw:
            continue
        try:
            expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > now:
            continue
        path = meeting.recording_path
        if not path:
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
                removed += 1
                logger.info(
                    "Removed expired audio for meeting %s (%s)", meeting.id, path
                )
        except OSError as exc:
            logger.warning("Could not remove %s: %s", path, exc)
    return removed
