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

import io
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

from gdrive import (
    GDRIVE_FOLDER_ID,
    _get_drive_service,
    format_transcript_md,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TRANSCRIBER_URL = os.getenv("TRANSCRIBER_URL", "http://localhost:8001")
POLL_INTERVAL = int(os.getenv("DRIVE_POLL_INTERVAL", "30"))
WORK_DIR = Path(os.getenv("DRIVE_WORK_DIR", "/tmp/drive_watcher"))
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
    query = (
        f"'{GDRIVE_FOLDER_ID}' in parents"
        f" and trashed = false"
        f" and not description contains '{PROCESSED_MARKER}'"
    )
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


def transcribe_audio(audio_path: Path) -> list[dict]:
    """Отправить аудио на WhisperX и получить сегменты."""
    with httpx.Client(timeout=600) as client:
        response = client.post(
            f"{TRANSCRIBER_URL}/transcribe",
            json={"audio_path": str(audio_path)},
        )
        response.raise_for_status()
        return response.json().get("segments", [])


def upload_md(service, md_content: str, filename: str) -> str | None:
    """Загрузить MD файл на Google Drive."""
    from googleapiclient.http import MediaIoBaseUpload

    file_metadata = {
        "name": filename,
        "parents": [GDRIVE_FOLDER_ID],
        "mimeType": "text/markdown",
    }
    media = MediaIoBaseUpload(
        io.BytesIO(md_content.encode("utf-8")),
        mimetype="text/markdown",
        resumable=False,
    )
    file = (
        service.files()
        .create(body=file_metadata, media_body=media, fields="id, webViewLink")
        .execute()
    )
    return file.get("webViewLink")


def mark_as_processed(service, file_id: str):
    """Пометить файл как обработанный (в description)."""
    service.files().update(
        fileId=file_id,
        body={"description": f"{PROCESSED_MARKER} at {datetime.now().isoformat()}"},
    ).execute()


def process_file(service, file_info: dict):
    """Обработать один аудиофайл: скачать → транскрибировать → загрузить MD."""
    file_id = file_info["id"]
    filename = file_info["name"]
    logger.info("Processing: %s (%s)", filename, file_id)

    work_dir = WORK_DIR / file_id
    try:
        # 1. Скачать
        audio_path = download_file(service, file_id, filename, work_dir)

        # 2. Транскрибировать
        logger.info("Transcribing %s...", filename)
        segments = transcribe_audio(audio_path)

        if not segments:
            logger.warning("No segments found for %s — empty audio?", filename)
            mark_as_processed(service, file_id)
            return

        logger.info("Got %d segments for %s", len(segments), filename)

        # 3. Сформировать MD
        transcript = {
            "segments": segments,
            "meeting_url": "",
            "duration_seconds": segments[-1].get("end", 0) if segments else 0,
        }
        md_content = format_transcript_md(transcript)

        # 4. Загрузить MD
        stem = Path(filename).stem
        md_filename = f"{stem}_transcript.md"
        link = upload_md(service, md_content, md_filename)
        logger.info("Uploaded transcript: %s -> %s", md_filename, link)

        # 5. Пометить оригинал
        mark_as_processed(service, file_id)
        logger.info("Done: %s", filename)

    except Exception as e:
        logger.exception("Failed to process %s: %s", filename, e)
    finally:
        # Очистить рабочую директорию
        import shutil

        shutil.rmtree(work_dir, ignore_errors=True)


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
