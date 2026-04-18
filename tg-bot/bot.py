"""Telegram-бот для транскрипции встреч Телемоста.

Работает в группах и в ЛС:
- Автоматически ловит ссылки telemost.yandex.ru/j/... в чате
- /rec <ссылка> — подключиться к встрече
- /stop — остановить запись и получить транскрипт
- /status — статус текущей записи
"""

import asyncio
import html
import logging
import os
import re
from datetime import datetime

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message
from aiohttp import web

from gdrive import update_transcript_md, upload_transcript_md

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TG_TOKEN = os.environ["TG_BOT_TOKEN"]
BOT_API = os.getenv("BOT_API_URL", "http://localhost:8000")
BOT_API_KEY = os.getenv("BOT_API_KEY") or os.getenv("TELEMOST_SERVICE_API_KEY")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))
VOICE_REVIEW_SAMPLE_COUNT = int(os.getenv("VOICE_REVIEW_SAMPLE_COUNT", "2"))
VOICE_REVIEW_SAMPLE_MAX_SECONDS = float(
    os.getenv("VOICE_REVIEW_SAMPLE_MAX_SECONDS", "10")
)

bot = Bot(token=TG_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Regex для ссылок Телемоста
TELEMOST_RE = re.compile(r"https?://telemost(?:\.360)?\.yandex\.ru/j/\d+")

# Активные сессии: chat_id → {"meeting_id": str, "url": str}
active: dict[int, dict] = {}
pending_reviews: dict[int, dict] = {}

# Локи per chat_id — защита от гонки, когда несколько
# handle_meeting_url одновременно создают /join для одного чата.
# Без этого три параллельные ссылки в одном чате создают три записи,
# потому что все три проходят проверку `chat_id in active` до записи.
_join_locks: dict[int, asyncio.Lock] = {}


def _get_join_lock(chat_id: int) -> asyncio.Lock:
    lock = _join_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _join_locks[chat_id] = lock
    return lock


def _safe_html(value: object) -> str:
    return html.escape(str(value), quote=False)


def _bot_api_headers() -> dict[str, str]:
    if not BOT_API_KEY:
        return {}
    return {"X-API-Key": BOT_API_KEY}


@dp.message(Command("start", "help"))
async def cmd_start(msg: Message):
    await msg.answer(
        "Я транскрибирую встречи Яндекс Телемоста.\n\n"
        "<b>Как использовать:</b>\n"
        "1. Кинь ссылку на Телемост — я подключусь автоматически\n"
        "2. Или: <code>/rec https://telemost.yandex.ru/j/...</code>\n\n"
        "<b>Команды:</b>\n"
        "/stop — остановить запись и получить транскрипт\n"
        "/status — статус текущей записи\n"
        "/voices — показать последние готовые встречи для разметки голосов\n"
        "/voices MEETING_ID — начать разметку для старой записи\n\n"
        "После транскрипта могу прислать примеры аудио для неизвестных голосов, "
        "и ты просто ответишь именем.\n\n"
        "Работаю и в группах — просто добавь меня и кидайте ссылки."
    )


@dp.message(Command("mychatid"))
async def cmd_mychatid(msg: Message):
    """Возвращает chat_id — для настройки TELEMOST_ADMIN_CHAT_ID в .bashrc."""
    await msg.answer(
        f"Твой chat_id: <code>{msg.chat.id}</code>\n\n"
        f"Пропиши в <code>/workspace/.bashrc</code>:\n"
        f"<code>export TELEMOST_ADMIN_CHAT_ID={msg.chat.id}</code>"
    )


@dp.message(Command("rec"))
async def cmd_rec(msg: Message, command: CommandObject):
    """Подключиться к встрече: /rec <url> [кол-во спикеров]."""
    text = command.args or ""
    match = TELEMOST_RE.search(text)
    if not match:
        await msg.answer(
            "Укажи ссылку: <code>/rec https://telemost.yandex.ru/j/...</code>\nМожно указать кол-во спикеров: <code>/rec ссылка 3</code>"
        )
        return
    # Парсим число спикеров после URL
    remainder = text[match.end() :].strip()
    num_speakers = int(remainder) if remainder.isdigit() else None
    await _join_meeting(msg, match.group(0), num_speakers=num_speakers)


@dp.message(Command("stop"))
async def cmd_stop(msg: Message):
    session = active.get(msg.chat.id)
    if not session:
        await msg.answer("Нет активной записи.")
        return

    meeting_id = session["meeting_id"]
    status_msg = await msg.answer("Останавливаю запись...")

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{BOT_API}/leave/{meeting_id}",
                headers=_bot_api_headers(),
            )
            resp.raise_for_status()
        except Exception as e:
            await status_msg.edit_text(f"Ошибка: {_safe_html(e)}")
            return

    await status_msg.edit_text("Запись остановлена. Транскрибирую... ⏳")
    transcript = await _wait_and_get_transcript(meeting_id)
    active.pop(msg.chat.id, None)

    if transcript:
        await _send_transcript(msg.chat.id, transcript)
    else:
        await status_msg.edit_text("Не удалось получить транскрипт.")


