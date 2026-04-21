"""Google Drive Watcher — мониторит папку, транскрибирует аудио, кладёт MD обратно.

Запуск:
    python drive_watcher.py

Логика:
    1. Каждые POLL_INTERVAL секунд проверяет GDRIVE_FOLDER_ID на новые аудиофайлы
    2. Скачивает новый файл → /tmp/drive_watcher/{file_id}/audio.*
    3. Отправляет на WhisperX (TRANSCRIBER_URL/transcribe)
    4. Формирует MD-транскрипт
    5. Загружает MD в ту же папку на Drive
    6. Помечает оригинал как обработанный (description = "transcribed")
"""

import logging
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

from gdrive import (
    GDRIVE_FOLDER_ID,
    _get_drive_service,
    upload_transcript_md,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TRANSCRIBER_URL = os.getenv("TRANSCRIBER_URL", "http://localhost:8001")
POLL_INTERVAL = int(os.getenv("DRIVE_POLL_INTERVAL", "30"))
WORK_DIR = Path(os.getenv("DRIVE_WORK_DIR", "/tmp/drive_watcher"))
ARCHIVE_DIR = Path(
    os.getenv("DRIVE_ARCHIVE_DIR", "/workspace/recordings/drive_imports")
)
AUDIO_MIMES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/webm",
    "audio/mp4",
    "audio/x-m4a",
    "video/mp4",
    "video/webm",
}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".webm", ".m4a", ".mp4", ".flac", ".aac"}

# Маркер обработанных файлов — ставим в description
PROCESSED_MARKER = "transcribed"


def list_new_audio_files(service) -> list[dict]:
    """Получить список необработанных аудиофайлов из папки Drive."""
    query = f"'{GDRIVE_FOLDER_ID}' in parents and trashed = false"
    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name, mimeType, description, size)",
            pageSize=50,
        )
        .execute()
    )
    files = results.get("files", [])

    audio_files = []
    for f in files:
        # Пропускаем уже обработанные
        desc = f.get("description") or ""
        if PROCESSED_MARKER in desc:
            continue
        name = f.get("name", "")
        mime = f.get("mimeType", "")
        ext = Path(name).suffix.lower()
        if mime in AUDIO_MIMES or ext in AUDIO_EXTENSIONS:
            audio_files.append(f)

    return audio_files


