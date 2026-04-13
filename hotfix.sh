#!/usr/bin/env bash
# hotfix.sh — мгновенный деплой через rsync (без git)
# Синкает файлы напрямую + рестартит сервис. Для быстрых итераций.
#
# Использование:
#   ./hotfix.sh tg           — синк tg-bot + рестарт
#   ./hotfix.sh bot          — синк bot-service + рестарт
#   ./hotfix.sh transcriber  — синк transcriber-service + рестарт
#   ./hotfix.sh all          — синк всего + рестарт tg + bot
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- Загрузка SSH-конфига ---
ENV_FILE="$SCRIPT_DIR/.env.deploy"
if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
fi

if [ -z "${VAST_SSH:-}" ]; then
    echo "❌ VAST_SSH не задан! См. deploy.sh для настройки."
    exit 1
fi

# Извлекаем параметры SSH для rsync
# VAST_SSH="ssh -p 12345 root@1.2.3.4" → SSH_OPTS="-e 'ssh -p 12345'" REMOTE_HOST="root@1.2.3.4"
SSH_PORT=$(echo "$VAST_SSH" | grep -oP '(?<=-p\s)\d+' || echo "22")
REMOTE_HOST=$(echo "$VAST_SSH" | grep -oP '\S+@\S+$')
RSYNC_SSH="ssh -p $SSH_PORT"

REMOTE_APP="/workspace/telemost-transcriber"
REMOTE_LOGS="/workspace/logs"
TARGET="${1:-all}"

# --- Цвета ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}▶ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }

RSYNC_OPTS="-avz --delete --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' --exclude='recordings/' --exclude='.git'"

sync_and_restart_tg() {
    log "Синк tg-bot..."
    rsync $RSYNC_OPTS -e "$RSYNC_SSH" "$SCRIPT_DIR/tg-bot/" "$REMOTE_HOST:$REMOTE_APP/tg-bot/"
    log "Рестарт tg-bot..."
    $VAST_SSH "source /workspace/.bashrc 2>/dev/null || true; if [ -z \"\${TG_BOT_TOKEN:-}\" ]; then echo 'tg-bot skipped: TG_BOT_TOKEN is not set'; else pkill -f '[p]ython bot.py' 2>/dev/null || true; sleep 1; cd $REMOTE_APP/tg-bot && TG_BOT_TOKEN=\$TG_BOT_TOKEN BOT_API_URL=http://localhost:8000 nohup python bot.py > $REMOTE_LOGS/tg-bot.log 2>&1 < /dev/null & fi" 2>/dev/null
    log "tg-bot обновлён"
}

sync_and_restart_bot() {
    log "Синк bot-service..."
    rsync $RSYNC_OPTS -e "$RSYNC_SSH" "$SCRIPT_DIR/bot-service/" "$REMOTE_HOST:$REMOTE_APP/bot-service/"
    log "Рестарт bot-service..."
    $VAST_SSH "source /workspace/.bashrc 2>/dev/null || true; pkill -f '[u]vicorn app.main:app.*8000' 2>/dev/null || true; sleep 1; cd $REMOTE_APP/bot-service && TRANSCRIBER_URL=http://localhost:8001 DATABASE_URL=\"sqlite+aiosqlite:///\/workspace/transcriber.db\" RECORDINGS_DIR=/workspace/recordings BOT_NAME=\"\${BOT_NAME:-Транскрибатор}\" DISPLAY=:99 nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > $REMOTE_LOGS/bot.log 2>&1 < /dev/null &" 2>/dev/null
    log "bot-service обновлён"
}

sync_and_restart_transcriber() {
    log "Синк transcriber-service..."
    rsync $RSYNC_OPTS -e "$RSYNC_SSH" "$SCRIPT_DIR/transcriber-service/" "$REMOTE_HOST:$REMOTE_APP/transcriber-service/"
    log "Рестарт transcriber-service..."
    $VAST_SSH "source /workspace/.bashrc 2>/dev/null || true; mkdir -p /workspace/voice_bank; pkill -f '[u]vicorn app.main:app.*8001' 2>/dev/null || true; sleep 1; cd $REMOTE_APP/transcriber-service && HF_TOKEN=\${HF_TOKEN:-} DIARIZATION_MODEL=\${DIARIZATION_MODEL:-pyannote/speaker-diarization-community-1} CLUSTERING_THRESHOLD=\${CLUSTERING_THRESHOLD:-0.35} CLUSTERING_FA=\${CLUSTERING_FA:-0.04} CLUSTERING_FB=\${CLUSTERING_FB:-0.9} VOICE_BANK_DIR=\${VOICE_BANK_DIR:-/workspace/voice_bank} VOICE_MATCH_THRESHOLD=\${VOICE_MATCH_THRESHOLD:-0.40} MIN_EMBEDDING_SEGMENT_SEC=\${MIN_EMBEDDING_SEGMENT_SEC:-1.0} nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 > $REMOTE_LOGS/transcriber.log 2>&1 < /dev/null &" 2>/dev/null
    warn "transcriber-service обновлён (модель грузится ~2 мин, + первая загрузка community-1!)"
}

case "$TARGET" in
    tg)           sync_and_restart_tg ;;
    bot)          sync_and_restart_bot ;;
    transcriber)  sync_and_restart_transcriber ;;
    all)
        sync_and_restart_tg
        sync_and_restart_bot
        warn "transcriber НЕ перезапущен. Для рестарта: ./hotfix.sh transcriber"
        ;;
    *)
        echo "Использование: ./hotfix.sh [tg|bot|transcriber|all]"
        exit 1
        ;;
esac

sleep 2
log "Health check..."
$VAST_SSH "curl -s http://localhost:8000/health 2>/dev/null || echo 'bot: down'; curl -s http://localhost:8001/health 2>/dev/null || echo 'transcriber: down'" 2>/dev/null
log "Готово!"
