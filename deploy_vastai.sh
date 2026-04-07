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

# ─── 1. Клонирование / обновление репо ──────────────────────────────────────
echo "=== [1/7] Клонирование репо ==="
if [ -d "$APP" ]; then
    echo "Репо уже существует, обновляю..."
    cd "$APP" && git pull --ff-only || true
else
    git clone "$REPO_URL" "$APP"
fi
cd "$APP"

# ─── 2. Системные пакеты ────────────────────────────────────────────────────
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

# ─── 3. Python-зависимости ───────────────────────────────────────────────────
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

# ─── 4. Playwright Chromium ──────────────────────────────────────────────────
echo "=== [4/7] Playwright Chromium ==="
playwright install chromium
playwright install-deps chromium 2>/dev/null || true
echo "Playwright Chromium установлен"

# ─── 5. Подготовка директорий ────────────────────────────────────────────────
echo "=== [5/7] Подготовка директорий ==="
mkdir -p "$RECORDINGS" "$LOGS"

# ─── 6. Xvfb + PulseAudio ───────────────────────────────────────────────────
echo "=== [6/7] Запуск Xvfb + PulseAudio ==="

# --- Xvfb ---
if ! pgrep -x Xvfb > /dev/null; then
    Xvfb :99 -screen 0 1920x1080x24 -ac &
    sleep 1
    echo "Xvfb запущен"
fi
export DISPLAY=:99

# --- PulseAudio (user mode, ref: screenappai/meeting-bot) ---
USER_ID=$(id -u)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/${USER_ID}}
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

pulseaudio --kill 2>/dev/null || true
sleep 1
pulseaudio -D --exit-idle-time=-1 --log-level=info 2>&1
sleep 5

if pgrep -x pulseaudio > /dev/null; then
    echo "PulseAudio запущен"
    # Null-sink — виртуальный динамик для захвата аудио
    SINK_ID=$(pactl load-module module-null-sink \
        sink_name=virtual_output \
        sink_properties=device.description="Virtual_Output" 2>&1)
    echo "Loaded null sink (ID: $SINK_ID)"

    pactl set-default-sink virtual_output 2>&1
    echo "Set virtual_output as default sink"

    if pactl list sources short | grep -q "virtual_output.monitor"; then
        echo "Monitor source virtual_output.monitor is available for ffmpeg"
    else
        echo "WARNING: virtual_output.monitor не найден!"
    fi
else
    echo "ERROR: PulseAudio не запустился"
    exit 1
fi

# ─── 7. Запуск сервисов ─────────────────────────────────────────────────────
echo "=== [7/7] Запуск сервисов ==="

# Остановить старые процессы если запущены
pkill -f "uvicorn.*app.main:app" 2>/dev/null || true
sleep 1

# --- Transcriber Service (порт 8001) ---
cd "$APP/transcriber-service"
HF_TOKEN="${HF_TOKEN:-}" \
nohup python -m uvicorn app.main:app \
    --host 0.0.0.0 --port 8001 \
    > "$LOGS/transcriber.log" 2>&1 &
TRANSCRIBER_PID=$!
echo "transcriber-service запущен на :8001 (PID $TRANSCRIBER_PID)"

# Ждём загрузки модели (large-v3 + diarization)
echo "Ожидание загрузки моделей transcriber..."
for i in $(seq 1 60); do
    if curl -s http://localhost:8001/health 2>/dev/null | grep -q '"pipeline_ready":true'; then
        echo "Transcriber готов!"
        break
    fi
    if ! kill -0 $TRANSCRIBER_PID 2>/dev/null; then
        echo "ERROR: transcriber-service упал! Логи:"
        tail -30 "$LOGS/transcriber.log"
        exit 1
    fi
    sleep 5
done

# --- Bot Service (порт 8000) ---
cd "$APP/bot-service"
TRANSCRIBER_URL=http://localhost:8001 \
DATABASE_URL="sqlite+aiosqlite:///$DB_PATH" \
RECORDINGS_DIR="$RECORDINGS" \
BOT_NAME="${BOT_NAME:-Транскрибатор}" \
DISPLAY=:99 \
XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
nohup python -m uvicorn app.main:app \
    --host 0.0.0.0 --port 8000 \
    > "$LOGS/bot.log" 2>&1 &
BOT_PID=$!
echo "bot-service запущен на :8000 (PID $BOT_PID)"

sleep 3

# Проверка
if ! kill -0 $BOT_PID 2>/dev/null; then
    echo "ERROR: bot-service упал! Логи:"
    tail -30 "$LOGS/bot.log"
    exit 1
fi

echo ""
echo "=== Деплой завершён ==="
echo "  Bot API:         http://localhost:8000/docs"
echo "  Transcriber API: http://localhost:8001/docs"
echo "  Логи:            $LOGS/"
echo "  БД SQLite:       $DB_PATH"
echo "  Записи:          $RECORDINGS/"
echo ""
echo "Проверка health:"
curl -s http://localhost:8001/health && echo ""
curl -s http://localhost:8000/health && echo ""
echo ""
echo "Пример запуска:"
echo '  curl -X POST http://localhost:8000/join -H "Content-Type: application/json" -d '"'"'{"meeting_url": "https://telemost.yandex.ru/j/XXXXXXXXXX"}'"'"
