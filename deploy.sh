#!/usr/bin/env bash
# deploy.sh — быстрый деплой на Vast.ai одной командой
# Использование:
#   ./deploy.sh              — push + pull + рестарт всех сервисов
#   ./deploy.sh tg           — рестарт только tg-bot
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
    $VAST_SSH "tail -20 $REMOTE_LOGS/tg-bot.log 2>/dev/null; echo '---'; tail -20 $REMOTE_LOGS/bot.log 2>/dev/null; echo '---'; tail -20 $REMOTE_LOGS/transcriber.log 2>/dev/null" 2>/dev/null || true
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

# --- 3. Рестарт сервисов ---
restart_tg() {
    log "Рестарт tg-bot..."
    $VAST_SSH "source /workspace/.bashrc 2>/dev/null || true; if [ -z \"\${TG_BOT_TOKEN:-}\" ]; then echo 'tg-bot skipped: TG_BOT_TOKEN is not set'; else pkill -f '^/venv/main/bin/python bot.py$' 2>/dev/null || true; sleep 1; cd $REMOTE_APP/tg-bot && TG_BOT_TOKEN=\$TG_BOT_TOKEN BOT_API_URL=http://localhost:8000 nohup /venv/main/bin/python bot.py > $REMOTE_LOGS/tg-bot.log 2>&1 < /dev/null & echo 'tg-bot PID: '\$!; fi" 2>/dev/null
}

restart_bot() {
    log "Рестарт bot-service..."
    $VAST_SSH "source /workspace/.bashrc 2>/dev/null || true; fuser -k 8000/tcp 2>/dev/null || true; sleep 1; cd $REMOTE_APP/bot-service && TRANSCRIBER_URL=http://localhost:8001 DATABASE_URL=\"sqlite+aiosqlite:///\/workspace/transcriber.db\" RECORDINGS_DIR=/workspace/recordings BOT_NAME=\"\${BOT_NAME:-Транскрибатор}\" DISPLAY=:99 nohup /venv/main/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > $REMOTE_LOGS/bot.log 2>&1 < /dev/null & echo 'bot-service PID: '\$!" 2>/dev/null
}

restart_transcriber() {
    log "Рестарт transcriber-service..."
    $VAST_SSH "source /workspace/.bashrc 2>/dev/null || true; mkdir -p /workspace/voice_bank; fuser -k 8001/tcp 2>/dev/null || true; sleep 1; cd $REMOTE_APP/transcriber-service && HF_TOKEN=\${HF_TOKEN:-} ANTHROPIC_API_KEY=\${ANTHROPIC_API_KEY:-} DIARIZATION_MODEL=\${DIARIZATION_MODEL:-pyannote/speaker-diarization-community-1} ASR_LANGUAGE=\${ASR_LANGUAGE:-ru} CLUSTERING_THRESHOLD=\${CLUSTERING_THRESHOLD:-0.35} CLUSTERING_FA=\${CLUSTERING_FA:-0.04} CLUSTERING_FB=\${CLUSTERING_FB:-0.9} VOICE_BANK_DIR=\${VOICE_BANK_DIR:-/workspace/voice_bank} VOICE_MATCH_THRESHOLD=\${VOICE_MATCH_THRESHOLD:-0.40} MIN_EMBEDDING_SEGMENT_SEC=\${MIN_EMBEDDING_SEGMENT_SEC:-1.0} SPEAKER_LLM_REFINEMENT_ENABLED=\${SPEAKER_LLM_REFINEMENT_ENABLED:-false} SPEAKER_LLM_EXECUTOR_MODEL=\${SPEAKER_LLM_EXECUTOR_MODEL:-claude-sonnet-4-6} SPEAKER_LLM_ADVISOR_MODEL=\${SPEAKER_LLM_ADVISOR_MODEL:-claude-opus-4-6} SPEAKER_LLM_ADVISOR_ENABLED=\${SPEAKER_LLM_ADVISOR_ENABLED:-true} TRANSCRIPT_LLM_REFINEMENT_ENABLED=\${TRANSCRIPT_LLM_REFINEMENT_ENABLED:-true} TRANSCRIPT_LLM_EXECUTOR_MODEL=\${TRANSCRIPT_LLM_EXECUTOR_MODEL:-claude-sonnet-4-6} TRANSCRIPT_LLM_ADVISOR_MODEL=\${TRANSCRIPT_LLM_ADVISOR_MODEL:-claude-opus-4-6} TRANSCRIPT_LLM_ADVISOR_ENABLED=\${TRANSCRIPT_LLM_ADVISOR_ENABLED:-true} nohup /venv/main/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 > $REMOTE_LOGS/transcriber.log 2>&1 < /dev/null & echo 'transcriber-service PID: '\$!" 2>/dev/null
}

case "$TARGET" in
    tg)           restart_tg ;;
    bot)          restart_bot ;;
    transcriber)  restart_transcriber ;;
    all)
        restart_tg
        restart_bot
        # transcriber обычно не трогаем — долго грузит модель
        warn "transcriber НЕ перезапущен (модель грузится ~2 мин). Для рестарта: ./deploy.sh transcriber"
        ;;
    *)
        err "Неизвестный сервис: $TARGET"
        echo "Доступные: tg, bot, transcriber, all, logs"
        exit 1
        ;;
esac

# --- 4. Проверка ---
sleep 2
log "Проверка health..."
$VAST_SSH "curl -s http://localhost:8000/health 2>/dev/null && echo '' || echo 'bot-service: не отвечает'; curl -s http://localhost:8001/health 2>/dev/null && echo '' || echo 'transcriber: не отвечает'" 2>/dev/null

log "Готово! 🚀"