@dp.message(Command("status"))
async def cmd_status(msg: Message):
    session = active.get(msg.chat.id)
    if not session:
        await msg.answer("Нет активной записи.")
        return

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                f"{BOT_API}/status/{session['meeting_id']}",
                headers=_bot_api_headers(),
            )
            data = resp.json()
            status = data["status"]
            duration = data.get("duration_seconds")
            dur_str = ""
            if duration:
                m, s = divmod(int(duration), 60)
                dur_str = f" ({m}:{s:02d})"
            await msg.answer(f"Статус: <b>{_safe_html(status)}</b>{dur_str}")
        except Exception as e:
            await msg.answer(f"Ошибка: {_safe_html(e)}")


@dp.message(Command("voices"))
async def cmd_voices(msg: Message, command: CommandObject):
    meeting_id = (command.args or "").strip()
    if meeting_id:
        await _start_speaker_review(msg.chat.id, meeting_id)
        return

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(
                f"{BOT_API}/meetings",
                params={"status": "done", "limit": 5},
                headers=_bot_api_headers(),
            )
            response.raise_for_status()
            meetings = response.json()
        except Exception as e:
            await msg.answer(f"Не удалось получить список встреч: {_safe_html(e)}")
            return

    if not meetings:
        await msg.answer("Пока нет готовых встреч для разметки голосов.")
        return

    lines = ["Последние готовые встречи:", ""]
    for meeting in meetings:
        created_at = _format_created_at(meeting.get("created_at"))
        duration = meeting.get("duration_seconds")
        duration_text = _format_time(float(duration)) if duration else "?"
        lines.append(
            f"<code>{_safe_html(meeting['meeting_id'])}</code>  "
            f"{_safe_html(created_at)}  {duration_text}"
        )

    lines.extend(
        [
            "",
            "Чтобы начать разметку, отправь:",
            "<code>/voices MEETING_ID</code>",
        ]
    )
    await msg.answer("\n".join(lines))


@dp.message(Command("skipvoice"))
async def cmd_skipvoice(msg: Message):
    state = pending_reviews.get(msg.chat.id)
    if not state or not state.get("current"):
        await msg.answer("Сейчас нет активного вопроса по новому голосу.")
        return

    skipped = state["current"]["current_name"]
    await msg.answer(f"Ок, пропускаю {_safe_html(skipped)}.")
    state["current"] = None
    await _send_next_review_item(msg.chat.id)


@dp.message(Command("stopvoices"))
async def cmd_stopvoices(msg: Message):
    if msg.chat.id in pending_reviews:
        pending_reviews.pop(msg.chat.id, None)
        await msg.answer("Остановил разметку новых голосов.")
        return
    await msg.answer("Сейчас нет активной разметки голосов.")


def _is_pending_voice_label_message(message: Message) -> bool:
    text = (message.text or "").strip()
    return (
        message.chat.id in pending_reviews
        and bool(text)
        and not text.startswith("/")
        and not TELEMOST_RE.search(text)
    )


