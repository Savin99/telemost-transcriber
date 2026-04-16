#!/usr/bin/env bash
# deploy.sh — быстрый деплой на Vast.ai одной командой
# Использование:
#   ./deploy.sh              — push + pull + рестарт всех сервисов
#   ./deploy.sh tg           — рестарт только tg-bot
#   ./deploy.sh watcher      — рестарт только drive-watcher
#   ./deploy.sh bot          — рестарт только bot-service
#   ./deploy.sh transcriber  — рестарт только transcriber-service
#   ./deploy.sh logs         — просто посмотреть логи
#
# Настройка (один раз):
#   export VAST_SSH="ssh -p 12345 root@12.34.56.78"  — или добавь в .env.deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- Загрузка SSH-конфига ---
ENV_FILE="$SCRIPT_DIR/.env.deploy"
if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
fi

if [ -z "${VAST_SSH:-}" ]; then
    echo "❌ VAST_SSH не задан!"
    echo ""
    echo "Создай файл .env.deploy с одной строкой:"
    echo '  VAST_SSH="ssh -p PORT root@IP"'
    echo ""
    echo "Или экспортируй: export VAST_SSH=\"ssh -p PORT root@IP\""
    exit 1
fi

# --- Извлечение параметров SSH для scp ---
SSH_PORT=$(printf '%s\n' "$VAST_SSH" | awk '{for (i = 1; i <= NF; i++) if ($i == "-p") {print $(i + 1); exit}}')
SSH_PORT=${SSH_PORT:-22}
REMOTE_HOST=$(printf '%s\n' "$VAST_SSH" | awk '{print $NF}')

REMOTE_APP="/workspace/telemost-transcriber"
REMOTE_LOGS="/workspace/logs"
TARGET="${1:-all}"

# --- Цвета ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}▶ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
err()  { echo -e "${RED}✖ $1${NC}"; }

# --- Функция: показать логи ---
show_logs() {
    log "Последние логи:"
    $VAST_SSH "tail -20 $REMOTE_LOGS/tg-bot.log 2>/dev/null; echo '---'; tail -20 $REMOTE_LOGS/drive-watcher.log 2>/dev/null; echo '---'; tail -20 $REMOTE_LOGS/bot.log 2>/dev/null; echo '---'; tail -20 $REMOTE_LOGS/transcriber.log 2>/dev/null" 2>/dev/null || true
}

# --- Только логи ---
if [ "$TARGET" = "logs" ]; then
    show_logs
    exit 0
fi

# --- 1. Git push (локально) ---
log "Git push..."
if git diff --quiet && git diff --cached --quiet; then
    warn "Нет изменений в git, пушу текущее состояние"
fi
git push origin main 2>&1 | tail -3
log "Push готов"

# --- 2. Git pull (на сервере) ---
log "Git pull на сервере..."
$VAST_SSH "cd $REMOTE_APP && git pull --ff-only" 2>&1 | tail -5
log "Pull готов"

# --- 3. Копируем и запускаем скрипт рестарта ---
log "Рестарт сервисов ($TARGET)..."
scp -P "$SSH_PORT" "$SCRIPT_DIR/remote/restart_service.sh" \
    "$REMOTE_HOST:$REMOTE_APP/remote/restart_service.sh" 2>/dev/null
$VAST_SSH "bash $REMOTE_APP/remote/restart_service.sh $TARGET"

log "Готово! 🚀"
