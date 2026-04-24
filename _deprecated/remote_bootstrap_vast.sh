#!/usr/bin/env bash

echo "[DEPRECATED] Этот скрипт не работает с VDS 193.233.87.211 + Modal." >&2
echo "Используй: ./deploy_vds.sh (см. ./deploy_vds.sh --help)" >&2
exit 1

# bootstrap_vast.sh — развернуть transcribe-stack на НОВОМ Vast-инстансе.
#
# Выполняется ЛОКАЛЬНО (на Mac или VDS), нужен доступ к новому vast:
#   export VAST_SSH='ssh -p PORT root@NEW_IP'
#   export VDS_HOST=193.233.87.211   # куда vast будет reverse-tunnel'ить
#   bash remote/bootstrap_vast.sh
#
# Что делает:
#   1. Генерит SSH-ключ на vast, кладёт public в authorized_keys на VDS.
#   2. Ставит autossh.
#   3. Пишет supervisord-конфиги для:
#        bot-service, transcriber-service, drive-watcher, reverse-tunnel.
#   4. Ставит cron на voice_bank backup (локально + на VDS раз в сутки).
#   5. Ставит cron на health-watchdog (с алёртом в TG).
#   6. Если на VDS есть /root/transcribe-backups/voice_bank_latest.tar.gz —
#      восстанавливает его в /workspace/voice_bank/.
#   7. Запускает supervisorctl reread && update.
#
# После: сервис доступен снаружи через http://VDS_IP:18001 (как раньше).

set -euo pipefail

: "${VAST_SSH:?VAST_SSH not set, e.g. export VAST_SSH=\"ssh -p 50123 root@1.2.3.4\"}"
: "${VDS_HOST:=193.233.87.211}"

SSH_PORT=$(printf '%s' "$VAST_SSH" | awk '{for(i=1;i<=NF;i++) if($i=="-p") print $(i+1)}')
SSH_PORT=${SSH_PORT:-22}
REMOTE_HOST=$(printf '%s' "$VAST_SSH" | awk '{print $NF}')

echo "▶ vast: $REMOTE_HOST (port $SSH_PORT)"
echo "▶ VDS:  $VDS_HOST"

# --- 1. SSH-ключ vast → VDS ---
echo "▶ Генерю SSH-ключ на vast..."
VAST_PUB=$($VAST_SSH 'test -f ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C vast@transcribe >/dev/null 2>&1; cat ~/.ssh/id_ed25519.pub')

echo "▶ Кладу ключ на VDS authorized_keys..."
ssh -o StrictHostKeyChecking=accept-new "root@${VDS_HOST}" \
    "grep -qxF '${VAST_PUB}' ~/.ssh/authorized_keys || echo '${VAST_PUB}' >> ~/.ssh/authorized_keys"

# Проверка: vast → VDS без пароля
echo "▶ Проверяю SSH vast → VDS..."
$VAST_SSH "ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes root@${VDS_HOST} 'echo OK'" \
    || { echo "❌ vast не может ходить на VDS"; exit 1; }

# --- 2. Всё остальное на vast одной сессией ---
echo "▶ Конфигурирую vast (autossh + supervisord + cron)..."
$VAST_SSH "VDS_HOST='${VDS_HOST}' bash -s" <<'REMOTE'
set -e

# --- autossh ---
which autossh >/dev/null 2>&1 || { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y autossh; } >/dev/null

# --- supervisord config ---
cat > /etc/supervisor/conf.d/transcribe-stack.conf <<CONF
[program:bot-service]
command=/bin/bash -lc "source /workspace/.bashrc; cd /workspace/telemost-transcriber/bot-service; exec /venv/main/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
autostart=true
autorestart=true
startretries=999
stopasgroup=true
killasgroup=true
stdout_logfile=/workspace/logs/bot.log
stdout_logfile_maxbytes=20MB
stdout_logfile_backups=3
redirect_stderr=true
environment=DISPLAY=":99"

[program:transcriber-service]
command=/bin/bash -lc "source /workspace/.bashrc; cd /workspace/telemost-transcriber/transcriber-service; exec /venv/main/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001"
autostart=true
autorestart=true
startretries=999
stopasgroup=true
killasgroup=true
stdout_logfile=/workspace/logs/transcriber.log
stdout_logfile_maxbytes=20MB
stdout_logfile_backups=3
redirect_stderr=true
startsecs=60

