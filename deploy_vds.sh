#!/usr/bin/env bash
# deploy_vds.sh — единый deploy-скрипт telemost-transcriber на VDS 193.233.87.211.
#
# Архитектура на VDS:
#   /root/telemost/                         — root-папка проекта (НЕ git-репо)
#   /root/telemost/env.sh (mode 600)        — источник всех секретов
#   /root/telemost/venv/                    — venv для supervisord-сервисов
#   supervisord (systemd):
#     telemost-stack:telemost-bot-service   — uvicorn app.main:app на 127.0.0.1:8010
#     telemost-stack:telemost-tg-bot        — python bot.py
#     telemost-stack:telemost-drive-watcher — python drive_watcher.py
#     telemost-xvfb, telemost-pulse         — вспомогательные
#   Docker:
#     telemost-transcriber-1                — docker-compose -f docker-compose.yml -f docker-compose.cpu.yml
#                                             порт 0.0.0.0:8011 -> 8001, ASR_BACKEND=modal
#   Modal (локально, не на VDS):
#     modal deploy modal_app/whisperx_service.py   — serverless GPU для ASR
#
# Использование:
#   ./deploy_vds.sh <target> [flags]
#
# Targets:
#   bot            rsync bot-service/    + supervisorctl restart + healthcheck
#   tg             rsync tg-bot/         + supervisorctl restart (telemost-tg-bot)
#   watcher        rsync tg-bot/         + supervisorctl restart (telemost-drive-watcher)
#   transcriber    rsync transcriber-service/ + docker compose up -d --build + healthcheck
#   modal          локально: modal deploy modal_app/whisperx_service.py
#   all            modal -> transcriber -> bot -> tg -> watcher
#   restart <svc>  только supervisorctl/docker restart (без rsync)
#   logs <svc>     ssh ... tail -f /root/telemost/logs/<svc>.log
#   status         supervisorctl status + docker ps + df -h + free -m
#
# Flags:
#   --dry-run      показать что будет, без побочных эффектов
#   --no-build     для transcriber: не пересобирать Docker image
#   --skip-health  не ждать healthcheck
#   --strict-sync  rsync с --delete
#   --lazy         не рестартить если rsync ничего не изменил
#   --backup-env   cp /root/telemost/env.sh env.sh.bak_<TS> перед операцией
#   --force        пропустить disk/ram guards
#   -v/--verbose   rsync --progress, docker build --progress=plain
#   -h/--help      показать эту справку
#
# Formal: см. .env.deploy.example для формата .env.deploy.
# Для первичного раскатывания SSH-ключа:  ssh-copy-id root@193.233.87.211

set -Eeuo pipefail
shopt -s inherit_errexit 2>/dev/null || true

# ─────────── Константы ───────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REMOTE_APP="/root/telemost"
REMOTE_LOGS="$REMOTE_APP/logs"
REMOTE_ENV="$REMOTE_APP/env.sh"
REMOTE_BOT_PORT=8010
REMOTE_TRANSCRIBER_PORT=8011

SUP_GROUP="telemost-stack"
SUP_BOT="$SUP_GROUP:telemost-bot-service"
SUP_TG="$SUP_GROUP:telemost-tg-bot"
SUP_WATCHER="$SUP_GROUP:telemost-drive-watcher"
DOCKER_TRANSCRIBER_SVC="transcriber"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.cpu.yml)

MODAL_APP_FILE="modal_app/whisperx_service.py"
MODAL_APP_NAME="telemost-whisperx"

LOCK_DIR="${TMPDIR:-/tmp}/telemost-deploy.lock.d"
DEPLOY_ID="$(date +%s)-$$"
BUILD_LOG_DIR="$SCRIPT_DIR/.deploy-logs"

