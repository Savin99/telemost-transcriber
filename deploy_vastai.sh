#!/usr/bin/env bash
# deploy_vastai.sh — деплой telemost-transcriber на Vast.ai (без Docker)
# Окружение: PyTorch + CUDA + Python уже есть, venv активен в /venv/main
# Запуск: bash /workspace/deploy_vastai.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/monsterscorp/telemost-transcriber.git}"
WORK=/workspace
APP=$WORK/telemost-transcriber
DB_PATH=$WORK/transcriber.db
RECORDINGS=$WORK/recordings
LOGS=$WORK/logs

echo "=== [1/7] Клонирование репо ==="
if [ -d "$APP" ]; then
    echo "Репо уже существует, обновляю..."
    cd "$APP" && git pull --ff-only || true
else
    git clone "$REPO_URL" "$APP"
fi
cd "$APP"

echo "=== [2/7] Системные пакеты: Xvfb, PulseAudio, FFmpeg, зависимости Chrome ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    xvfb \
    pulseaudio \
    ffmpeg \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libgtk-3-0 \
    libxshmfence1 \
    libglu1-mesa \
    fonts-liberation \
    xdg-utils \
    wget \
    ca-certificates \
    > /dev/null 2>&1
echo "Системные пакеты установлены"

echo "=== [3/7] Python-зависимости ==="
pip install -q --no-cache-dir \
    whisperx \
    faster-whisper \
    pyannote.audio \
    fastapi==0.115.* \
    "uvicorn[standard]==0.34.*" \
    playwright==1.52.* \
    "sqlalchemy[asyncio]==2.0.*" \
    aiosqlite \
    httpx==0.28.* \
    pydantic==2.11.* \
    aiofiles
echo "Python-зависимости установлены"

echo "=== [4/7] Playwright Chromium ==="
playwright install chromium
playwright install-deps chromium 2>/dev/null || true
echo "Playwright Chromium установлен"

echo "=== [5/7] Патч: PostgreSQL → SQLite ==="
# --- database.py: заменяем движок, типы и server_default ---
cat > "$APP/bot-service/app/database.py" << 'PYEOF'
import os
import uuid as _uuid
from uuid import UUID

from sqlalchemy import Column, Float, String, Text, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:////workspace/transcriber.db",
)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _generate_uuid():
    return str(_uuid.uuid4())


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(String(36), primary_key=True, default=_generate_uuid)
    meeting_url = Column(Text, nullable=False)
    bot_name = Column(Text, nullable=False, default="Транскрибатор")
    status = Column(String(50), nullable=False, default="joining")
    recording_path = Column(Text)
    duration_seconds = Column(Float)
    error_message = Column(Text)
    created_at = Column(Text)  # ISO-строка, заполняется триггером
    updated_at = Column(Text)


class TranscriptSegmentDB(Base):
    __tablename__ = "transcript_segments"

    id = Column(String(36), primary_key=True, default=_generate_uuid)
    meeting_id = Column(String(36), nullable=False)
    speaker = Column(Text)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    text = Column(Text, nullable=False)


@event.listens_for(Meeting, "init")
def _meeting_set_timestamps(target, args, kwargs):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    if target.created_at is None:
        target.created_at = now
    if target.updated_at is None:
        target.updated_at = now


async def init_db():
    """Создать таблицы если не существуют."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


async def update_meeting_status(
    session: AsyncSession,
    meeting_id,
    status: str,
    **kwargs,
):
    from datetime import datetime, timezone
    meeting_id_str = str(meeting_id)
    meeting = await session.get(Meeting, meeting_id_str)
    if meeting:
        meeting.status = status
        meeting.updated_at = datetime.now(timezone.utc).isoformat()
        for key, value in kwargs.items():
            setattr(meeting, key, value)
        await session.commit()
PYEOF

# --- bot-service/app/main.py: добавить init_db() в lifespan и работать со строковыми id ---
sed -i 's/from .database import (/from .database import (\n    init_db,/' "$APP/bot-service/app/main.py"

# Добавить вызов init_db() в lifespan
sed -i '/os.makedirs(RECORDINGS_DIR, exist_ok=True)/a\    await init_db()' "$APP/bot-service/app/main.py"

# Поменять RECORDINGS_DIR на /workspace/recordings
sed -i 's|RECORDINGS_DIR = "/app/recordings"|RECORDINGS_DIR = "/workspace/recordings"|' "$APP/bot-service/app/main.py"

# --- bot-service/app/models.py: UUID → str для meeting_id ---
sed -i 's/from uuid import UUID//;s/meeting_id: UUID/meeting_id: str/' "$APP/bot-service/app/models.py"
# Убрать import datetime если не нужен
sed -i 's/created_at: datetime/created_at: str | None = None/' "$APP/bot-service/app/models.py"

echo "Патч SQLite применён"

echo "=== [6/7] Подготовка директорий ==="
mkdir -p "$RECORDINGS" "$LOGS"

echo "=== [7/7] Запуск сервисов ==="

# --- Xvfb ---
if ! pgrep -x Xvfb > /dev/null; then
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
    echo "Xvfb запущен"
fi
export DISPLAY=:99

# --- PulseAudio с виртуальным sink ---
if ! pgrep -x pulseaudio > /dev/null; then
    pulseaudio --start --exit-idle-time=-1 2>/dev/null || true
    # Создаём виртуальный аудио-выход для захвата
    pactl load-module module-null-sink sink_name=virtual_output sink_properties=device.description="VirtualOutput" 2>/dev/null || true
    pactl set-default-sink virtual_output 2>/dev/null || true
    echo "PulseAudio запущен с виртуальным sink"
fi

# Остановить старые процессы uvicorn если запущены
pkill -f "uvicorn.*transcriber-service" 2>/dev/null || true
pkill -f "uvicorn.*bot-service" 2>/dev/null || true
sleep 1

# --- Transcriber Service (порт 8001) ---
cd "$APP/transcriber-service"
nohup python -m uvicorn app.main:app \
    --host 0.0.0.0 --port 8001 \
    > "$LOGS/transcriber.log" 2>&1 &
echo "transcriber-service запущен на :8001 (PID $!)"

# --- Bot Service (порт 8000) ---
cd "$APP/bot-service"
export TRANSCRIBER_URL=http://localhost:8001
export DATABASE_URL="sqlite+aiosqlite:////workspace/transcriber.db"
export BOT_NAME="${BOT_NAME:-Транскрибатор}"

nohup python -m uvicorn app.main:app \
    --host 0.0.0.0 --port 8000 \
    > "$LOGS/bot.log" 2>&1 &
echo "bot-service запущен на :8000 (PID $!)"

sleep 3
echo ""
echo "=== Деплой завершён ==="
echo "  Bot API:         http://localhost:8000/docs"
echo "  Transcriber API: http://localhost:8001/docs"
echo "  Логи:            $LOGS/"
echo "  БД SQLite:       $DB_PATH"
echo "  Записи:          $RECORDINGS/"
echo ""
echo "Проверка health:"
curl -s http://localhost:8001/health 2>/dev/null && echo "" || echo "  transcriber ещё стартует (модели грузятся)..."
curl -s http://localhost:8000/health 2>/dev/null && echo "" || echo "  bot ещё стартует..."
