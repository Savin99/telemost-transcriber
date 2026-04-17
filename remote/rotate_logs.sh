#!/usr/bin/env bash
# rotate_logs.sh — запуск logrotate для telemost-transcriber.
# Вызывается из cron (см. bootstrap_vast_host.sh).
set -euo pipefail

CONF="/workspace/telemost-transcriber/remote/logrotate.conf"
STATE="/workspace/.logrotate.state"

if ! command -v logrotate >/dev/null 2>&1; then
    echo "logrotate не установлен" >&2
    exit 1
fi

if [ ! -f "$CONF" ]; then
    echo "Не найден конфиг: $CONF" >&2
    exit 1
fi

logrotate --state "$STATE" "$CONF"