# ─────────── Цвета (ASCII-safe, не emoji) ───────────
if [[ -t 1 ]] && command -v tput >/dev/null 2>&1; then
  C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[1;33m'
  C_RED=$'\033[0;31m'
  C_CYAN=$'\033[0;36m'
  C_DIM=$'\033[2m'
  C_RESET=$'\033[0m'
else
  C_GREEN='' C_YELLOW='' C_RED='' C_CYAN='' C_DIM='' C_RESET=''
fi

ts() { date +'%H:%M:%S'; }
log()  { printf '%s[%s] %s%s\n' "$C_DIM" "$(ts)" "$1" "$C_RESET"; }
step() { printf '%s[%s] [*]%s %s\n' "$C_CYAN" "$(ts)" "$C_RESET" "$1"; }
ok()   { printf '%s[%s] [OK]%s %s\n' "$C_GREEN" "$(ts)" "$C_RESET" "$1"; }
warn() { printf '%s[%s] [WARN]%s %s\n' "$C_YELLOW" "$(ts)" "$C_RESET" "$1" >&2; }
err()  { printf '%s[%s] [ERR]%s %s\n' "$C_RED" "$(ts)" "$C_RESET" "$1" >&2; }

# ─────────── Переменные состояния (CLI) ───────────
TARGET=""
RESTART_SVC=""
LOGS_SVC=""
DRY_RUN=0
NO_BUILD=0
SKIP_HEALTH=0
STRICT_SYNC=0
LAZY=0
BACKUP_ENV=0
FORCE=0
VERBOSE=0

WARNINGS_COUNT=0
STEPS_COUNT=0
START_TIME=$(date +%s)

# ─────────── CLI парсер ───────────
usage() {
  # Выводим шапку скрипта (всё что между первой и последней строкой "# ...")
  sed -n '2,/^set -E/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
}

parse_args() {
  local positional=()
  while (($# > 0)); do
    case "$1" in
      -h|--help) usage; exit 0 ;;
      --dry-run) DRY_RUN=1 ;;
      --no-build) NO_BUILD=1 ;;
      --skip-health) SKIP_HEALTH=1 ;;
      --strict-sync) STRICT_SYNC=1 ;;
      --lazy) LAZY=1 ;;
      --backup-env) BACKUP_ENV=1 ;;
      --force) FORCE=1 ;;
      -v|--verbose) VERBOSE=1 ;;
      --) shift; positional+=("$@"); break ;;
      -*) err "Неизвестный флаг: $1"; usage >&2; exit 2 ;;
      *) positional+=("$1") ;;
    esac
    shift
  done

  TARGET="${positional[0]:-}"
  case "$TARGET" in
    bot|tg|watcher|transcriber|modal|all|status) ;;
    restart)
      RESTART_SVC="${positional[1]:-}"
      [[ -z "$RESTART_SVC" ]] && { err "Укажи сервис: restart {bot|tg|watcher|transcriber}"; exit 2; }
      ;;
    logs)
      LOGS_SVC="${positional[1]:-}"
      [[ -z "$LOGS_SVC" ]] && { err "Укажи сервис: logs {bot|tg|watcher|transcriber}"; exit 2; }
      ;;
    "")
      err "Не задан target."
      usage >&2
      exit 2
      ;;
    *)
      err "Неизвестный target: $TARGET"
      usage >&2
      exit 2
      ;;
  esac
}

# ─────────── Обёртки dry-run / ssh / rsync ───────────
run() {
  if (( DRY_RUN )); then
    printf '%s[dry]%s %s\n' "$C_DIM" "$C_RESET" "$*"
  else
    "$@"
  fi
}

# ssh_exec "remote command" — выполнение на VDS.
# Remote-side выражения должны уже быть quoted (single quotes или escape'нутые $).
ssh_exec() {
  local cmd="$1"
  if (( DRY_RUN )); then
    printf '%s[dry-ssh]%s %s\n' "$C_DIM" "$C_RESET" "$cmd"
    return 0
  fi
  # shellcheck disable=SC2086
  $VDS_SSH "$cmd"
}

# ssh_exec_ro — то же самое, но допустимо в dry-run для status/healthcheck.
ssh_exec_ro() {
  local cmd="$1"
  # shellcheck disable=SC2086
  $VDS_SSH "$cmd"
}

