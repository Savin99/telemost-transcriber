#!/usr/bin/env bash
# hotfix.sh — мгновенный деплой через rsync (без git)
# Синкает файлы напрямую + рестартит сервис. Для быстрых итераций.
#
# Использование:
#   ./hotfix.sh tg           — синк tg-bot + рестарт
#   ./hotfix.sh bot          — синк bot-service + рестарт
#   ./hotfix.sh transcriber  — синк transcriber-service + рестарт
#   ./hotfix.sh all          — синк всего + рестарт tg + watcher + bot
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

# Извлекаем параметры SSH для rsync/scp
SSH_PORT=$(printf '%s\n' "$VAST_SSH" | awk '{for (i = 1; i <= NF; i++) if ($i == "-p") {print $(i + 1); exit}}')
SSH_PORT=${SSH_PORT:-22}
REMOTE_HOST=$(printf '%s\n' "$VAST_SSH" | awk '{print $NF}')
RSYNC_SSH="ssh -p $SSH_PORT"

REMOTE_APP="/workspace/telemost-transcriber"
TARGET="${1:-all}"

# --- Цвета ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}▶ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }

RSYNC_OPTS="-avz --delete --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' --exclude='.env.deploy' --exclude='credentials/' --exclude='recordings/' --exclude='.git'"

# --- Копируем скрипт рестарта ---
upload_restart_script() {
    scp -P "$SSH_PORT" "$SCRIPT_DIR/remote/restart_service.sh" \
        "$REMOTE_HOST:$REMOTE_APP/remote/restart_service.sh" 2>/dev/null
}

# --- Синк + рестарт по сервису ---
sync_and_restart_tg() {
    log "Синк tg-bot..."
    rsync $RSYNC_OPTS -e "$RSYNC_SSH" "$SCRIPT_DIR/tg-bot/" "$REMOTE_HOST:$REMOTE_APP/tg-bot/"
    upload_restart_script
    $VAST_SSH "bash $REMOTE_APP/remote/restart_service.sh tg"
}

sync_and_restart_watcher() {
    log "Синк tg-bot (для drive-watcher)..."
    rsync $RSYNC_OPTS -e "$RSYNC_SSH" "$SCRIPT_DIR/tg-bot/" "$REMOTE_HOST:$REMOTE_APP/tg-bot/"
    upload_restart_script
    $VAST_SSH "bash $REMOTE_APP/remote/restart_service.sh watcher"
}

sync_and_restart_bot() {
    log "Синк bot-service..."
    rsync $RSYNC_OPTS -e "$RSYNC_SSH" "$SCRIPT_DIR/bot-service/" "$REMOTE_HOST:$REMOTE_APP/bot-service/"
    upload_restart_script
    $VAST_SSH "bash $REMOTE_APP/remote/restart_service.sh bot"
}

sync_and_restart_transcriber() {
    log "Синк transcriber-service..."
    rsync $RSYNC_OPTS -e "$RSYNC_SSH" "$SCRIPT_DIR/transcriber-service/" "$REMOTE_HOST:$REMOTE_APP/transcriber-service/"
    upload_restart_script
    $VAST_SSH "bash $REMOTE_APP/remote/restart_service.sh transcriber"
}

case "$TARGET" in
    tg)           sync_and_restart_tg ;;
    watcher)      sync_and_restart_watcher ;;
    bot)          sync_and_restart_bot ;;
    transcriber)  sync_and_restart_transcriber ;;
    all)
        log "Синк всех сервисов..."
        rsync $RSYNC_OPTS -e "$RSYNC_SSH" "$SCRIPT_DIR/tg-bot/" "$REMOTE_HOST:$REMOTE_APP/tg-bot/"
        rsync $RSYNC_OPTS -e "$RSYNC_SSH" "$SCRIPT_DIR/bot-service/" "$REMOTE_HOST:$REMOTE_APP/bot-service/"
        rsync $RSYNC_OPTS -e "$RSYNC_SSH" "$SCRIPT_DIR/transcriber-service/" "$REMOTE_HOST:$REMOTE_APP/transcriber-service/"
        upload_restart_script
        $VAST_SSH "bash $REMOTE_APP/remote/restart_service.sh all"
        ;;
    *)
        echo "Использование: ./hotfix.sh [tg|watcher|bot|transcriber|all]"
        exit 1
        ;;
esac

log "Готово!"
