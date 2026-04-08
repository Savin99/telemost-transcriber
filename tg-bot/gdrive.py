"""Загрузка файлов на Google Drive через OAuth2."""

import io
import logging
import os
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive",
]

GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "1jwDy7XAtvX327nf0MJWZHzFERBwkbjvR")
GDRIVE_CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET", "/app/credentials/client_secret.json")
GDRIVE_TOKEN_PATH = os.getenv("GDRIVE_TOKEN_PATH", "/app/credentials/gdrive_token.json")


def _get_credentials() -> Credentials | None:
    """Получить OAuth2 credentials (из сохранённого токена или через авторизацию)."""
    creds = None

    if os.path.exists(GDRIVE_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(GDRIVE_TOKEN_PATH, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Сохранить обновлённый токен
        with open(GDRIVE_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return creds


def authorize():
    """Одноразовая авторизация — запустить вручную для получения токена."""
    flow = InstalledAppFlow.from_client_secrets_file(GDRIVE_CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(port=0)

    os.makedirs(os.path.dirname(GDRIVE_TOKEN_PATH), exist_ok=True)
    with open(GDRIVE_TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"Token saved to {GDRIVE_TOKEN_PATH}")
    return creds


def _get_drive_service():
    """Создать клиент Google Drive API."""
    creds = _get_credentials()
    if not creds or not creds.valid:
        return None
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def format_transcript_md(transcript: dict) -> str:
    """Отформатировать транскрипт как Markdown."""
    segments = transcript.get("segments", [])
    duration = transcript.get("duration_seconds")
    meeting_url = transcript.get("meeting_url", "")

    lines = []
    lines.append("# Транскрипт встречи")
    lines.append("")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"**Дата:** {now}")
    if meeting_url:
        lines.append(f"**Ссылка:** {meeting_url}")
    if duration:
        m, s = divmod(int(duration), 60)
        h, m = divmod(m, 60)
        dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        lines.append(f"**Длительность:** {dur_str}")
    lines.append(f"**Сегментов:** {len(segments)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    current_speaker = None
    for seg in segments:
        speaker = seg.get("speaker") or "Неизвестный"
        start = seg.get("start", 0)
        text = seg.get("text", "").strip()
        if not text:
            continue

        m, s = divmod(int(start), 60)
        ts = f"{m}:{s:02d}"

        if speaker != current_speaker:
            current_speaker = speaker
            lines.append(f"### {speaker} [{ts}]")
            lines.append("")

        lines.append(text)
        lines.append("")

    return "\n".join(lines)


def upload_transcript_md(transcript: dict, filename: str | None = None) -> str | None:
    """Загрузить транскрипт как .md файл на Google Drive.

    Returns:
        URL файла на Google Drive или None при ошибке.
    """
    service = _get_drive_service()
    if not service:
        logger.warning("Google Drive not authorized, skipping upload")
        return None

    try:
        md_content = format_transcript_md(transcript)

        if not filename:
            now = datetime.now().strftime("%Y-%m-%d_%H-%M")
            filename = f"transcript_{now}.md"

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

        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
        ).execute()

        link = file.get("webViewLink")
        logger.info("Transcript uploaded to Google Drive: %s", link)
        return link

    except Exception as e:
        logger.exception("Failed to upload transcript to Google Drive: %s", e)
        return None


if __name__ == "__main__":
    authorize()