[program:tg-bot]
command=/bin/bash -lc "source /workspace/.bashrc; cd /workspace/telemost-transcriber/tg-bot; exec /venv/main/bin/python bot.py"
autostart=true
autorestart=true
startretries=999
stopasgroup=true
killasgroup=true
stdout_logfile=/workspace/logs/tg-bot.log
stdout_logfile_maxbytes=20MB
stdout_logfile_backups=3
redirect_stderr=true

[program:drive-watcher]
command=/bin/bash -lc "source /workspace/.bashrc; cd /workspace/telemost-transcriber/tg-bot; exec /venv/main/bin/python drive_watcher.py"
autostart=true
autorestart=true
startretries=999
stopasgroup=true
killasgroup=true
stdout_logfile=/workspace/logs/drive-watcher.log
stdout_logfile_maxbytes=20MB
stdout_logfile_backups=3
redirect_stderr=true

# ВНИМАНИЕ: любое изменение transcribe-stack.conf + supervisorctl update
# перезапускает ВСЮ группу — включая активные записи встреч.
# Проверяй /meetings?status=recording перед правкой на проде.
[group:transcribe-stack]
programs=bot-service,transcriber-service,tg-bot,drive-watcher
CONF

cat > /etc/supervisor/conf.d/reverse-tunnel.conf <<CONF
[program:reverse-tunnel]
command=/usr/bin/autossh -M 0 -NT -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new -R 127.0.0.1:18000:127.0.0.1:8000 root@${VDS_HOST}
autostart=true
autorestart=true
startretries=999
environment=AUTOSSH_GATETIME="0"
stdout_logfile=/workspace/logs/reverse-tunnel.log
stdout_logfile_maxbytes=10MB
redirect_stderr=true
CONF

# --- Voice_bank backup (локально + на VDS раз в сутки) ---
mkdir -p /root/backups
cat > /usr/local/bin/voice_bank_backup.sh <<BAK
#!/usr/bin/env bash
set -e
SRC="/workspace/voice_bank"
BAK_DIR="/root/backups"
mkdir -p "\$BAK_DIR"
TS=\$(date -u +%Y%m%dT%H%M%SZ)
tar -C "\$(dirname "\$SRC")" -czf "\$BAK_DIR/voice_bank_\${TS}.tar.gz" "\$(basename "\$SRC")"
ln -sf "voice_bank_\${TS}.tar.gz" "\$BAK_DIR/voice_bank_latest.tar.gz"
find "\$BAK_DIR" -name "voice_bank_*.tar.gz" -type f -mtime +3 -delete
BAK
chmod +x /usr/local/bin/voice_bank_backup.sh

cat > /usr/local/bin/voice_bank_backup_remote.sh <<REM
#!/usr/bin/env bash
set -e
# Пересылаем свежий tar.gz на VDS (там лежит долговременное хранилище).
ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes root@${VDS_HOST} 'mkdir -p /root/transcribe-backups'
scp -o BatchMode=yes /root/backups/voice_bank_latest.tar.gz \
    root@${VDS_HOST}:/root/transcribe-backups/voice_bank_latest.tar.gz
# Ротация на VDS: держим версию с датой ещё 14 дней.
TS=\$(date -u +%Y%m%dT%H%M%SZ)
ssh root@${VDS_HOST} "cp /root/transcribe-backups/voice_bank_latest.tar.gz /root/transcribe-backups/voice_bank_\${TS}.tar.gz; find /root/transcribe-backups -name 'voice_bank_2*' -mtime +14 -delete"
REM
chmod +x /usr/local/bin/voice_bank_backup_remote.sh

cat > /etc/cron.d/voice_bank_backup <<CRON
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 * * * * root /usr/local/bin/voice_bank_backup.sh >> /workspace/logs/voice_bank_backup.log 2>&1
15 3 * * * root /usr/local/bin/voice_bank_backup_remote.sh >> /workspace/logs/voice_bank_backup.log 2>&1
CRON
chmod 644 /etc/cron.d/voice_bank_backup

# --- Health watchdog (алёрт в TG при 3 FAIL) ---
cat > /usr/local/bin/health_watchdog.sh <<'WD'
#!/usr/bin/env bash
set -u
STATE="/tmp/health_watchdog.state"
LOG="/workspace/logs/watchdog.log"
mkdir -p "$(dirname "$LOG")"
source /workspace/.bashrc 2>/dev/null || true
TOKEN="${TG_BOT_TOKEN:-}"
CHAT="${TG_ALERT_CHAT_ID:-}"
KEY="${TELEMOST_SERVICE_API_KEY:-}"
ok=1
fail_reasons=""
curl -sf -m 10 -H "X-API-Key: $KEY" http://localhost:8000/health >/dev/null \
    || { ok=0; fail_reasons="${fail_reasons} bot-service"; }
