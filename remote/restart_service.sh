#!/usr/bin/env bash
# restart_service.sh — рестарт сервисов на Vast.ai сервере
# Копируется на сервер через deploy.sh/hotfix.sh и запускается там.
#
# Использование:
#   bash restart_service.sh tg           — рестарт tg-bot
#   bash restart_service.sh watcher      — рестарт drive-watcher
#   bash restart_service.sh bot          — рестарт bot-service
#   bash restart_service.sh transcriber  — рестарт transcriber-service
#   bash restart_service.sh all          — рестарт tg + watcher + bot (без transcriber)
#   bash restart_service.sh health       — только проверка health
set -euo pipefail

# --- Константы ---
APP_DIR="/workspace/telemost-transcriber"
LOG_DIR="/workspace/logs"
PIP="/venv/main/bin/pip"
PYTHON="/venv/main/bin/python"

# --- Загрузка env из .bashrc ---
source /workspace/.bashrc 2>/dev/null || true

# --- Цвета ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}▶ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
err()  { echo -e "${RED}✖ $1${NC}"; }

TARGET="${1:-all}"

# --- Создание необходимых директорий ---
mkdir -p "$LOG_DIR" /workspace/recordings /workspace/voice_bank

# --- Функции рестарта ---

restart_tg() {
    if [ -z "${TG_BOT_TOKEN:-}" ]; then
        warn "tg-bot skipped: TG_BOT_TOKEN is not set"
        return
    fi

    log "Рестарт tg-bot..."
    pkill -f '^/venv/main/bin/python bot.py$' 2>/dev/null || true
    sleep 1

    cd "$APP_DIR/tg-bot"
    $PIP install --quiet --disable-pip-version-check -r requirements.txt

    TG_BOT_TOKEN="$TG_BOT_TOKEN" \
    BOT_API_URL="http://localhost:8000" \
    nohup $PYTHON bot.py > "$LOG_DIR/tg-bot.log" 2>&1 < /dev/null &

    log "tg-bot PID: $!"
}

restart_watcher() {
    if [ -z "${GDRIVE_FOLDER_ID:-}" ]; then
        warn "drive-watcher skipped: GDRIVE_FOLDER_ID is not set"
        return
    fi

    log "Рестарт drive-watcher..."
    pkill -f 'python drive_watcher.py' 2>/dev/null || true
    sleep 1

    cd "$APP_DIR/tg-bot"

    TRANSCRIBER_URL="http://localhost:8001" \
    GDRIVE_FOLDER_ID="$GDRIVE_FOLDER_ID" \
    GDRIVE_CLIENT_SECRET="${GDRIVE_CLIENT_SECRET:-}" \
    GDRIVE_TOKEN_PATH="${GDRIVE_TOKEN_PATH:-}" \
    DRIVE_POLL_INTERVAL="${DRIVE_POLL_INTERVAL:-30}" \
    nohup $PYTHON drive_watcher.py > "$LOG_DIR/drive-watcher.log" 2>&1 < /dev/null &

    log "drive-watcher PID: $!"
}

restart_bot() {
    if [ -z "${TELEMOST_SERVICE_API_KEY:-}" ]; then
        err "bot-service: TELEMOST_SERVICE_API_KEY is not set"
        exit 1
    fi

    log "Рестарт bot-service..."
    fuser -k 8000/tcp 2>/dev/null || true
    sleep 1

    cd "$APP_DIR/bot-service"
    $PIP install --quiet --disable-pip-version-check -r requirements.txt

    TRANSCRIBER_URL="http://localhost:8001" \
    DATABASE_URL="sqlite+aiosqlite:////workspace/transcriber.db" \
    RECORDINGS_DIR="/workspace/recordings" \
    BOT_NAME="${BOT_NAME:-Транскрибатор}" \
    TELEMOST_SERVICE_API_KEY="$TELEMOST_SERVICE_API_KEY" \
    GDRIVE_FOLDER_ID="${GDRIVE_FOLDER_ID:-}" \
    GDRIVE_CLIENT_SECRET="${GDRIVE_CLIENT_SECRET:-}" \
    GDRIVE_TOKEN_PATH="${GDRIVE_TOKEN_PATH:-}" \
    MEETING_METADATA_LLM_ENABLED="${MEETING_METADATA_LLM_ENABLED:-false}" \
    ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    MEETING_METADATA_RULES_JSON="${MEETING_METADATA_RULES_JSON:-}" \
    MEETING_METADATA_RULES_PATH="${MEETING_METADATA_RULES_PATH:-}" \
    MEETING_METADATA_EXECUTOR_MODEL="${MEETING_METADATA_EXECUTOR_MODEL:-claude-sonnet-4-6}" \
    MEETING_METADATA_ADVISOR_MODEL="${MEETING_METADATA_ADVISOR_MODEL:-claude-opus-4-6}" \
    MEETING_METADATA_ADVISOR_ENABLED="${MEETING_METADATA_ADVISOR_ENABLED:-true}" \
    MEETING_METADATA_ADVISOR_MAX_USES="${MEETING_METADATA_ADVISOR_MAX_USES:-2}" \
    MEETING_METADATA_TIMEOUT_SEC="${MEETING_METADATA_TIMEOUT_SEC:-120}" \
    MEETING_METADATA_MAX_TOKENS="${MEETING_METADATA_MAX_TOKENS:-1024}" \
    DISPLAY=":99" \
    nohup $PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
        > "$LOG_DIR/bot.log" 2>&1 < /dev/null &

    log "bot-service PID: $!"
}

