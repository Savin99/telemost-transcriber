#!/bin/bash
set -e

# --- Виртуальный дисплей ---
echo "Starting Xvfb..."
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x720x24 -ac &
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
pulseaudio -D --exit-idle-time=-1 2>&1
sleep 2

# --- Виртуальный аудио-sink ---
if pgrep -x "pulseaudio" > /dev/null; then
    echo "PulseAudio is running"
    pactl load-module module-null-sink \
        sink_name=virtual_output \
        sink_properties=device.description="Virtual_Output"
    pactl set-default-sink virtual_output
    echo "Set virtual_output as default sink"
else
    echo "ERROR: PulseAudio failed to start"
    exit 1
fi

# --- PulseAudio watchdog (фоновый процесс) ---
# Перезапускает PulseAudio если он упал
(
    while true; do
        sleep 10
        if ! pgrep -x pulseaudio > /dev/null 2>&1; then
            echo "[watchdog] PulseAudio died! Restarting..."
            pulseaudio -D --exit-idle-time=-1 2>&1 || true
            sleep 2
            if pgrep -x pulseaudio > /dev/null 2>&1; then
                pactl load-module module-null-sink \
                    sink_name=virtual_output \
                    sink_properties=device.description="Virtual_Output" 2>/dev/null || true
                pactl set-default-sink virtual_output 2>/dev/null || true
                echo "[watchdog] PulseAudio restarted OK"
            fi
        fi
    done
) &
echo "PulseAudio watchdog started"

# --- FastAPI ---
echo "Starting FastAPI..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