# ─────────── Загрузка .env.deploy ───────────
load_env_deploy() {
  local env_file="$SCRIPT_DIR/.env.deploy"
  if [[ ! -f "$env_file" ]]; then
    err ".env.deploy не найден. Скопируй .env.deploy.example и заполни."
    exit 2
  fi

  set -a
  # shellcheck source=/dev/null
  source "$env_file"
  set +a

  # Backwards-compat: если задан VAST_SSH (старый Vast.ai формат) — парсим его.
  if [[ -z "${VDS_SSH:-}${VDS_SSH_HOST:-}" && -n "${VAST_SSH:-}" ]]; then
    warn "В .env.deploy используется устаревшая переменная VAST_SSH."
    warn "Обнови файл: VDS_SSH_HOST=\"193.233.87.211\", VDS_SSH_PORT=\"22\", VDS_SSH_USER=\"root\"."
    local _port _host
    _port=$(printf '%s\n' "$VAST_SSH" | awk '{for (i=1;i<=NF;i++) if ($i=="-p") {print $(i+1); exit}}')
    _host=$(printf '%s\n' "$VAST_SSH" | awk '{print $NF}')
    VDS_SSH_PORT="${_port:-22}"
    VDS_SSH_USER="${_host%@*}"
    VDS_SSH_HOST="${_host#*@}"
    if [[ "$VDS_SSH_HOST" != "193.233.87.211" ]]; then
      warn "Старый VAST_SSH указывает на $VDS_SSH_HOST (должно быть 193.233.87.211) — ЗАМЕНИ перед продакшн-деплоем."
    fi
  fi

  VDS_SSH_HOST="${VDS_SSH_HOST:-}"
  VDS_SSH_PORT="${VDS_SSH_PORT:-22}"
  VDS_SSH_USER="${VDS_SSH_USER:-root}"
  VDS_SSH="${VDS_SSH:-}"

  if [[ -z "$VDS_SSH" ]]; then
    if [[ -z "$VDS_SSH_HOST" ]]; then
      err "VDS_SSH_HOST не задан в .env.deploy."
      exit 2
    fi
    VDS_SSH="ssh -p $VDS_SSH_PORT -o ConnectTimeout=10 -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new $VDS_SSH_USER@$VDS_SSH_HOST"
  fi

  RSYNC_SSH="ssh -p $VDS_SSH_PORT -o ConnectTimeout=10 -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new"
  RSYNC_REMOTE="${VDS_SSH_USER}@${VDS_SSH_HOST}"
}

# ─────────── Поиск modal CLI (локально) ───────────
find_modal() {
  local candidates=(
    "$HOME/Library/Python/3.13/bin/modal"
    "$HOME/Library/Python/3.12/bin/modal"
    "$HOME/Library/Python/3.11/bin/modal"
  )
  if command -v python3 >/dev/null 2>&1; then
    local userbase
    userbase="$(python3 -m site --user-base 2>/dev/null || true)"
    [[ -n "$userbase" ]] && candidates+=("$userbase/bin/modal")
  fi
  for c in "${candidates[@]}"; do
    [[ -x "$c" ]] && { echo "$c"; return 0; }
  done
  command -v modal 2>/dev/null && return 0
  return 1
}