@dp.message(_is_pending_voice_label_message)
async def handle_pending_voice_label(msg: Message):
    state = pending_reviews.get(msg.chat.id)
    if not state:
        return

    text = (msg.text or "").strip()
    if not text:
        return
    if text.startswith("/"):
        return
    if TELEMOST_RE.search(text):
        return

    current = state.get("current")
    if not current:
        return

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            response = await client.post(
                f"{BOT_API}/meetings/{state['meeting_id']}/speaker-review/"
                f"{state['meeting_key']}/{current['speaker_label']}/label",
                json={"name": text},
                headers=_bot_api_headers(),
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            await msg.answer(f"Не удалось сохранить голос: {_safe_html(e)}")
            return

    await msg.answer(
        f"Запомнил: <b>{_safe_html(data['name'])}</b>. "
        f"В этой встрече тоже переименовал {_safe_html(data['previous_name'])}."
    )
    merged_labels = data.get("merged_labels", [])
    if merged_labels:
        merged_speaker_labels = {item["speaker_label"] for item in merged_labels}
        state["queue"] = [
            item
            for item in state.get("queue", [])
            if item.get("speaker_label") not in merged_speaker_labels
        ]
        await msg.answer(
            f"И ещё автоматически склеил похожих кластеров: {len(merged_labels)}."
        )
    state["current"] = None
    await _send_next_review_item(msg.chat.id)


@dp.message(F.text.regexp(TELEMOST_RE))
async def handle_meeting_url(msg: Message):
    """Автоматический перехват ссылок Телемоста в чате."""
    url = TELEMOST_RE.search(msg.text).group(0)
    await _join_meeting(msg, url)


async def _join_meeting(msg: Message, url: str, num_speakers: int | None = None):
    """Подключить бота к встрече."""
    chat_id = msg.chat.id

    async with _get_join_lock(chat_id):
        if chat_id in active:
            if active[chat_id].get("url") == url:
                return
            await msg.answer("Уже идёт запись. Сначала /stop")
            return

        # Застолбить место до вызова /join, чтобы параллельные
        # хендлеры увидели занятость и вернулись.
        active[chat_id] = {"meeting_id": None, "url": url}

        status_msg = await msg.answer("Подключаюсь к встрече...")

        payload = {"meeting_url": url}
        if num_speakers is not None:
            payload["num_speakers"] = num_speakers

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    f"{BOT_API}/join",
                    json=payload,
                    headers=_bot_api_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                meeting_id = data["meeting_id"]
            except Exception as e:
                active.pop(chat_id, None)
                await status_msg.edit_text(f"Не удалось подключиться: {_safe_html(e)}")
                return

        active[chat_id] = {"meeting_id": meeting_id, "url": url}

    await status_msg.edit_text(
        "Бот подключается к встрече.\n\n"
        "Когда закончите — отправьте /stop\n"
        "Или бот завершит автоматически, когда встреча закончится."
    )

    # Фоновое ожидание завершения
    asyncio.create_task(_auto_wait(chat_id, meeting_id))


async def _auto_wait(chat_id: int, meeting_id: str):
    """Фоновый поллинг — ловит завершение встречи или ошибку."""
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            await asyncio.sleep(POLL_INTERVAL)

            # Проверяем что сессия ещё наша
            session = active.get(chat_id)
            if not session or session["meeting_id"] != meeting_id:
                return

            try:
                resp = await client.get(
                    f"{BOT_API}/status/{meeting_id}",
                    headers=_bot_api_headers(),
                )
                data = resp.json()
                status = data["status"]
            except Exception:
                continue

            if status == "done":
                active.pop(chat_id, None)
                try:
                    resp = await client.get(
                        f"{BOT_API}/transcripts/{meeting_id}",
                        headers=_bot_api_headers(),
                    )
                    await _send_transcript(chat_id, resp.json())
                except Exception as e:
                    await bot.send_message(
                        chat_id, f"Транскрипт готов, но ошибка: {_safe_html(e)}"
                    )
                return

            if status == "error":
                active.pop(chat_id, None)
                error_msg = _safe_html(data.get("error_message", "Неизвестная ошибка"))
                await bot.send_message(chat_id, f"Ошибка записи: {error_msg}")
                return


async def _wait_and_get_transcript(meeting_id: str) -> dict | None:
    """Поллить статус до готовности транскрипта."""
    async with httpx.AsyncClient(timeout=600) as client:
        for _ in range(120):  # Макс ~20 минут
            await asyncio.sleep(POLL_INTERVAL)
            try:
                resp = await client.get(
                    f"{BOT_API}/status/{meeting_id}",
                    headers=_bot_api_headers(),
                )
                data = resp.json()
            except Exception:
                continue

            if data["status"] == "done":
                resp = await client.get(
                    f"{BOT_API}/transcripts/{meeting_id}",
                    headers=_bot_api_headers(),
                )
                return resp.json()

            if data["status"] == "error":
                return None

    return None


def _format_time(seconds: float) -> str:
    total_seconds = int(seconds)
    minutes, seconds_part = divmod(total_seconds, 60)
    hours, minutes_part = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes_part:02d}:{seconds_part:02d}"
    return f"{minutes_part}:{seconds_part:02d}"