curl -sf -m 10 http://localhost:8001/health >/dev/null \
    || { ok=0; fail_reasons="${fail_reasons} transcriber"; }
for svc in tg-bot drive-watcher; do
    state=$(supervisorctl status "transcribe-stack:${svc}" 2>/dev/null | awk '{print $2}')
    if [ "$state" != "RUNNING" ]; then
        ok=0
        fail_reasons="${fail_reasons} ${svc}:${state:-unknown}"
    fi
done
prev=$(cat "$STATE" 2>/dev/null || echo 0)
if [ "$ok" -eq 0 ]; then
    new=$((prev + 1))
    echo "$new" > "$STATE"
    echo "$(date -Is) health FAIL (consecutive=$new) reasons:${fail_reasons}" >> "$LOG"
    if [ "$new" -eq 3 ] && [ -n "$TOKEN" ] && [ -n "$CHAT" ]; then
        curl -sf -m 10 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${CHAT}" \
            --data-urlencode "text=⚠️ transcribe stack health FAIL 3× на $(hostname):${fail_reasons}" >/dev/null || true
    fi
else
    if [ "$prev" -gt 0 ] && [ -n "$TOKEN" ] && [ -n "$CHAT" ]; then
        curl -sf -m 10 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${CHAT}" \
            --data-urlencode "text=✅ transcribe stack recovered на $(hostname)" >/dev/null || true
    fi
    echo 0 > "$STATE"
fi
WD
chmod +x /usr/local/bin/health_watchdog.sh

cat > /etc/cron.d/health_watchdog <<CRON
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root /usr/local/bin/health_watchdog.sh
CRON
chmod 644 /etc/cron.d/health_watchdog

# --- Ротация старых .wav, чтобы диск не забивался ---
cat > /usr/local/bin/rotate_recordings.sh <<'RR'
#!/usr/bin/env bash
# Удаляем .wav старше RECORDINGS_TTL_DAYS (default 7).
# Транскрипт к этому моменту уже на Drive; .wav нужен только
# для повторной разметки голосов через /voices.
set -u
DIR="${RECORDINGS_DIR:-/workspace/recordings}"
TTL="${RECORDINGS_TTL_DAYS:-7}"
LOG="/workspace/logs/rotate_recordings.log"
mkdir -p "$(dirname "$LOG")"
if [ -d "$DIR" ]; then
    count=$(find "$DIR" -maxdepth 1 -type f -name '*.wav' -mtime +"$TTL" -print -delete 2>>"$LOG" | wc -l)
    echo "$(date -Is) removed=$count dir=$DIR ttl=${TTL}d" >> "$LOG"
fi
RR
chmod +x /usr/local/bin/rotate_recordings.sh

cat > /etc/cron.d/rotate_recordings <<CRON
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 4 * * * root /usr/local/bin/rotate_recordings.sh
CRON
chmod 644 /etc/cron.d/rotate_recordings

# --- Восстановление voice_bank из VDS (если есть backup) ---
if ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new root@${VDS_HOST} \
        'test -f /root/transcribe-backups/voice_bank_latest.tar.gz' 2>/dev/null; then
    echo "▶ Восстанавливаю voice_bank из VDS-бэкапа..."
    scp -o BatchMode=yes root@${VDS_HOST}:/root/transcribe-backups/voice_bank_latest.tar.gz /tmp/vb.tar.gz
    mkdir -p /workspace
    tar -xzf /tmp/vb.tar.gz -C /workspace/
    rm -f /tmp/vb.tar.gz
    echo "✅ voice_bank восстановлен из VDS"
fi

mkdir -p /workspace/logs /workspace/recordings /workspace/voice_bank

# --- Запуск всего ---
supervisorctl reread
supervisorctl update
sleep 5
supervisorctl status transcribe-stack: reverse-tunnel
echo "✅ bootstrap done"
REMOTE

# --- На VDS: подготовить директорию для бэкапов voice_bank ---
ssh "root@${VDS_HOST}" 'mkdir -p /root/transcribe-backups' 2>/dev/null || true

echo ""
echo "🎉 Bootstrap завершён."
echo "   - supervisord управляет bot-service + transcriber-service + drive-watcher + reverse-tunnel"
echo "   - cron: voice_bank backup каждый час локально + раз в сутки на VDS"
echo "   - cron: health-watchdog каждые 5 мин"
echo "   - Публичный URL: http://${VDS_HOST}:18001 (без изменений)"