# ─────────── Preflight ───────────
preflight_local() {
  local missing=()
  for tool in ssh rsync curl awk sed grep; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
  done
  if (( ${#missing[@]} > 0 )); then
    err "Локально отсутствуют инструменты: ${missing[*]}"
    exit 3
  fi

  # Git warning (не блок)
  if command -v git >/dev/null 2>&1 && git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    local dirty
    dirty=$(git -C "$SCRIPT_DIR" status --porcelain 2>/dev/null | head -5)
    if [[ -n "$dirty" ]]; then
      warn "Uncommitted изменения в рабочей копии (первые 5):"
      printf '%s\n' "$dirty" | sed 's/^/    /' >&2
      warn "Деплой продолжится — но коммить, чтобы было что ревертить."
      ((WARNINGS_COUNT++)) || true
    fi
  fi

  mkdir -p "$BUILD_LOG_DIR"
}

preflight_ssh() {
  step "Проверка SSH-доступа на $RSYNC_REMOTE..."
  if ! ssh -o BatchMode=yes -o ConnectTimeout=5 -p "$VDS_SSH_PORT" "$RSYNC_REMOTE" 'true' 2>/dev/null; then
    err "SSH-ключ не настроен для $RSYNC_REMOTE (BatchMode=yes упал)."
    err "  Настрой: ssh-copy-id -p $VDS_SSH_PORT $RSYNC_REMOTE"
    err "  Или проверь ~/.ssh/config / проверь host key: ssh-keygen -R $VDS_SSH_HOST"
    exit 3
  fi
  ok "SSH доступен"
}

preflight_remote() {
  step "Проверка состояния VDS..."
  local checks
  checks=$(ssh_exec_ro "
    set -e
    echo UID=\$(id -u)
    echo SUP=\$(systemctl is-active supervisor 2>/dev/null || echo inactive)
    echo DOCKER_OK=\$(docker ps >/dev/null 2>&1 && echo yes || echo no)
    echo COMPOSE_VER=\$(docker compose version --short 2>/dev/null || echo none)
    echo FREE_MB=\$(awk '/MemAvailable:/ {print int(\$2/1024)}' /proc/meminfo)
    echo DISK_AVAIL_GB=\$(df -BG / | awk 'NR==2 {gsub(/G/, \"\", \$4); print \$4}')
    echo ENV_MODE=\$(stat -c %a '$REMOTE_ENV' 2>/dev/null || echo none)
    echo APP_DIR=\$(test -d '$REMOTE_APP' && echo yes || echo no)
  ") || { err "Не удалось выполнить preflight-команды на VDS"; exit 3; }

  local uid sup docker_ok compose_ver free_mb disk_gb env_mode app_dir
  uid=$(sed -n 's/^UID=//p' <<<"$checks")
  sup=$(sed -n 's/^SUP=//p' <<<"$checks")
  docker_ok=$(sed -n 's/^DOCKER_OK=//p' <<<"$checks")
  compose_ver=$(sed -n 's/^COMPOSE_VER=//p' <<<"$checks")
  free_mb=$(sed -n 's/^FREE_MB=//p' <<<"$checks")
  disk_gb=$(sed -n 's/^DISK_AVAIL_GB=//p' <<<"$checks")
  env_mode=$(sed -n 's/^ENV_MODE=//p' <<<"$checks")
  app_dir=$(sed -n 's/^APP_DIR=//p' <<<"$checks")

  [[ "$app_dir" == "yes" ]] || { err "$REMOTE_APP не существует на VDS"; exit 3; }
  [[ "$uid" == "0" ]] || warn "UID на VDS = $uid (ожидается 0 — root). supervisorctl/docker могут требовать sudo."
  [[ "$sup" == "active" ]] || { err "supervisord не active: $sup. Почини: systemctl restart supervisor"; exit 3; }
  [[ "$docker_ok" == "yes" ]] || { err "docker ps упал на VDS."; exit 3; }

  # docker compose >= 2.20 (для !override / !reset YAML-тегов)
  if [[ "$compose_ver" == "none" ]]; then
    err "docker compose plugin не установлен на VDS."
    exit 3
  fi
  local cmaj cmin
  cmaj=$(awk -F. '{print $1}' <<<"$compose_ver")
  cmin=$(awk -F. '{print $2}' <<<"$compose_ver")
  if (( cmaj < 2 || (cmaj == 2 && cmin < 20) )); then
    warn "docker compose $compose_ver < 2.20 — YAML-теги !override/!reset могут не работать."
    ((WARNINGS_COUNT++)) || true
  fi

  if [[ "$env_mode" == "none" ]]; then
    warn "$REMOTE_ENV не найден. Сервисы не стартуют без env.sh."
    ((WARNINGS_COUNT++)) || true
  elif [[ "$env_mode" != "600" ]]; then
    warn "$REMOTE_ENV mode=$env_mode (ожидается 600). chmod 600 $REMOTE_ENV"
    ((WARNINGS_COUNT++)) || true
  fi

  # Disk/ram guards (только для transcriber с build)
  if [[ "$TARGET" == "transcriber" || "$TARGET" == "all" ]] && (( ! NO_BUILD )); then
    if (( free_mb < 800 )) && (( ! FORCE )); then
      err "На VDS свободно $free_mb MB RAM (< 800). Docker build упадёт. Используй --no-build или --force."
      exit 3
    fi
    if (( disk_gb < 5 )) && (( ! FORCE )); then
      err "На VDS свободно $disk_gb GB диска (< 5). Используй --force или почисти."
      exit 3
    fi
    log "VDS RAM=${free_mb}MB, disk=${disk_gb}GB — OK"
  fi

  ok "VDS preflight passed"
}

preflight_modal() {
  step "Проверка Modal CLI (локально)..."
  local modal_bin
  if ! modal_bin=$(find_modal); then
    err "modal CLI не найден."
    err "  Установи: pip3 install --user modal"
    err "  Или укажи путь вручную, добавив в PATH: ~/Library/Python/3.13/bin"
    exit 3
  fi
  MODAL_BIN="$modal_bin"
  log "modal CLI: $MODAL_BIN"

  # Проверка auth
  if ! "$MODAL_BIN" profile current >/dev/null 2>&1; then
    if [[ -n "${MODAL_TOKEN_ID:-}" && -n "${MODAL_TOKEN_SECRET:-}" ]]; then
      log "Используем MODAL_TOKEN_ID/SECRET из .env.deploy"
      export MODAL_TOKEN_ID MODAL_TOKEN_SECRET
    else
      err "modal не авторизован."
      err "  Выполни: modal token new"
      err "  Или задай MODAL_TOKEN_ID/MODAL_TOKEN_SECRET в .env.deploy"
      exit 3
    fi
  fi
  ok "Modal CLI готов"
}

# ─────────── Lock ───────────
acquire_lock() {
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    local holder_pid="?"
    [[ -f "$LOCK_DIR/pid" ]] && holder_pid="$(cat "$LOCK_DIR/pid")"
    err "Deploy уже запущен (PID $holder_pid, dir: $LOCK_DIR)."
    err "Если это stale lock: rm -rf \"$LOCK_DIR\""
    exit 4
  fi
  echo $$ > "$LOCK_DIR/pid"
  echo "$DEPLOY_ID" > "$LOCK_DIR/id"
}

release_lock() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }

cleanup() {
  local exit_code=$?
  release_lock
  if (( exit_code == 0 )); then
    local elapsed=$(( $(date +%s) - START_TIME ))
    if (( WARNINGS_COUNT > 0 )); then
      printf '\n%s[%s] DONE%s in %ds (%d warnings)\n' "$C_YELLOW" "$(ts)" "$C_RESET" "$elapsed" "$WARNINGS_COUNT"
    else
      printf '\n%s[%s] DONE%s in %ds\n' "$C_GREEN" "$(ts)" "$C_RESET" "$elapsed"
    fi
  else
    printf '\n%s[%s] FAILED%s (exit=%d)\n' "$C_RED" "$(ts)" "$C_RESET" "$exit_code"
  fi
}

# ─────────── Rsync ───────────
rsync_dir() {
  local src="$1"
  local dst="$2"
  local label="$3"

  if [[ ! -d "$SCRIPT_DIR/$src" ]]; then
    err "Нет локальной директории $src"
    return 5
  fi

  local excludes=(
    --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.pyo'
    --exclude='.pytest_cache/' --exclude='.ruff_cache/' --exclude='.mypy_cache/'
    --exclude='.env' --exclude='.env.*'
    --exclude='credentials/' --exclude='recordings/' --exclude='voice_bank/'
    --exclude='.git/' --exclude='.gitignore'
    --exclude='tests/'
    --exclude='*.log' --exclude='logs/'
    --exclude='*.db' --exclude='*.sqlite' --exclude='*.sqlite-journal'
    --exclude='.DS_Store'
    --exclude='*.wav' --exclude='*.m4a' --exclude='*.mp3'
  )
  local extra=()
  (( STRICT_SYNC )) && extra+=(--delete)
  (( VERBOSE )) && extra+=(--progress) || extra+=(--itemize-changes)
  (( DRY_RUN )) && extra+=(--dry-run)

  step "Rsync $label: $src/ → $RSYNC_REMOTE:$REMOTE_APP/$src/"

  local out rc
  for attempt in 1 2; do
    set +e
    out=$(rsync -az --stats \
      --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
      -e "$RSYNC_SSH" \
      "${excludes[@]}" "${extra[@]}" \
      "$SCRIPT_DIR/$src/" "$RSYNC_REMOTE:$REMOTE_APP/$src/" 2>&1)
    rc=$?
    set -e
    if (( rc == 0 )); then
      break
    fi
    if (( attempt == 1 )); then
      warn "rsync упал (rc=$rc), повтор через 5s..."
      sleep 5
    fi
  done
  if (( rc != 0 )); then
    err "rsync упал дважды. Output:"
    printf '%s\n' "$out" | tail -20 >&2
    return $rc
  fi
  (( VERBOSE )) && printf '%s\n' "$out"

  # Парсим "Number of regular files transferred: N"
  local transferred
  transferred=$(printf '%s\n' "$out" | awk -F': ' '/Number of regular files transferred/ {gsub(/,/,"",$2); print $2}')
  transferred="${transferred:-?}"
  RSYNC_TRANSFERRED="$transferred"
  ok "Rsync $label (files changed: $transferred)"
}

rsync_file() {
  local src="$1"
  local dst_path="$2"
  local label="$3"
  local extra=()
  (( VERBOSE )) && extra+=(--progress)
  (( DRY_RUN )) && extra+=(--dry-run)
  run rsync -az "${extra[@]}" -e "$RSYNC_SSH" \
    "$SCRIPT_DIR/$src" "$RSYNC_REMOTE:$dst_path"
  ok "Rsync $label"
}

# ─────────── Backup env.sh ───────────
backup_env_sh() {
  (( BACKUP_ENV )) || return 0
  step "Backup $REMOTE_ENV..."
  ssh_exec "cp '$REMOTE_ENV' '${REMOTE_ENV}.bak_\$(date +%F-%H%M%S)'"
  ok "Backup env.sh"
}

# ─────────── Supervisord helpers ───────────
sup_restart() {
  local svc="$1"
  step "supervisorctl restart $svc"
  ssh_exec "supervisorctl restart '$svc'"
}

sup_status_line() {
  local svc="$1"
  ssh_exec_ro "supervisorctl status '$svc' 2>&1 | head -1"
}

# Возвращает uptime в секундах (по выводу "RUNNING pid X, uptime H:MM:SS")
sup_uptime_sec() {
  local line="$1"
  local uptime
  uptime=$(grep -oE 'uptime +[0-9]+:[0-9]+:[0-9]+' <<<"$line" | awk '{print $2}')
  if [[ -z "$uptime" ]]; then
    # может быть "uptime 0:00:05" или "uptime 1 day, 2:30:15"
    uptime=$(grep -oE 'uptime +[0-9]+[ :]' <<<"$line" | awk '{print $2}')
  fi
  if [[ "$uptime" =~ ^([0-9]+):([0-9]+):([0-9]+)$ ]]; then
    echo $((10#${BASH_REMATCH[1]}*3600 + 10#${BASH_REMATCH[2]}*60 + 10#${BASH_REMATCH[3]}))
  else
    echo 0
  fi
}

# ─────────── Healthchecks ───────────
wait_supervisor_running() {
  local svc="$1"
  local min_uptime="${2:-3}"
  local max_tries=10
  local line status upt
  for ((i=1; i<=max_tries; i++)); do
    line=$(sup_status_line "$svc" 2>/dev/null || echo "")
    status=$(awk '{print $2}' <<<"$line")
    if [[ "$status" == "RUNNING" ]]; then
      upt=$(sup_uptime_sec "$line")
      if (( upt >= min_uptime )); then
        ok "$svc: RUNNING (uptime ${upt}s)"
        return 0
      fi
    fi
    sleep 2
  done
  warn "$svc: не поднялся (last line: $line)"
  ssh_exec_ro "supervisorctl tail -3000 '$svc' 2>/dev/null | tail -30" >&2 || true
  ((WARNINGS_COUNT++)) || true
  return 1
}

http_health() {
  local url="$1"
  local remote="$2"          # 1 = через ssh_exec (expand на VDS), 0 = локально
  local use_api_key="${3:-0}" # 1 = source env.sh и передать X-API-Key из $TELEMOST_SERVICE_API_KEY
  local retries="${4:-5}"
  local interval="${5:-3}"
  local code=""
  local i
  if (( remote )); then
    # Формируем remote-команду. $-переменные внутри одинарных кавычек remote_cmd
    # раскроются на VDS после source env.sh. Через ssh_exec_ro весь remote_cmd
    # уйдёт как один positional arg в ssh и выполнится там.
    local remote_cmd
    if (( use_api_key )); then
      remote_cmd='source '"$REMOTE_ENV"' 2>/dev/null; curl -fsS --max-time 5 -H "X-API-Key: $TELEMOST_SERVICE_API_KEY" -o /dev/null -w "%{http_code}" '"$url"' 2>/dev/null || true'
    else
      remote_cmd='curl -fsS --max-time 5 -o /dev/null -w "%{http_code}" '"$url"' 2>/dev/null || true'
    fi
    for ((i=1; i<=retries; i++)); do
      set +e
      code=$(ssh_exec_ro "$remote_cmd" 2>/dev/null)
      set -e
      [[ "$code" == "200" ]] && { ok "HTTP $url → 200"; return 0; }
      (( i < retries )) && sleep "$interval"
    done
  else
    for ((i=1; i<=retries; i++)); do
      set +e
      code=$(curl -fsS --max-time 5 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || true)
      set -e
      [[ "$code" == "200" ]] && { ok "HTTP $url → 200"; return 0; }
      (( i < retries )) && sleep "$interval"
    done
  fi
  warn "HTTP $url не отвечает (last code=${code:-?})"
  ((WARNINGS_COUNT++)) || true
  return 1
}

# ─────────── Targets ───────────
deploy_bot() {
  step "=== Deploy bot-service ==="
  ((STEPS_COUNT++)) || true
  backup_env_sh
  RSYNC_TRANSFERRED="?"
  rsync_dir "bot-service" "bot-service" "bot-service"
  if (( LAZY )) && [[ "$RSYNC_TRANSFERRED" == "0" ]]; then
    log "Нет изменений — не рестартим (--lazy)"
  else
    sup_restart "$SUP_BOT"
  fi
  (( SKIP_HEALTH )) && return 0
  wait_supervisor_running "$SUP_BOT" 3
  http_health "http://127.0.0.1:${REMOTE_BOT_PORT}/health" 1 1 5 3 || true
}

deploy_tg() {
  step "=== Deploy tg-bot ==="
  ((STEPS_COUNT++)) || true
  backup_env_sh
  RSYNC_TRANSFERRED="?"
  rsync_dir "tg-bot" "tg-bot" "tg-bot"
  if (( LAZY )) && [[ "$RSYNC_TRANSFERRED" == "0" ]]; then
    log "Нет изменений — не рестартим (--lazy)"
  else
    sup_restart "$SUP_TG"
  fi
  (( SKIP_HEALTH )) && return 0
  wait_supervisor_running "$SUP_TG" 3
}

deploy_watcher() {
  step "=== Deploy drive-watcher ==="
  ((STEPS_COUNT++)) || true
  backup_env_sh
  RSYNC_TRANSFERRED="?"
  rsync_dir "tg-bot" "tg-bot" "tg-bot (для watcher)"
  if (( LAZY )) && [[ "$RSYNC_TRANSFERRED" == "0" ]]; then
    log "Нет изменений — не рестартим (--lazy)"
  else
    sup_restart "$SUP_WATCHER"
  fi
  (( SKIP_HEALTH )) && return 0
  wait_supervisor_running "$SUP_WATCHER" 3
}

deploy_transcriber() {
  step "=== Deploy transcriber ==="
  ((STEPS_COUNT++)) || true
  backup_env_sh
  rsync_dir "transcriber-service" "transcriber-service" "transcriber-service"

  step "Sync docker-compose.yml + docker-compose.cpu.yml"
  rsync_file "docker-compose.yml" "$REMOTE_APP/docker-compose.yml" "docker-compose.yml"
  rsync_file "docker-compose.cpu.yml" "$REMOTE_APP/docker-compose.cpu.yml" "docker-compose.cpu.yml"

  local build_flag="--build" build_progress=""
  (( NO_BUILD )) && build_flag=""
  (( VERBOSE )) && build_progress="--progress=plain"

  local build_log="$BUILD_LOG_DIR/transcriber-build-${DEPLOY_ID}.log"
  step "docker compose up -d $build_flag transcriber (log: $build_log)"

  local compose_cmd="cd '$REMOTE_APP' && set -a && source '$REMOTE_ENV' && set +a && docker compose ${COMPOSE_FILES[*]} up -d $build_flag $build_progress $DOCKER_TRANSCRIBER_SVC"

  if (( DRY_RUN )); then
    printf '%s[dry-ssh]%s %s\n' "$C_DIM" "$C_RESET" "$compose_cmd"
  else
    # tee output локально для диагностики
    set +e
    ssh_exec_ro "$compose_cmd" 2>&1 | tee "$build_log"
    local rc=${PIPESTATUS[0]}
    set -e
    if (( rc != 0 )); then
      err "docker compose упал (rc=$rc). Лог: $build_log"
      ((WARNINGS_COUNT++)) || true
      return $rc
    fi
  fi

  (( SKIP_HEALTH )) && return 0

  # transcriber warm-up: больше попыток при --build
  local retries=5 interval=3
  if (( NO_BUILD == 0 )); then
    retries=10; interval=6
  fi
  local url="http://${VDS_SSH_HOST}:${REMOTE_TRANSCRIBER_PORT}/health"
  http_health "$url" 0 0 "$retries" "$interval" || true

  # Dangling-image hint
  if (( NO_BUILD == 0 )) && (( ! DRY_RUN )); then
    local dangling
    dangling=$(ssh_exec_ro "docker images --filter dangling=true --format '{{.ID}}' 2>/dev/null | wc -l" | tr -d '[:space:]')
    if [[ -n "$dangling" && "$dangling" != "0" ]]; then
      log "Висят $dangling dangling image(s). Очистить: ssh $RSYNC_REMOTE 'docker image prune -f'"
    fi
  fi
}

deploy_modal() {
  step "=== Deploy Modal ($MODAL_APP_NAME) ==="
  ((STEPS_COUNT++)) || true

  if (( DRY_RUN )); then
    printf '%s[dry]%s %s deploy %s\n' "$C_DIM" "$C_RESET" "$MODAL_BIN" "$MODAL_APP_FILE"
    return 0
  fi

  local attempt rc
  for attempt in 1 2; do
    set +e
    "$MODAL_BIN" deploy "$SCRIPT_DIR/$MODAL_APP_FILE"
    rc=$?
    set -e
    if (( rc == 0 )); then
      break
    fi
    if (( attempt == 1 )); then
      warn "modal deploy упал (rc=$rc). Повтор через 10s..."
      sleep 10
    fi
  done
  if (( rc != 0 )); then
    err "modal deploy упал дважды (rc=$rc)."
    if [[ "$TARGET" == "all" ]]; then
      if [[ -t 0 ]]; then
        read -r -p "Продолжить без Modal? [y/N]: " ans
        [[ "$ans" =~ ^[Yy]$ ]] || exit $rc
      else
        err "Non-interactive режим — abort."
        exit $rc
      fi
    else
      exit $rc
    fi
  fi
  log "Проверяю что Modal app виден..."
  "$MODAL_BIN" app list 2>/dev/null | grep -E "^\|?\s*${MODAL_APP_NAME}\b" || warn "Modal app '$MODAL_APP_NAME' не виден в 'modal app list' (возможно rate-limit)"
  ok "Modal deploy"
}

deploy_all() {
  step "=== Deploy ALL ==="
  deploy_modal
  deploy_transcriber
  deploy_bot
  deploy_tg
  deploy_watcher
}

cmd_restart() {
  step "=== Restart only: $RESTART_SVC ==="
  case "$RESTART_SVC" in
    bot)         sup_restart "$SUP_BOT"; (( SKIP_HEALTH )) || wait_supervisor_running "$SUP_BOT" 3 ;;
    tg)          sup_restart "$SUP_TG"; (( SKIP_HEALTH )) || wait_supervisor_running "$SUP_TG" 3 ;;
    watcher)     sup_restart "$SUP_WATCHER"; (( SKIP_HEALTH )) || wait_supervisor_running "$SUP_WATCHER" 3 ;;
    transcriber)
      step "docker compose restart transcriber"
      ssh_exec "cd '$REMOTE_APP' && docker compose ${COMPOSE_FILES[*]} restart $DOCKER_TRANSCRIBER_SVC"
      (( SKIP_HEALTH )) || http_health "http://${VDS_SSH_HOST}:${REMOTE_TRANSCRIBER_PORT}/health" 0 0 5 3 || true
      ;;
    *) err "Неизвестный сервис: $RESTART_SVC"; exit 2 ;;
  esac
}

