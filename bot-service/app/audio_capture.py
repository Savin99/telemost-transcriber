import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)


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

    async def start(self):
        """Запуск записи аудио."""
        env = {
            **os.environ,
            "DISPLAY": os.getenv("DISPLAY", ":99"),
            "XDG_RUNTIME_DIR": os.getenv("XDG_RUNTIME_DIR", "/run/user/0"),
        }

        args = [
            "ffmpeg",
            "-y",
            "-f", "pulse",
            "-i", "virtual_output.monitor",
            "-ac", "1",
            "-ar", "16000",
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