def _format_created_at(value: str | None) -> str:
    if not value:
        return "без даты"
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return value
    return dt.strftime("%d.%m %H:%M")


def _format_segment_preview(segments: list[dict]) -> str:
    if not segments:
        return "без подходящих сегментов"
    parts = []
    for segment in segments[:3]:
        parts.append(
            f"{_format_time(float(segment['start']))}-{_format_time(float(segment['end']))}"
        )
    if len(segments) > 3:
        parts.append("...")
    return ", ".join(parts)


async def _start_speaker_review(chat_id: int, meeting_id: str, quiet: bool = False):
    async with httpx.AsyncClient(timeout=600) as client:
        try:
            response = await client.post(
                f"{BOT_API}/meetings/{meeting_id}/speaker-review",
                json={
                    "samples_per_speaker": VOICE_REVIEW_SAMPLE_COUNT,
                    "sample_max_seconds": VOICE_REVIEW_SAMPLE_MAX_SECONDS,
                },
                headers=_bot_api_headers(),
            )
            response.raise_for_status()
            review = response.json()
        except Exception as e:
            if quiet:
                logger.warning(
                    "Could not start speaker review for %s: %s", meeting_id, e
                )
            else:
                await bot.send_message(
                    chat_id,
                    f"Не удалось подготовить разметку для "
                    f"<code>{_safe_html(meeting_id)}</code>: {_safe_html(e)}",
                )
            return

    unknown_items = [
        item
        for item in review.get("items", [])
        if not item.get("is_known") and int(item.get("sample_count", 0)) > 0
    ]
    if not unknown_items:
        if not quiet:
            await bot.send_message(
                chat_id,
                f"Для встречи <code>{_safe_html(meeting_id)}</code> новых голосов не нашёл.",
            )
        return

    if chat_id in pending_reviews:
        pending_reviews.pop(chat_id, None)

    pending_reviews[chat_id] = {
        "meeting_id": meeting_id,
        "meeting_key": review["meeting_key"],
        "queue": unknown_items,
        "current": None,
    }
    await bot.send_message(
        chat_id,
        "Я нашёл новые или неизвестные голоса. Сейчас пришлю короткие примеры, "
        "а ты просто ответь именем сообщением.\n\n"
        f"Встреча: <code>{_safe_html(meeting_id)}</code>\n\n"
        "Команды:\n"
        "/skipvoice — пропустить текущий голос\n"
        "/stopvoices — закончить разметку",
    )
    await _send_next_review_item(chat_id)


async def _maybe_start_speaker_review(chat_id: int, transcript: dict):
    meeting_id = transcript.get("meeting_id")
    if not meeting_id:
        return
    await _start_speaker_review(chat_id, str(meeting_id), quiet=True)


