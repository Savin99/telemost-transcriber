import asyncio
import importlib
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

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


TRANSCRIBER_URL = os.getenv("TRANSCRIBER_URL", "http://localhost:8001")
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", "/app/recordings")
API_KEY_HEADER = "X-API-Key"
LEGACY_STATUS_ALIASES = {"joining": "pending"}
DEFAULT_UNKNOWN_SPEAKER = "Unknown Speaker 1"

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


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias=API_KEY_HEADER),
):
    expected_api_key = (
        getattr(app.state, "service_api_key", None) or _load_service_api_key()
    )
    if not x_api_key or not secrets.compare_digest(x_api_key, expected_api_key):
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


def _upload_recording_to_drive_sync(
    recording_path: str,
    *,
    filename: str | None = None,
) -> dict[str, str] | None:
    gdrive_module = _load_tg_bot_module("gdrive")
    return gdrive_module.upload_recording_file(
        recording_path,
        filename=filename,
    )


async def _upload_recording_to_drive(
    recording_path: str,
    *,
    filename: str | None = None,
) -> dict[str, str] | None:
    return await asyncio.to_thread(
        _upload_recording_to_drive_sync,
        recording_path,
        filename=filename,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service_api_key = _load_service_api_key()
    validate_required_env()
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    await init_db()
    yield
    # Остановить все активные сессии
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

            if _env_bool("UPLOAD_ORIGINAL_RECORDING", True):
                wav_name = drive_file["filename"]
                if wav_name.endswith(".md"):
                    wav_name = wav_name[:-3] + ".wav"
                elif not wav_name.endswith(".wav"):
                    wav_name = f"{wav_name}.wav"
                try:
                    recording_upload = await _upload_recording_to_drive(
                        recording_path,
                        filename=wav_name,
                    )
                    if recording_upload:
                        logger.info(
                            "Meeting %s recording backed up to Drive: %s",
                            meeting_id,
                            recording_upload.get("web_view_link"),
                        )
                except Exception:
                    logger.exception(
                        "Meeting %s: original recording upload failed (non-fatal)",
                        meeting_id,
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
) -> dict:
    """Отправить запись на транскрипцию. Возвращает полный JSON-ответ."""
    payload = {"audio_path": recording_path}
    if num_speakers is not None:
        payload["num_speakers"] = num_speakers
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
