#!/usr/bin/env bash

echo "[DEPRECATED] Этот скрипт не работает с VDS 193.233.87.211 + Modal." >&2
echo "Используй: ./deploy_vds.sh (см. ./deploy_vds.sh --help)" >&2
exit 1

# restart_service.sh — рестарт сервисов на Vast.ai сервере
# Копируется на сервер через deploy.sh/hotfix.sh и запускается там.
#
# Использование:
#   bash restart_service.sh tg           — рестарт tg-bot
#   bash restart_service.sh watcher      — рестарт drive-watcher
#   bash restart_service.sh bot          — рестарт bot-service
#   bash restart_service.sh transcriber  — рестарт transcriber-service
#   bash restart_service.sh all          — рестарт всех 4 сервисов (включая transcriber, +2 мин прогрев)
#   bash restart_service.sh health       — только проверка health
set -euo pipefail

# --- Константы ---
APP_DIR="/workspace/telemost-transcriber"
LOG_DIR="/workspace/logs"
DEPS_CACHE_DIR="/workspace/.deps_cache"
PIP="/venv/main/bin/pip"
PYTHON="/venv/main/bin/python"

# --- Загрузка env из .bashrc ---
source /workspace/.bashrc 2>/dev/null || true

mkdir -p "$DEPS_CACHE_DIR"

# --- Хеш-кеш для pip install ---
# install_if_changed <service_name> <hash_input_files...> -- <pip_command>
# Пропускает переустановку зависимостей, если хеш входных файлов не изменился.
install_if_changed() {
    local service="$1"
    shift
    local files=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
        files+=("$1")
        shift
    done
    shift  # съедаем "--"
    local marker="$DEPS_CACHE_DIR/$service.hash"
    local current_hash
    current_hash="$(cat "${files[@]}" 2>/dev/null | md5sum | awk '{print $1}')"
    if [ -f "$marker" ] && [ "$(cat "$marker")" = "$current_hash" ]; then
        log "$service: зависимости не изменились, пропускаю pip install"
        return 0
    fi
    log "$service: обновляю зависимости..."
    if "$@"; then
        echo "$current_hash" > "$marker"
    else
        err "$service: pip install упал"
        return 1
    fi
}

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
    install_if_changed "tg-bot" requirements.txt -- \
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
    pkill -f '^/venv/main/bin/python drive_watcher\.py$' 2>/dev/null || true
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
    install_if_changed "bot-service" requirements.txt -- \
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
    MEETING_METADATA_ADVISOR_MODEL="${MEETING_METADATA_ADVISOR_MODEL:-claude-opus-4-7}" \
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
    install_if_changed "transcriber-service" requirements.txt "$APP_DIR/remote/restart_service.sh" -- \
        bash -c '
            set -e
            '"$PIP"' install --quiet --disable-pip-version-check \
                torch torchaudio --index-url https://download.pytorch.org/whl/cu124
            '"$PIP"' install --quiet --disable-pip-version-check \
                "whisperx @ git+https://github.com/m-bain/whisperX.git@v3.8.5"
            '"$PIP"' install --quiet --disable-pip-version-check -r requirements.txt
        '

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
    SPEAKER_LLM_ADVISOR_MODEL="${SPEAKER_LLM_ADVISOR_MODEL:-claude-opus-4-7}" \
    SPEAKER_LLM_ADVISOR_ENABLED="${SPEAKER_LLM_ADVISOR_ENABLED:-true}" \
    TRANSCRIPT_LLM_REFINEMENT_ENABLED="${TRANSCRIPT_LLM_REFINEMENT_ENABLED:-true}" \
    TRANSCRIPT_LLM_EXECUTOR_MODEL="${TRANSCRIPT_LLM_EXECUTOR_MODEL:-claude-sonnet-4-6}" \
    TRANSCRIPT_LLM_ADVISOR_MODEL="${TRANSCRIPT_LLM_ADVISOR_MODEL:-claude-opus-4-7}" \
    TRANSCRIPT_LLM_ADVISOR_ENABLED="${TRANSCRIPT_LLM_ADVISOR_ENABLED:-true}" \
    nohup $PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8001 \
        > "$LOG_DIR/transcriber.log" 2>&1 < /dev/null &

    log "transcriber-service PID: $!"
}

check_bot_health() {
    curl -sf -H "X-API-Key: ${TELEMOST_SERVICE_API_KEY:-}" \
        http://localhost:8000/health >/dev/null 2>&1
}

check_transcriber_health() {
    curl -sf http://localhost:8001/health >/dev/null 2>&1
}

wait_for_transcriber() {
    local max_wait="${1:-180}"
    local elapsed=0
    local interval=15
    while [ "$elapsed" -lt "$max_wait" ]; do
        if check_transcriber_health; then
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    return 1
}

health_check() {
    log "Health check..."
    if [ -n "${TELEMOST_SERVICE_API_KEY:-}" ]; then
        if check_bot_health; then
            log "bot-service: ok"
        else
            warn "bot-service: не отвечает"
        fi
    else
        warn "bot-service: TELEMOST_SERVICE_API_KEY is not set"
    fi
    if check_transcriber_health; then
        log "transcriber: ok"
    else
        warn "transcriber: не отвечает (может всё ещё грузить модели)"
    fi
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
        warn "transcriber перезапускается — модель грузится ~2 мин, health временно красный"
        restart_transcriber
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
if [ "$TARGET" = "transcriber" ] || [ "$TARGET" = "all" ]; then
    log "Жду прогрева transcriber (до 3 минут)..."
    if wait_for_transcriber 180; then
        log "transcriber прогрет"
    else
        warn "transcriber не поднялся за 3 минуты — проверь /workspace/logs/transcriber.log"
    fi
fi
health_check
log "Готово!"