cmd_logs() {
  local path
  case "$LOGS_SVC" in
    bot)         path="$REMOTE_LOGS/bot.log" ;;
    tg)          path="$REMOTE_LOGS/tg-bot.log" ;;
    watcher)     path="$REMOTE_LOGS/drive-watcher.log" ;;
    transcriber) path="$REMOTE_LOGS/transcriber.log" ;;
    *) err "Неизвестный сервис: $LOGS_SVC"; exit 2 ;;
  esac
  step "tail -f $path"
  # shellcheck disable=SC2086
  exec $VDS_SSH "tail -f '$path'"
}

cmd_status() {
  step "=== Status ==="
  ssh_exec_ro "
    echo '--- supervisorctl status ---'
    supervisorctl status
    echo
    echo '--- docker ps (telemost) ---'
    docker ps --filter name=telemost --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
    echo
    echo '--- disk / ram ---'
    df -h /
    echo
    free -h
  "
}

# ─────────── Main ───────────
main() {
  parse_args "$@"

  preflight_local
  load_env_deploy

  trap cleanup EXIT
  trap 'err "Прервано сигналом"; exit 130' INT TERM

  acquire_lock

  case "$TARGET" in
    status)
      preflight_ssh
      cmd_status
      ;;
    logs)
      preflight_ssh
      cmd_logs
      ;;
    restart)
      preflight_ssh
      preflight_remote
      cmd_restart
      ;;
    modal)
      preflight_modal
      deploy_modal
      ;;
    bot)
      preflight_ssh
      preflight_remote
      deploy_bot
      ;;
    tg)
      preflight_ssh
      preflight_remote
      deploy_tg
      ;;
    watcher)
      preflight_ssh
      preflight_remote
      deploy_watcher
      ;;
    transcriber)
      preflight_ssh
      preflight_remote
      deploy_transcriber
      ;;
    all)
      preflight_modal
      preflight_ssh
      preflight_remote
      deploy_all
      ;;
  esac
}

main "$@"
