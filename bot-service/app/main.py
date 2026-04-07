import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
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
    TranscriptResponse,
    TranscriptSegment,
)
from .telemost import TelemostSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TRANSCRIBER_URL = os.getenv("TRANSCRIBER_URL", "http://transcriber:8001")
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", "/app/recordings")

# Активные сессии: meeting_id (str) → {"session": TelemostSession, "capture": AudioCapture, "task": Task}
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


async def _bot_workflow(meeting_id: str, meeting_url: str, bot_name: str):
    """Основной workflow бота: вход → запись → транскрипция."""
    async with async_session() as session:
        try:
            # 1. Вход в Телемост
            telemost = TelemostSession(meeting_url, bot_name)
            recording_path = os.path.join(RECORDINGS_DIR, f"{meeting_id}.wav")
            capture = AudioCapture(recording_path)

            active_sessions[meeting_id] = {
                "session": telemost,
                "capture": capture,
            }

            await telemost.join()

            # 2. Запуск записи
            await update_meeting_status(session, meeting_id, "recording")
            await capture.start()

            # 3. Ожидание завершения встречи
            await telemost.wait_for_end()

            # 4. Остановка записи
            duration = await capture.stop()
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
            segments = await _transcribe(recording_path)

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

        except Exception as e:
            logger.exception("Error processing meeting %s", meeting_id)
            await session.rollback()
            async with async_session() as err_session:
                await update_meeting_status(
                    err_session, meeting_id, "error", error_message=str(e)
                )
        finally:
            active_sessions.pop(meeting_id, None)


async def _transcribe(recording_path: str) -> list[dict]:
    """Отправить запись на транскрипцию."""
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            f"{TRANSCRIBER_URL}/transcribe",
            json={"audio_path": recording_path},
        )
        response.raise_for_status()
        return response.json()["segments"]


async def _stop_session(meeting_id: str):
    """Остановить активную сессию."""
    info = active_sessions.get(meeting_id)
    if not info:
        return

    capture: AudioCapture = info["capture"]
    telemost: TelemostSession = info["session"]

    duration = await capture.stop()
    await telemost.leave()

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

    # Запуск workflow в фоне
    task = asyncio.create_task(
        _bot_workflow(meeting_id, request.meeting_url, request.bot_name)
    )
    if meeting_id in active_sessions:
        active_sessions[meeting_id]["task"] = task
    else:
        active_sessions[meeting_id] = {"task": task}

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
    meeting = await session.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if meeting_id not in active_sessions:
        raise HTTPException(status_code=400, detail="Bot is not active for this meeting")

    # Остановить запись — workflow продолжит транскрипцию
    info = active_sessions.get(meeting_id, {})
    telemost: TelemostSession | None = info.get("session")
    if telemost:
        telemost._meeting_ended.set()

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
    meeting = await session.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

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
    meeting = await session.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

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


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check."""
    return HealthResponse(status="ok", active_bots=len(active_sessions))
