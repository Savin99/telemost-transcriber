"""Telegram-бот для транскрипции встреч Телемоста.

Работает в группах и в ЛС:
- Автоматически ловит ссылки telemost.yandex.ru/j/... в чате
- /rec <ссылка> — подключиться к встрече
- /stop — остановить запись и получить транскрипт
- /status — статус текущей записи
"""

import asyncio
import logging
import os
import re

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from gdrive import upload_transcript_md

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TG_TOKEN = os.environ["TG_BOT_TOKEN"]
BOT_API = os.getenv("BOT_API_URL", "http://localhost:8000")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))

bot = Bot(token=TG_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Regex для ссылок Телемоста
TELEMOST_RE = re.compile(r"https?://telemost\.yandex\.ru/j/\d+")

# Активные сессии: chat_id → {"meeting_id": str, "url": str}
active: dict[int, dict] = {}


@dp.message(Command("start", "help"))
async def cmd_start(msg: Message):
    await msg.answer(
        "Я транскрибирую встречи Яндекс Телемоста.\n\n"
        "<b>Как использовать:</b>\n"
        "1. Кинь ссылку на Телемост — я подключусь автоматически\n"
        "2. Или: <code>/rec https://telemost.yandex.ru/j/...</code>\n\n"
        "<b>Команды:</b>\n"
        "/stop — остановить запись и получить транскрипт\n"
        "/status — статус текущей записи\n\n"
        "Работаю и в группах — просто добавь меня и кидайте ссылки."
    )


@dp.message(Command("rec"))
async def cmd_rec(msg: Message, command: CommandObject):
    """Подключиться к встрече по команде /rec <url>."""
    text = command.args or ""
    match = TELEMOST_RE.search(text)
    if not match:
        await msg.answer("Укажи ссылку: <code>/rec https://telemost.yandex.ru/j/...</code>")
        return
    await _join_meeting(msg, match.group(0))


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
            resp = await client.post(f"{BOT_API}/leave/{meeting_id}")
            resp.raise_for_status()
        except Exception as e:
            await status_msg.edit_text(f"Ошибка: {e}")
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
            resp = await client.get(f"{BOT_API}/status/{session['meeting_id']}")
            data = resp.json()
            status = data["status"]
            duration = data.get("duration_seconds")
            dur_str = ""
            if duration:
                m, s = divmod(int(duration), 60)
                dur_str = f" ({m}:{s:02d})"
            await msg.answer(f"Статус: <b>{status}</b>{dur_str}")
        except Exception as e:
            await msg.answer(f"Ошибка: {e}")


@dp.message(F.text.regexp(TELEMOST_RE))
async def handle_meeting_url(msg: Message):
    """Автоматический перехват ссылок Телемоста в чате."""
    url = TELEMOST_RE.search(msg.text).group(0)
    await _join_meeting(msg, url)


async def _join_meeting(msg: Message, url: str):
    """Подключить бота к встрече."""
    chat_id = msg.chat.id

    if chat_id in active:
        await msg.answer(
            "Уже идёт запись. Сначала /stop"
        )
        return

    status_msg = await msg.answer(f"Подключаюсь к встрече...")

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{BOT_API}/join",
                json={"meeting_url": url},
            )
            resp.raise_for_status()
            data = resp.json()
            meeting_id = data["meeting_id"]
        except Exception as e:
            await status_msg.edit_text(f"Не удалось подключиться: {e}")
            return

    active[chat_id] = {"meeting_id": meeting_id, "url": url}

    await status_msg.edit_text(
        f"Бот подключается к встрече.\n\n"
        f"Когда закончите — отправьте /stop\n"
        f"Или бот завершит автоматически, когда встреча закончится."
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
                resp = await client.get(f"{BOT_API}/status/{meeting_id}")
                data = resp.json()
                status = data["status"]
            except Exception:
                continue

            if status == "done":
                active.pop(chat_id, None)
                try:
                    resp = await client.get(f"{BOT_API}/transcripts/{meeting_id}")
                    await _send_transcript(chat_id, resp.json())
                except Exception as e:
                    await bot.send_message(chat_id, f"Транскрипт готов, но ошибка: {e}")
                return

            if status == "error":
                active.pop(chat_id, None)
                error_msg = data.get("error_message", "Неизвестная ошибка")
                await bot.send_message(chat_id, f"Ошибка записи: {error_msg}")
                return


async def _wait_and_get_transcript(meeting_id: str) -> dict | None:
    """Поллить статус до готовности транскрипта."""
    async with httpx.AsyncClient(timeout=600) as client:
        for _ in range(120):  # Макс ~20 минут
            await asyncio.sleep(POLL_INTERVAL)
            try:
                resp = await client.get(f"{BOT_API}/status/{meeting_id}")
                data = resp.json()
            except Exception:
                continue

            if data["status"] == "done":
                resp = await client.get(f"{BOT_API}/transcripts/{meeting_id}")
                return resp.json()

            if data["status"] == "error":
                return None

    return None


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
    lines = []
    current_speaker = None
    for seg in segments:
        speaker = seg.get("speaker") or "?"
        start = seg["start"]
        text = seg["text"]
        m, s = divmod(int(start), 60)
        ts = f"{m}:{s:02d}"

        if speaker != current_speaker:
            current_speaker = speaker
            lines.append(f"\n<b>{speaker}</b> [{ts}]:")
        lines.append(text)

    full_text = "\n".join(lines).strip()
    header = f"📝 <b>Транскрипт встречи</b>\n{dur_str}\n"

    message = header + full_text
    if len(message) <= 4096:
        await bot.send_message(chat_id, message)
    else:
        # Отправить короткую сводку + файл
        await bot.send_message(chat_id, header + f"Сегментов: {len(segments)}. Отправляю файлом...")
        # Убираем HTML-теги для txt-файла
        plain = full_text.replace("<b>", "").replace("</b>", "")
        file = BufferedInputFile(
            plain.encode("utf-8"),
            filename="transcript.txt",
        )
        await bot.send_document(chat_id, file)

    # Сохранить MD на Google Drive
    gdrive_link = upload_transcript_md(transcript)
    if gdrive_link:
        await bot.send_message(chat_id, f"📁 <a href=\"{gdrive_link}\">Транскрипт на Google Drive</a>")


async def main():
    logger.info("Starting Telegram bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
