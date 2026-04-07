import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)


async def _exec(cmd: str) -> str:
    """Run a shell command and return stdout."""
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()


class AudioCapture:
    """Захват аудио из PulseAudio через FFmpeg."""

    def __init__(self, output_path: str):
        self.output_path = output_path
        self._process: asyncio.subprocess.Process | None = None
        self._start_time: float | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self._start_time is None:
            return None
        return time.time() - self._start_time

    async def _ensure_pulse_sink(self):
        """Проверить наличие virtual_output.monitor, восстановить если нет."""
        check = await _exec("pactl list sources short 2>/dev/null | grep virtual_output.monitor")
        if check:
            logger.info("PulseAudio virtual_output.monitor is available")
            return

        logger.warning("virtual_output.monitor not found, attempting recovery...")
        await _exec("pulseaudio --kill 2>/dev/null || true")
        await asyncio.sleep(1)
        await _exec("pulseaudio -D --exit-idle-time=-1 --log-level=info 2>&1")
        await asyncio.sleep(2)
        await _exec(
            'pactl load-module module-null-sink '
            'sink_name=virtual_output '
            'sink_properties=device.description="Virtual_Output"'
        )
        await _exec("pactl set-default-sink virtual_output")

        # Verify
        check = await _exec("pactl list sources short 2>/dev/null | grep virtual_output.monitor")
        if not check:
            raise RuntimeError("Failed to create virtual_output.monitor PulseAudio source")
        logger.info("PulseAudio virtual_output.monitor recovered")

    async def start(self):
        """Запуск записи аудио."""
        await self._ensure_pulse_sink()

        user_id = os.getuid()
        xdg_runtime = os.getenv("XDG_RUNTIME_DIR", f"/run/user/{user_id}")

        env = {
            **os.environ,
            "DISPLAY": os.getenv("DISPLAY", ":99"),
            "XDG_RUNTIME_DIR": xdg_runtime,
        }

        args = [
            "ffmpeg",
            "-y",
            "-loglevel", "info",
            "-f", "pulse",
            "-ac", "1",
            "-ar", "16000",
            "-i", "virtual_output.monitor",
            "-c:a", "pcm_s16le",
            self.output_path,
        ]

        logger.info("Starting FFmpeg: %s", " ".join(args))

        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._start_time = time.time()

        # Ждём 2 секунды, проверяем что процесс не упал
        await asyncio.sleep(2)
        if self._process.returncode is not None:
            stderr = await self._process.stderr.read()
            raise RuntimeError(f"FFmpeg exited early: {stderr.decode()}")

        logger.info("FFmpeg recording started (pid=%d)", self._process.pid)

    async def stop(self) -> float | None:
        """Остановка записи. Возвращает длительность в секундах."""
        if self._process is None:
            return None

        duration = self.duration_seconds
        logger.info("Stopping FFmpeg (duration=%.1fs)...", duration or 0)

        # Graceful stop: отправляем 'q' в stdin
        try:
            if self._process.stdin:
                self._process.stdin.write(b"q\n")
                await self._process.stdin.drain()
                self._process.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

        # Ждём завершения до 15 секунд
        try:
            await asyncio.wait_for(self._process.wait(), timeout=15)
        except asyncio.TimeoutError:
            logger.warning("FFmpeg did not exit in 15s, sending SIGTERM")
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.error("FFmpeg still alive, sending SIGKILL")
                self._process.kill()
                await self._process.wait()

        logger.info("FFmpeg stopped")
        self._process = None
        self._start_time = None
        return duration
