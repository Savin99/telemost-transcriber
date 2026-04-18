"""Загрузка файлов на Google Drive через OAuth2."""

import io
import logging
import os
from datetime import datetime
from typing import Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from meeting_metadata import (
    resolve_meeting_metadata,
    sanitize_drive_component,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive",
]

GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "1jwDy7XAtvX327nf0MJWZHzFERBwkbjvR")
GDRIVE_CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET", "/app/credentials/client_secret.json")
GDRIVE_TOKEN_PATH = os.getenv("GDRIVE_TOKEN_PATH", "/app/credentials/gdrive_token.json")


def _build_upload_result(
    *,
    file_id: str,
    folder_id: str,
    filename: str,
    web_view_link: str,
) -> dict[str, str]:
    return {
        "file_id": file_id,
        "folder_id": folder_id,
        "filename": filename,
        "web_view_link": web_view_link,
    }


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
    """Одноразовая авторизация — запустить вручную для получения токена.

    На сервере без браузера используется консольный режим:
    1. Покажет URL
    2. Открой URL в браузере
    3. Авторизуйся и скопируй код
    4. Вставь код в консоль
    """
    flow = InstalledAppFlow.from_client_secrets_file(GDRIVE_CLIENT_SECRET, SCOPES)
    try:
        creds = flow.run_local_server(port=0)
    except Exception:
        # Fallback для серверов без браузера
        creds = flow.run_console()

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
    title = str(transcript.get("title") or "Транскрипт встречи").strip()

    lines = []
    lines.append(f"# {title}")
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

    ai_status = transcript.get("ai_status")
    if ai_status:
        sr = ai_status.get("speaker_refinement", "disabled")
        tr = ai_status.get("transcript_refinement", "disabled")
        parts = []
        parts.append(f"спикеры: {sr}")
        parts.append(f"текст: {tr}")
        lines.append(f"**AI:** {', '.join(parts)}")

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


def _drive_query_quote(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _find_drive_folder(service, name: str, parent_id: str) -> str | None:
    query = (
        "mimeType = 'application/vnd.google-apps.folder' and "
        f"name = '{_drive_query_quote(name)}' and "
        f"'{_drive_query_quote(parent_id)}' in parents and trashed = false"
    )
    response = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name)",
        pageSize=10,
    ).execute()
    files = response.get("files", [])
    if not files:
        return None
    return files[0]["id"]


def ensure_drive_folder(service, name: str, parent_id: str) -> str:
    name = sanitize_drive_component(name, fallback="General")
    existing_id = _find_drive_folder(service, name, parent_id)
    if existing_id:
        return existing_id

    folder = service.files().create(
        body={
            "name": name,
            "parents": [parent_id],
            "mimeType": "application/vnd.google-apps.folder",
        },
        fields="id",
    ).execute()
    return folder["id"]


def ensure_drive_folder_path(
    service,
    root_folder_id: str,
    folder_path: Iterable[str],
) -> str:
    current_parent = root_folder_id
    for folder_name in folder_path:
        current_parent = ensure_drive_folder(service, folder_name, current_parent)
    return current_parent


def upload_transcript_md(
    transcript: dict,
    filename: str | None = None,
    source_filename: str | None = None,
    service=None,
) -> dict[str, str] | None:
    """Загрузить транскрипт как .md файл на Google Drive.

    Returns:
        Drive upload metadata or None при ошибке.
    """
    service = service or _get_drive_service()
    if not service:
        logger.warning("Google Drive not authorized, skipping upload")
        return None

    try:
        metadata = resolve_meeting_metadata(
            transcript=transcript,
            source_filename=source_filename,
        )
        enriched_transcript = dict(transcript)
        enriched_transcript["title"] = metadata.title

        if not filename:
            filename = metadata.filename

        parent_folder_id = ensure_drive_folder_path(
            service,
            GDRIVE_FOLDER_ID,
            metadata.folder_path,
        )

        file_metadata = {
            "name": sanitize_drive_component(filename, fallback=metadata.filename),
            "parents": [parent_folder_id],
            "mimeType": "text/markdown",
        }

        media = MediaIoBaseUpload(
            io.BytesIO(format_transcript_md(enriched_transcript).encode("utf-8")),
            mimetype="text/markdown",
            resumable=False,
        )

        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
        ).execute()

        link = str(file.get("webViewLink") or "")
        file_id = str(file.get("id") or "")
        result = _build_upload_result(
            file_id=file_id,
            folder_id=parent_folder_id,
            filename=file_metadata["name"],
            web_view_link=link,
        )
        logger.info(
            "Transcript uploaded to Google Drive: %s (%s/%s)",
            link,
            " / ".join(metadata.folder_path),
            file_metadata["name"],
        )
        return result

    except Exception as e:
        logger.exception("Failed to upload transcript to Google Drive: %s", e)
        return None


def upload_recording_file(
    recording_path: str,
    *,
    filename: str | None = None,
    service=None,
    originals_folder_name: str = "Originals",
) -> dict[str, str] | None:
    """Загрузить оригинальный wav-файл записи в подпапку Originals/ на Drive.

    Делается после upload транскрипта — чтобы оригинал не пропал при rebuild
    Vast-инстанса. Best-effort: ошибка upload не роняет основной workflow.
    """
    service = service or _get_drive_service()
    if not service:
        logger.warning("Google Drive not authorized, skipping recording upload")
        return None
    if not os.path.exists(recording_path):
        logger.warning("Recording file missing, skipping upload: %s", recording_path)
        return None
    try:
        parent_folder_id = ensure_drive_folder(
            service,
            originals_folder_name,
            GDRIVE_FOLDER_ID,
        )
        name = sanitize_drive_component(
            filename or os.path.basename(recording_path),
            fallback=os.path.basename(recording_path),
        )
        file_metadata = {"name": name, "parents": [parent_folder_id]}
        media = MediaFileUpload(
            recording_path,
            mimetype="audio/wav",
            resumable=True,
            chunksize=8 * 1024 * 1024,
        )
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
        ).execute()
        link = str(file.get("webViewLink") or "")
        file_id = str(file.get("id") or "")
        logger.info(
            "Recording uploaded to Google Drive: %s (%s/%s, %d bytes)",
            link,
            originals_folder_name,
            name,
            os.path.getsize(recording_path),
        )
        return _build_upload_result(
            file_id=file_id,
            folder_id=parent_folder_id,
            filename=name,
            web_view_link=link,
        )
    except Exception as e:
        logger.exception("Failed to upload recording to Google Drive: %s", e)
        return None


if __name__ == "__main__":
    authorize()