async def _send_next_review_item(chat_id: int):
    state = pending_reviews.get(chat_id)
    if not state:
        return

    if state.get("current") is None:
        queue = state.get("queue", [])
        if not queue:
            meeting_id = state.get("meeting_id")
            pending_reviews.pop(chat_id, None)
            await bot.send_message(chat_id, "Разметка новых голосов завершена.")
            if meeting_id:
                asyncio.create_task(_finalize_meeting_md(chat_id, str(meeting_id)))
            return
        state["current"] = queue.pop(0)

    current = state["current"]
    speaker_label = current["speaker_label"]
    sample_count = int(current.get("sample_count", 0))

    async with httpx.AsyncClient(timeout=120) as client:
        for sample_index in range(sample_count):
            try:
                response = await client.get(
                    f"{BOT_API}/meetings/{state['meeting_id']}/speaker-review/"
                    f"{state['meeting_key']}/{speaker_label}/samples/{sample_index}",
                    headers=_bot_api_headers(),
                )
                response.raise_for_status()
            except Exception as e:
                logger.warning(
                    "Could not fetch speaker sample %s/%s for %s: %s",
                    sample_index,
                    sample_count,
                    speaker_label,
                    e,
                )
                continue

            audio_file = BufferedInputFile(
                response.content,
                filename=f"{speaker_label}_{sample_index}.wav",
            )
            await bot.send_audio(
                chat_id,
                audio=audio_file,
                title=f"{speaker_label} sample {sample_index + 1}",
            )

    await bot.send_message(
        chat_id,
        "Кто это?\n"
        f"Текущая метка: <b>{_safe_html(current['current_name'])}</b>\n"
        f"Таймкоды: {_safe_html(_format_segment_preview(current.get('segments', [])))}\n\n"
        "Просто ответь именем одним сообщением.\n"
        'Если это тот же человек, напиши то же самое имя без слов вроде "тоже".',
    )


async def _send_transcript(chat_id: int, transcript: dict):
    """Отформатировать и отправить транскрипт."""
    segments = transcript.get("segments", [])
    if not segments:
        await bot.send_message(chat_id, "Транскрипт пуст — не было аудио.")
        return

    duration = transcript.get("duration_seconds")
    dur_str = ""
    if duration:
        m, s = divmod(int(duration), 60)
        dur_str = f"Длительность: {m}:{s:02d}\n"

    # Форматирование
    html_lines = []
    plain_lines = []
    current_speaker = None
    for seg in segments:
        speaker = str(seg.get("speaker") or "?")
        speaker_html = _safe_html(speaker)
        start = seg["start"]
        text_raw = str(seg.get("text", ""))
        text_html = _safe_html(text_raw)
        m, s = divmod(int(start), 60)
        ts = f"{m}:{s:02d}"

        if speaker != current_speaker:
            current_speaker = speaker
            html_lines.append(f"\n<b>{speaker_html}</b> [{ts}]:")
            plain_lines.append(f"\n{speaker} [{ts}]:")
        html_lines.append(text_html)
        plain_lines.append(text_raw)

    full_text_html = "\n".join(html_lines).strip()
    full_text_plain = "\n".join(plain_lines).strip()
    header = f"📝 <b>Транскрипт встречи</b>\n{dur_str}\n"

    message = header + full_text_html
    if len(message) <= 4096:
        await bot.send_message(chat_id, message)
    else:
        # Отправить короткую сводку + файл
        await bot.send_message(
            chat_id, header + f"Сегментов: {len(segments)}. Отправляю файлом..."
        )
        file = BufferedInputFile(
            full_text_plain.encode("utf-8"),
            filename="transcript.txt",
        )
        await bot.send_document(chat_id, file)

    # Сохранить MD на Google Drive
    drive_file = upload_transcript_md(transcript)
    if drive_file and drive_file.get("web_view_link"):
        safe_link = html.escape(drive_file["web_view_link"], quote=True)
        await bot.send_message(
            chat_id, f'📁 <a href="{safe_link}">Транскрипт на Google Drive</a>'
        )

    await _maybe_start_speaker_review(chat_id, transcript)


