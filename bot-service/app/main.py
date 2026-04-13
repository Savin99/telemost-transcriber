import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import Depends, FastAPI, HTTPException, Response
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

TRANSCRIBER_URL = os.getenv("TRANSCRIBER_URL", "http://transcriber:8001")
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", "/app/recordings")

# Активные сессии:
# meeting_id -> {
#   "task": asyncio.Task | None,
#   "session": TelemostSession | None,
#   "capture": AudioCapture | None,
#   "stop_requested": bool,
#   "recording_started": bool,
# }
active_sessions: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    await init_db()
    yield
    # Остановить все активные сессии
    for meeting_id in list(active_sessions.keys()):
        await _stop_session(meeting_id)


app = FastAPI(title="Telemost Bot", lifespan=lifespan)


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
                raise asyncio.CancelledError

            # 2. Запуск записи (PulseAudio per-session sink + FFmpeg)
            await update_meeting_status(session, meeting_id, "recording")
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
            )

            # 5. Закрытие браузера
            await telemost.leave()

            # 6. Отправка на транскрипцию
            await update_meeting_status(session, meeting_id, "transcribing")
            segments = await _transcribe(recording_path, num_speakers=num_speakers)

            # 7. Сохранение результатов
            for seg in segments:
                db_segment = TranscriptSegmentDB(
                    meeting_id=meeting_id,
                    speaker=seg.get("speaker"),
                    start_time=float(seg["start"]),
                    end_time=float(seg["end"]),
                    text=str(seg["text"]),
                )
                session.add(db_segment)
            await session.commit()

            await update_meeting_status(session, meeting_id, "done")
            logger.info("Meeting %s processed successfully", meeting_id)

        except asyncio.CancelledError:
            logger.info("Meeting %s was stopped before recording pipeline finished", meeting_id)
            await session.rollback()

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
                    "done",
                    recording_path=recording_path,
                    duration_seconds=duration,
                    error_message=None,
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
                    err_session, meeting_id, "error", error_message=str(e)
                )
        finally:
            info = active_sessions.get(meeting_id)
            if info:
                info["recording_started"] = False
            active_sessions.pop(meeting_id, None)


async def _transcribe(
    recording_path: str,
    num_speakers: int | None = None,
) -> list[dict]:
    """Отправить запись на транскрипцию."""
    payload = {"audio_path": recording_path}
    if num_speakers is not None:
        payload["num_speakers"] = num_speakers
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            f"{TRANSCRIBER_URL}/transcribe",
            json=payload,
        )
        response.raise_for_status()
        return response.json()["segments"]


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


@app.post("/join", response_model=MeetingStatus)
async def join_meeting(
    request: JoinRequest,
    session: AsyncSession = Depends(get_session),
):
    """Подключить бота к встрече."""
    meeting = Meeting(
        meeting_url=request.meeting_url,
        bot_name=request.bot_name,
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
        "recording_started": False,
    }

    # Запуск workflow в фоне
    task = asyncio.create_task(
        _bot_workflow(meeting_id, request.meeting_url, request.bot_name, request.num_speakers)
    )
    active_sessions[meeting_id]["task"] = task

    return MeetingStatus(
        meeting_id=meeting.id,
        status=meeting.status,
        meeting_url=meeting.meeting_url,
        created_at=meeting.created_at,
    )


@app.post("/leave/{meeting_id}", response_model=MeetingStatus)
async def leave_meeting(
    meeting_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Отключить бота от встречи и запустить транскрипцию."""
    meeting = await _get_meeting_or_404(session, meeting_id)

    if meeting_id not in active_sessions:
        raise HTTPException(status_code=400, detail="Bot is not active for this meeting")

    # Отмечаем stop всегда; дальнейшая логика зависит от стадии запуска.
    info = active_sessions.get(meeting_id, {})
    info["stop_requested"] = True
    task: asyncio.Task | None = info.get("task")
    recording_started = bool(info.get("recording_started"))
    telemost: TelemostSession | None = info.get("session")

    # Если запись уже идет, workflow завершится через событие окончания.
    if recording_started and telemost:
        telemost._meeting_ended.set()
    # Если запись ещё не стартовала — отменяем задачу сразу.
    elif task and not task.done():
        task.cancel()

    await session.refresh(meeting)
    return MeetingStatus(
        meeting_id=meeting.id,
        status=meeting.status,
        meeting_url=meeting.meeting_url,
        duration_seconds=meeting.duration_seconds,
        created_at=meeting.created_at,
    )


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

    return MeetingStatus(
        meeting_id=meeting.id,
        status=meeting.status,
        meeting_url=meeting.meeting_url,
        duration_seconds=duration,
        error_message=meeting.error_message,
        created_at=meeting.created_at,
    )


@app.get("/transcripts/{meeting_id}", response_model=TranscriptResponse)
async def get_transcript(
    meeting_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Получить транскрипт встречи."""
    meeting = await _get_meeting_or_404(session, meeting_id)

    result = await session.execute(
        select(TranscriptSegmentDB)
        .where(TranscriptSegmentDB.meeting_id == meeting_id)
        .order_by(TranscriptSegmentDB.start_time)
    )
    segments = result.scalars().all()

    return TranscriptResponse(
        meeting_id=meeting.id,
        meeting_url=meeting.meeting_url,
        duration_seconds=meeting.duration_seconds,
        segments=[
            TranscriptSegment(
                speaker=seg.speaker,
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
        query = query.where(Meeting.status == status)

    result = await session.execute(query)
    meetings = result.scalars().all()

    return [
        MeetingStatus(
            meeting_id=meeting.id,
            status=meeting.status,
            meeting_url=meeting.meeting_url,
            duration_seconds=meeting.duration_seconds,
            error_message=meeting.error_message,
            created_at=meeting.created_at,
        )
        for meeting in meetings
    ]


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
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
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


@app.get("/meetings/{meeting_id}/speaker-review/{meeting_key}/{speaker_label}/samples/{sample_index}")
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
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
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
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    data = response.json()
    rename_sources = [data["previous_name"]]
    rename_sources.extend(
        item["previous_name"] for item in data.get("merged_labels", [])
    )
    for previous_name in {name for name in rename_sources if name and name != data["name"]}:
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
