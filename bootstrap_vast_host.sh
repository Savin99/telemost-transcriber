#!/usr/bin/env bash
# Подготовка нового Vast.ai хоста под telemost-transcriber.
# Запускать НА САМОМ СЕРВЕРЕ:
#   bash /workspace/telemost-transcriber/bootstrap_vast_host.sh
#
# Что делает:
# - ставит системные зависимости для bot-service
# - клонирует/обновляет репозиторий
# - ставит Python-зависимости bot/transcriber/tg-bot
# - ставит Google Chrome и Playwright Chromium
# - готовит директории /workspace/{logs,recordings,voice_bank}
#
# Что НЕ делает:
# - не прописывает секреты автоматически
# - не стартует сервисы
#
# После него:
# 1) заполнить /workspace/.bashrc нужными export
# 2) локально обновить .env.deploy на новый SSH
# 3) локально выполнить ./deploy.sh transcriber и ./deploy.sh all

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Savin99/telemost-transcriber.git}"
WORKDIR="${WORKDIR:-/workspace}"
APP_DIR="${APP_DIR:-$WORKDIR/telemost-transcriber}"
LOG_DIR="${LOG_DIR:-$WORKDIR/logs}"
RECORDINGS_DIR="${RECORDINGS_DIR:-$WORKDIR/recordings}"
VOICE_BANK_DIR="${VOICE_BANK_DIR:-$WORKDIR/voice_bank}"
PYTHON_BIN="${PYTHON_BIN:-/venv/main/bin/python}"
PIP_BIN="${PIP_BIN:-/venv/main/bin/pip}"
PLAYWRIGHT_BIN="${PLAYWRIGHT_BIN:-/venv/main/bin/playwright}"

if [ ! -x "$PYTHON_BIN" ] || [ ! -x "$PIP_BIN" ]; then
    echo "ERROR: ожидаю Python и pip в /venv/main/bin"
    echo "Сейчас: PYTHON_BIN=$PYTHON_BIN PIP_BIN=$PIP_BIN"
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "=== [1/6] Системные пакеты ==="
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    ca-certificates \
    dos2unix \
    ffmpeg \
    fonts-liberation \
    git \
    gnupg2 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libgl1-mesa-dri \
    libgl1-mesa-glx \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxkbcommon-x11-0 \
    libxrandr2 \
    libxss1 \
    pulseaudio \
    wget \
    xvfb

echo "=== [2/6] Google Chrome ==="
if ! command -v google-chrome >/dev/null 2>&1 && ! command -v google-chrome-stable >/dev/null 2>&1; then
    wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | \
        gpg --dearmor -o /usr/share/keyrings/google-linux-signing-key.gpg
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux-signing-key.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list
    apt-get update -qq
    apt-get install -y -qq google-chrome-stable
fi

echo "=== [3/6] Репозиторий ==="
mkdir -p "$WORKDIR"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch origin main
    git -C "$APP_DIR" checkout main
    git -C "$APP_DIR" pull --ff-only origin main
else
    git clone "$REPO_URL" "$APP_DIR"
fi

echo "=== [4/6] Python-зависимости ==="
"$PIP_BIN" install --quiet --disable-pip-version-check \
    torch torchaudio --index-url https://download.pytorch.org/whl/cu124
"$PIP_BIN" install --quiet --disable-pip-version-check \
    "whisperx @ git+https://github.com/m-bain/whisperX.git"
"$PIP_BIN" install --quiet --disable-pip-version-check \
    -r "$APP_DIR/bot-service/requirements.txt" \
    -r "$APP_DIR/transcriber-service/requirements.txt" \
    -r "$APP_DIR/tg-bot/requirements.txt"

echo "=== [5/6] Playwright Chromium ==="
"$PLAYWRIGHT_BIN" install chromium

echo "=== [6/6] Директории ==="
mkdir -p "$LOG_DIR" "$RECORDINGS_DIR" "$VOICE_BANK_DIR"

cat <<'EOF'

Готово.

Теперь проверь, что в /workspace/.bashrc есть хотя бы:

export TELEMOST_SERVICE_API_KEY=supersecret-change-me
export HF_TOKEN=hf_xxx
export BOT_NAME="Транскрибатор"
export GDRIVE_FOLDER_ID=...
export GDRIVE_CLIENT_SECRET=/workspace/credentials/client_secret.json
export GDRIVE_TOKEN_PATH=/workspace/credentials/gdrive_token.json

Опционально:
export ANTHROPIC_API_KEY=...
export MEETING_METADATA_LLM_ENABLED=false

Дальше на локальной машине:
1. поменяй .env.deploy на новый SSH
2. выполни ./deploy.sh transcriber
3. выполни ./deploy.sh all

EOF