async def _finalize_meeting_md(chat_id: int, meeting_id: str) -> None:
    """После завершения review — перезаписать .md в Drive с актуальными именами."""
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            meeting_resp = await client.get(
                f"{BOT_API}/status/{meeting_id}",
                headers=_bot_api_headers(),
            )
            meeting_resp.raise_for_status()
            meeting_data = meeting_resp.json()
        except Exception as e:
            logger.warning("finalize_md: could not fetch meeting %s: %s", meeting_id, e)
            return

        drive = meeting_data.get("drive_file") or {}
        drive_file_id = str(drive.get("file_id") or "")
        if not drive_file_id:
            logger.info(
                "finalize_md: meeting=%s has no drive_file_id, skipping update",
                meeting_id,
            )
            await bot.send_message(
                chat_id,
                "Имена сохранил, но ссылки на .md в Drive нет — обновлять нечего.",
            )
            return

        try:
            transcript_resp = await client.get(
                f"{BOT_API}/transcripts/{meeting_id}",
                headers=_bot_api_headers(),
            )
            transcript_resp.raise_for_status()
            transcript = transcript_resp.json()
        except Exception as e:
            logger.warning(
                "finalize_md: could not fetch transcript for %s: %s", meeting_id, e
            )
            await bot.send_message(
                chat_id,
                f"Имена сохранил, но не смог получить свежий транскрипт: {_safe_html(e)}",
            )
            return

    source_filename = drive.get("filename") or None
    try:
        result = await asyncio.to_thread(
            update_transcript_md,
            drive_file_id,
            transcript,
            source_filename=source_filename,
        )
    except Exception as e:
        logger.exception("finalize_md: update_transcript_md failed: %s", e)
        await bot.send_message(
            chat_id,
            f"Имена сохранил, но не удалось обновить .md в Drive: {_safe_html(e)}",
        )
        return

    if not result:
        await bot.send_message(
            chat_id,
            "Имена сохранил, но обновить .md в Drive не получилось "
            "(см. логи бота).",
        )
        return

    link = result.get("web_view_link") or drive.get("web_view_link") or ""
    if link:
        safe_link = html.escape(link, quote=True)
        await bot.send_message(
            chat_id,
            f'✅ Транскрипт обновлён с правильными именами: '
            f'<a href="{safe_link}">открыть в Drive</a>',
        )
    else:
        await bot.send_message(
            chat_id, "✅ Транскрипт в Drive обновлён с правильными именами."
        )


async def _handle_trigger_review(request: web.Request) -> web.Response:
    """Internal HTTP hook — drive_watcher вызывает после обработки файла,
    чтобы бот автоматически инициировал review голосов в Telegram."""
    try:
        payload = await request.json()
        meeting_id = str(payload["meeting_id"])
        chat_id = int(payload["chat_id"])
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

    filename = str(payload.get("filename") or "")
    if filename:
        with suppress_exc():
            await bot.send_message(
                chat_id,
                f"Новая встреча обработана: <b>{_safe_html(filename)}</b>.\n"
                f"Начинаю разметку голосов...",
            )
    asyncio.create_task(_start_speaker_review(chat_id, meeting_id))
    logger.info(
        "Auto-review triggered: meeting=%s chat=%s filename=%s",
        meeting_id, chat_id, filename,
    )
    return web.json_response({"ok": True})


def suppress_exc():
    from contextlib import suppress
    return suppress(Exception)


async def _start_internal_http_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_post("/internal/trigger_review", _handle_trigger_review)
    runner = web.AppRunner(app)
    await runner.setup()
    host = os.getenv("INTERNAL_HTTP_HOST", "127.0.0.1")
    port = int(os.getenv("INTERNAL_HTTP_PORT", "8100"))
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("tg-bot internal HTTP server listening on %s:%d", host, port)
    return runner


async def main():
    logger.info("Starting Telegram bot...")
    runner = await _start_internal_http_server()
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
