#!/bin/bash
set -e

# --- Виртуальный дисплей ---
echo "Starting Xvfb..."
Xvfb :99 -screen 0 1920x1080x24 -ac &
export DISPLAY=:99
sleep 1

# --- PulseAudio (user mode, работает и от root) ---
echo "Starting PulseAudio..."
USER_ID=$(id -u)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/${USER_ID}}
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

pulseaudio --kill 2>/dev/null || true
sleep 1
pulseaudio -D --exit-idle-time=-1 --log-level=info 2>&1
sleep 5

# --- Виртуальный аудио-sink ---
if pgrep -x "pulseaudio" > /dev/null; then
    echo "PulseAudio is running"

    # Null-sink — виртуальный динамик, в который Chrome будет воспроизводить аудио
    SINK_ID=$(pactl load-module module-null-sink \
        sink_name=virtual_output \
        sink_properties=device.description="Virtual_Output" 2>&1)
    echo "Loaded null sink (ID: $SINK_ID)"

    # Устанавливаем как sink по умолчанию
    pactl set-default-sink virtual_output
    echo "Set virtual_output as default sink"

    # Проверяем что monitor-source доступен для FFmpeg
    if pactl list sources short | grep -q "virtual_output.monitor"; then
        echo "Monitor source virtual_output.monitor is available for ffmpeg"
    else
        echo "WARNING: Monitor source not found!"
    fi
else
    echo "ERROR: PulseAudio failed to start"
    exit 1
fi

# --- FastAPI ---
echo "Starting FastAPI..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
