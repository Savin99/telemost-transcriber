"""Backfill semantic titles and folders for existing Drive transcripts.

By default runs in dry-run mode.

Usage:
    python backfill_drive_metadata.py
    python backfill_drive_metadata.py --apply
    python backfill_drive_metadata.py --apply --limit 20
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import re
from collections import deque
from dataclasses import dataclass

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from gdrive import (
    GDRIVE_FOLDER_ID,
    _get_drive_service,
    ensure_drive_folder_path,
)
from meeting_metadata import resolve_meeting_metadata, slugify_filename_stem

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
MARKDOWN_HEADER_RE = re.compile(r"^###\s+(?P<speaker>.+?)\s+\[(?P<timestamp>[0-9:]+)\]\s*$")


@dataclass
class DriveMarkdownFile:
    file_id: str
    name: str
    parents: list[str]
    folder_path: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill semantic titles and folders for existing Drive transcripts.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes on Google Drive. Without this flag only prints a plan.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N markdown files. 0 means no limit.",
    )
    parser.add_argument(
        "--root-folder-id",
        default=GDRIVE_FOLDER_ID,
        help="Root Drive folder to scan recursively.",
    )
    return parser.parse_args()


def parse_timestamp(value: str) -> float:
    parts = [int(part) for part in value.strip().split(":") if part.strip()]
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes * 60 + seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(hours * 3600 + minutes * 60 + seconds)
    return 0.0


def parse_duration(value: str) -> float | None:
    try:
        return parse_timestamp(value)
    except ValueError:
        return None


def parse_markdown_transcript(content: str) -> dict:
    transcript: dict = {
        "segments": [],
        "meeting_url": "",
        "duration_seconds": 0,
        "meeting_date": "",
    }
    lines = content.splitlines()

    for line in lines:
        if line.startswith("**Дата:**"):
            raw_date = line.split("**Дата:**", 1)[1].strip()
            date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", raw_date)
            if date_match:
                transcript["meeting_date"] = date_match.group(0)
        elif line.startswith("**Ссылка:**"):
            transcript["meeting_url"] = line.split("**Ссылка:**", 1)[1].strip()
        elif line.startswith("**Длительность:**"):
            parsed_duration = parse_duration(line.split("**Длительность:**", 1)[1].strip())
            if parsed_duration is not None:
                transcript["duration_seconds"] = parsed_duration

    current_speaker: str | None = None
    current_start = 0.0
    current_lines: list[str] = []

    def flush_segment() -> None:
        nonlocal current_lines
        if current_speaker is None:
            current_lines = []
            return
        text = "\n".join(current_lines).strip()
        if not text:
            current_lines = []
            return
        transcript["segments"].append(
            {
                "speaker": current_speaker,
                "start": current_start,
                "end": current_start,
                "text": text,
            }
        )
        current_lines = []

    for line in lines:
        header_match = MARKDOWN_HEADER_RE.match(line.strip())
        if header_match:
            flush_segment()
            current_speaker = header_match.group("speaker").strip()
            current_start = parse_timestamp(header_match.group("timestamp"))
            continue
        if current_speaker is None:
            continue
        current_lines.append(line)

    flush_segment()
    return transcript


def rewrite_markdown_title(content: str, title: str) -> str:
    lines = content.splitlines()
    if lines and lines[0].startswith("# "):
        lines[0] = f"# {title}"
    else:
        lines = [f"# {title}", ""] + lines
    rewritten = "\n".join(lines)
    if content.endswith("\n"):
        return rewritten + "\n"
    return rewritten


def extract_date_from_filename(filename: str) -> str:
    patterns = (
        re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b"),
        re.compile(r"\b(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{4})\b"),
        re.compile(r"\b(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{2})\b"),
    )
    for pattern in patterns:
        match = pattern.search(filename)
        if not match:
            continue
        year = match.group("year")
        if len(year) == 2:
            year = f"20{year}"
        return f"{year}-{match.group('month')}-{match.group('day')}"
    return ""


def rebuild_filename_with_original_date(metadata, transcript: dict, source_filename: str) -> str:
    filename_date = extract_date_from_filename(source_filename)
    if filename_date:
        return f"{slugify_filename_stem(metadata.title)}_{filename_date}.md"
    meeting_date = str(transcript.get("meeting_date") or "").strip()
    if not meeting_date:
        return metadata.filename
    return f"{slugify_filename_stem(metadata.title)}_{meeting_date}.md"


def build_collision_suffix(file_info: DriveMarkdownFile) -> str:
    source_name = file_info.name
    uuid_match = re.search(
        r"(?<![0-9a-f])([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])",
        source_name,
        flags=re.IGNORECASE,
    )
    if uuid_match:
        return uuid_match.group(1).lower()

    timestamp_match = re.search(r"\b\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\b", source_name)
    if timestamp_match:
        return timestamp_match.group(0).replace("_", "-")

    return file_info.file_id[:8].lower()


def uniquify_target_name(
    target_name: str,
    folder_path: list[str],
    file_info: DriveMarkdownFile,
    seen_targets: dict[tuple[tuple[str, ...], str], int],
) -> str:
    key = (tuple(folder_path), target_name.casefold())
    occurrence = seen_targets.get(key, 0)
    seen_targets[key] = occurrence + 1
    if occurrence == 0:
        return target_name

    stem, ext = os.path.splitext(target_name)
    suffix = build_collision_suffix(file_info)
    return f"{stem}_{suffix}{ext}"


def iter_drive_children(service, parent_id: str) -> list[dict]:
    items: list[dict] = []
    page_token = None
    while True:
        response = service.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, parents)",
            pageSize=200,
            pageToken=page_token,
        ).execute()
        items.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def iter_markdown_files(service, root_folder_id: str) -> list[DriveMarkdownFile]:
    files: list[DriveMarkdownFile] = []
    queue: deque[tuple[str, list[str]]] = deque([(root_folder_id, [])])

    while queue:
        folder_id, folder_path = queue.popleft()
        for item in iter_drive_children(service, folder_id):
            item_id = str(item["id"])
            item_name = str(item.get("name") or "")
            item_mime = str(item.get("mimeType") or "")
            item_parents = [str(parent) for parent in item.get("parents", [])]
            if item_mime == FOLDER_MIME:
                queue.append((item_id, folder_path + [item_name]))
                continue
            if not item_name.lower().endswith(".md"):
                continue
            files.append(
                DriveMarkdownFile(
                    file_id=item_id,
                    name=item_name,
                    parents=item_parents,
                    folder_path=folder_path,
                )
            )
    return files


def download_markdown(service, file_id: str) -> str:
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue().decode("utf-8")


def move_and_update_file(
    service,
    file_info: DriveMarkdownFile,
    target_parent_id: str,
    target_name: str,
    updated_markdown: str,
) -> str:
    media = MediaIoBaseUpload(
        io.BytesIO(updated_markdown.encode("utf-8")),
        mimetype="text/markdown",
        resumable=False,
    )
    update_kwargs = {
        "fileId": file_info.file_id,
        "body": {"name": target_name},
        "media_body": media,
        "fields": "id, name, webViewLink, parents",
    }
    if set(file_info.parents) != {target_parent_id}:
        update_kwargs["addParents"] = target_parent_id
        remove_parents = [parent for parent in file_info.parents if parent != target_parent_id]
        if remove_parents:
            update_kwargs["removeParents"] = ",".join(remove_parents)
    response = service.files().update(**update_kwargs).execute()
    return str(response.get("webViewLink") or "")


def process_markdown_file(
    service,
    file_info: DriveMarkdownFile,
    root_folder_id: str,
    apply: bool,
    seen_targets: dict[tuple[tuple[str, ...], str], int],
) -> tuple[str, str]:
    markdown = download_markdown(service, file_info.file_id)
    transcript = parse_markdown_transcript(markdown)
    metadata = resolve_meeting_metadata(
        transcript=transcript,
        source_filename=file_info.name,
    )
    target_name = rebuild_filename_with_original_date(
        metadata,
        transcript,
        source_filename=file_info.name,
    )
    target_name = uniquify_target_name(
        target_name=target_name,
        folder_path=metadata.folder_path,
        file_info=file_info,
        seen_targets=seen_targets,
    )
    updated_markdown = rewrite_markdown_title(markdown, metadata.title)

    action_bits = [
        f"{'/'.join(file_info.folder_path) or '.'}/{file_info.name}",
        f"-> {'/'.join(metadata.folder_path)}/{target_name}",
        f"[{metadata.source}]",
    ]
    action = " ".join(action_bits)

    if not apply:
        return "preview", action

    target_parent_id = ensure_drive_folder_path(
        service,
        root_folder_id,
        metadata.folder_path,
    )
    link = move_and_update_file(
        service=service,
        file_info=file_info,
        target_parent_id=target_parent_id,
        target_name=target_name,
        updated_markdown=updated_markdown,
    )
    suffix = f" ({link})" if link else ""
    return "updated", action + suffix


def main() -> int:
    args = parse_args()
    service = _get_drive_service()
    if not service:
        logger.error("Google Drive not authorized! Run: python gdrive.py")
        return 1

    markdown_files = iter_markdown_files(service, args.root_folder_id)
    if args.limit > 0:
        markdown_files = markdown_files[: args.limit]

    if not markdown_files:
        logger.info("No markdown transcripts found under root folder %s", args.root_folder_id)
        return 0

    logger.info(
        "%s %d markdown transcript(s) under root folder %s",
        "Applying to" if args.apply else "Previewing",
        len(markdown_files),
        args.root_folder_id,
    )

    updated = 0
    seen_targets: dict[tuple[tuple[str, ...], str], int] = {}
    for index, file_info in enumerate(markdown_files, start=1):
        try:
            status, message = process_markdown_file(
                service=service,
                file_info=file_info,
                root_folder_id=args.root_folder_id,
                apply=args.apply,
                seen_targets=seen_targets,
            )
            logger.info("[%d/%d] %s: %s", index, len(markdown_files), status, message)
            if status == "updated":
                updated += 1
        except Exception as exc:
            logger.exception(
                "[%d/%d] failed to process %s: %s",
                index,
                len(markdown_files),
                file_info.name,
                exc,
            )

    logger.info(
        "Done. Mode=%s total=%d changed=%d",
        "apply" if args.apply else "preview",
        len(markdown_files),
        updated,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