def download_file(service, file_id: str, filename: str, dest_dir: Path) -> Path:
    """Скачать файл с Google Drive."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename

    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as f:
        from googleapiclient.http import MediaIoBaseDownload

        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

    logger.info("Downloaded %s (%d bytes)", dest_path, dest_path.stat().st_size)
    return dest_path


def archive_file(source_path: Path, file_id: str, filename: str) -> Path:
    """Сохранить исходник в постоянное хранилище до очистки /tmp."""
    archive_dir = ARCHIVE_DIR / file_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / filename

    if (
        archive_path.exists()
        and archive_path.stat().st_size == source_path.stat().st_size
    ):
        logger.info("Archive already exists: %s", archive_path)
        return archive_path

    shutil.copy2(source_path, archive_path)
    logger.info("Archived source recording to %s", archive_path)
    return archive_path


def transcribe_audio(audio_path: Path, num_speakers: int | None = None) -> dict:
    """Отправить аудио на WhisperX. Возвращает полный JSON-ответ."""
    payload = {"audio_path": str(audio_path)}
    if num_speakers is not None:
        payload["num_speakers"] = num_speakers
    with httpx.Client(
        timeout=float(os.getenv("TRANSCRIBER_TIMEOUT", "1800"))
    ) as client:
        response = client.post(
            f"{TRANSCRIBER_URL}/transcribe",
            json=payload,
        )
        response.raise_for_status()
        return response.json()


def mark_as_processed(service, file_id: str):
    """Пометить файл как обработанный (в description)."""
    service.files().update(
        fileId=file_id,
        body={"description": f"{PROCESSED_MARKER} at {datetime.now().isoformat()}"},
    ).execute()


def parse_num_speakers(filename: str) -> int | None:
    """Извлечь количество спикеров из имени файла.

    Ищет число в конце имени (перед расширением):
        'встреча 3.webm' → 3
        'запись_5.mp3' → 5
        'встреча.webm' → None
    """
    import re

    stem = Path(filename).stem
    match = re.search(r"[\s_\-](\d+)$", stem)
    if match:
        n = int(match.group(1))
        if 1 <= n <= 20:
            return n
    return None


def process_file(service, file_info: dict):
    """Обработать один аудиофайл: скачать → транскрибировать → загрузить MD."""
    file_id = file_info["id"]
    filename = file_info["name"]
    num_speakers = parse_num_speakers(filename)
    logger.info("Processing: %s (%s) speakers=%s", filename, file_id, num_speakers)

    work_dir = WORK_DIR / file_id
    try:
        # 1. Скачать
        downloaded_path = download_file(service, file_id, filename, work_dir)
        archived_path = archive_file(downloaded_path, file_id, filename)

        # 2. Транскрибировать
        logger.info("Transcribing %s (speakers=%s)...", filename, num_speakers)
        transcribe_result = transcribe_audio(archived_path, num_speakers=num_speakers)
        segments = transcribe_result.get("segments", [])
        ai_status = transcribe_result.get("ai_status")

        if not segments:
            logger.warning("No segments found for %s — empty audio?", filename)
            mark_as_processed(service, file_id)
            return

        logger.info(
            "Got %d segments for %s (ai_status=%s)", len(segments), filename, ai_status
        )

        # 3. Сформировать MD
        transcript = {
            "segments": segments,
            "meeting_url": "",
            "duration_seconds": segments[-1].get("end", 0) if segments else 0,
        }
        if ai_status:
            transcript["ai_status"] = ai_status

        # 4. Загрузить MD
        drive_file = upload_transcript_md(
            transcript=transcript,
            source_filename=filename,
            service=service,
        )
        logger.info(
            "Uploaded transcript for %s -> %s",
            filename,
            drive_file.get("web_view_link") if drive_file else None,
        )

        # 5. Пометить оригинал
        mark_as_processed(service, file_id)
        logger.info("Done: %s", filename)

        # 6. Auto-trigger review в Telegram (если есть unknowns + admin chat задан)
        _maybe_trigger_auto_review(
            file_id=file_id,
            filename=filename,
            archived_path=archived_path,
            segments=segments,
            duration_seconds=transcript.get("duration_seconds"),
            drive_file=drive_file,
        )

    except Exception as e:
        logger.exception("Failed to process %s: %s", filename, e)
    finally:
        # Очистить рабочую директорию
        shutil.rmtree(work_dir, ignore_errors=True)


def _maybe_trigger_auto_review(
    file_id: str,
    filename: str,
    archived_path: Path,
    segments: list[dict],
    duration_seconds: float | int | None = None,
    drive_file: dict | None = None,
) -> None:
    admin_chat_id = os.getenv("TELEMOST_ADMIN_CHAT_ID")
    if not admin_chat_id:
        logger.info("TELEMOST_ADMIN_CHAT_ID not set, skipping auto-review trigger")
        return
    has_unknowns = any(
        str(seg.get("speaker") or "").startswith("Unknown") for seg in segments
    )
    if not has_unknowns:
        logger.info("No unknown speakers in %s, auto-review not needed", filename)
        return

    meeting_id = f"drive-{file_id}"[:60]
    db_path = os.getenv(
        "BOT_SERVICE_DB",
        "/workspace/telemost-transcriber/bot-service/transcriber.db",
    )
    drive_file = drive_file or {}
    drive_file_id = str(drive_file.get("file_id") or "") or None
    drive_folder_id = str(drive_file.get("folder_id") or "") or None
    drive_filename = str(drive_file.get("filename") or "") or None
    drive_web_view_link = str(drive_file.get("web_view_link") or "") or None
    try:
        import sqlite3
        from datetime import timezone as _tz

        conn = sqlite3.connect(db_path)
        now = datetime.now(_tz.utc).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO meetings "
            "(id, meeting_url, bot_name, status, recording_path, "
            " duration_seconds, transcript_url, "
            " drive_file_id, drive_folder_id, drive_filename, drive_web_view_link, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meeting_id,
                f"drive-watcher-{file_id}",
                "Транскрибатор",
                "done",
                str(archived_path),
                int(duration_seconds) if duration_seconds else None,
                drive_web_view_link,
                drive_file_id,
                drive_folder_id,
                drive_filename,
                drive_web_view_link,
                now,
                now,
            ),
        )
        # Сохранить сегменты — чтобы bot.py мог после review дернуть
        # GET /transcripts/{id} и получить транскрипт с актуальными именами.
        conn.execute(
            "DELETE FROM transcript_segments WHERE meeting_id = ?",
            (meeting_id,),
        )
        import uuid as _uuid

        conn.executemany(
            "INSERT INTO transcript_segments "
            "(id, meeting_id, speaker, start_time, end_time, text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    _uuid.uuid4().hex,
                    meeting_id,
                    str(seg.get("speaker") or "Unknown"),
                    float(seg.get("start") or 0),
                    float(seg.get("end") or 0),
                    str(seg.get("text") or ""),
                )
                for seg in segments
            ],
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception(
            "Failed to INSERT meeting row for auto-review: %s",
            meeting_id,
        )
        return

    tg_bot_url = os.getenv("TG_BOT_INTERNAL_URL", "http://127.0.0.1:8100")
    try:
        import json as _json
        import urllib.request as _urllib

        body = _json.dumps(
            {
                "meeting_id": meeting_id,
                "chat_id": int(admin_chat_id),
                "filename": filename,
            }
        ).encode("utf-8")
        req = _urllib.Request(
            f"{tg_bot_url}/internal/trigger_review",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urllib.urlopen(req, timeout=15) as resp:
            logger.info(
                "Auto-review triggered: meeting=%s status=%s",
                meeting_id,
                resp.status,
            )
    except Exception:
        logger.exception(
            "Failed to trigger auto-review for meeting=%s",
            meeting_id,
        )


def main():
    logger.info("Drive Watcher starting...")
    logger.info("Folder ID: %s", GDRIVE_FOLDER_ID)
    logger.info("Transcriber: %s", TRANSCRIBER_URL)
    logger.info("Poll interval: %ds", POLL_INTERVAL)

    service = _get_drive_service()
    if not service:
        logger.error("Google Drive not authorized! Run: python gdrive.py")
        sys.exit(1)

    logger.info("Google Drive connected. Watching for new audio files...")

    while True:
        try:
            files = list_new_audio_files(service)
            if files:
                logger.info("Found %d new audio file(s)", len(files))
                for f in files:
                    process_file(service, f)
            else:
                logger.debug("No new files")
        except Exception as e:
            logger.exception("Error in watch loop: %s", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