restart_transcriber() {
    log "Рестарт transcriber-service..."
    fuser -k 8001/tcp 2>/dev/null || true
    sleep 1

    cd "$APP_DIR/transcriber-service"
    $PIP install --quiet --disable-pip-version-check \
        torch torchaudio --index-url https://download.pytorch.org/whl/cu124
    $PIP install --quiet --disable-pip-version-check \
        "whisperx @ git+https://github.com/m-bain/whisperX.git"
    $PIP install --quiet --disable-pip-version-check -r requirements.txt

    HF_TOKEN="${HF_TOKEN:-}" \
    ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    DIARIZATION_MODEL="${DIARIZATION_MODEL:-pyannote/speaker-diarization-community-1}" \
    ASR_LANGUAGE="${ASR_LANGUAGE:-ru}" \
    CLUSTERING_THRESHOLD="${CLUSTERING_THRESHOLD:-0.35}" \
    CLUSTERING_FA="${CLUSTERING_FA:-0.04}" \
    CLUSTERING_FB="${CLUSTERING_FB:-0.9}" \
    VOICE_BANK_DIR="${VOICE_BANK_DIR:-/workspace/voice_bank}" \
    VOICE_MATCH_THRESHOLD="${VOICE_MATCH_THRESHOLD:-0.40}" \
    MIN_EMBEDDING_SEGMENT_SEC="${MIN_EMBEDDING_SEGMENT_SEC:-1.0}" \
    SPEAKER_LLM_REFINEMENT_ENABLED="${SPEAKER_LLM_REFINEMENT_ENABLED:-false}" \
    SPEAKER_LLM_EXECUTOR_MODEL="${SPEAKER_LLM_EXECUTOR_MODEL:-claude-sonnet-4-6}" \
    SPEAKER_LLM_ADVISOR_MODEL="${SPEAKER_LLM_ADVISOR_MODEL:-claude-opus-4-6}" \
    SPEAKER_LLM_ADVISOR_ENABLED="${SPEAKER_LLM_ADVISOR_ENABLED:-true}" \
    TRANSCRIPT_LLM_REFINEMENT_ENABLED="${TRANSCRIPT_LLM_REFINEMENT_ENABLED:-true}" \
    TRANSCRIPT_LLM_EXECUTOR_MODEL="${TRANSCRIPT_LLM_EXECUTOR_MODEL:-claude-sonnet-4-6}" \
    TRANSCRIPT_LLM_ADVISOR_MODEL="${TRANSCRIPT_LLM_ADVISOR_MODEL:-claude-opus-4-6}" \
    TRANSCRIPT_LLM_ADVISOR_ENABLED="${TRANSCRIPT_LLM_ADVISOR_ENABLED:-true}" \
    nohup $PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8001 \
        > "$LOG_DIR/transcriber.log" 2>&1 < /dev/null &

    log "transcriber-service PID: $!"
}

health_check() {
    log "Health check..."
    if [ -n "${TELEMOST_SERVICE_API_KEY:-}" ]; then
        curl -s -H "X-API-Key: ${TELEMOST_SERVICE_API_KEY}" \
            http://localhost:8000/health 2>/dev/null && echo '' \
            || warn "bot-service: не отвечает"
    else
        warn "bot-service: TELEMOST_SERVICE_API_KEY is not set"
    fi
    curl -s http://localhost:8001/health 2>/dev/null && echo '' \
        || warn "transcriber: не отвечает"
}

# --- Диспетчер ---
case "$TARGET" in
    tg)           restart_tg ;;
    watcher)      restart_watcher ;;
    bot)          restart_bot ;;
    transcriber)  restart_transcriber ;;
    all)
        restart_tg
        restart_watcher
        restart_bot
        warn "transcriber НЕ перезапущен (модель грузится ~2 мин). Для рестарта: bash restart_service.sh transcriber"
        ;;
    health)
        health_check
        exit 0
        ;;
    *)
        err "Неизвестный сервис: $TARGET"
        echo "Доступные: tg, watcher, bot, transcriber, all, health"
        exit 1
        ;;
esac

sleep 2
health_check
log "Готово!"
