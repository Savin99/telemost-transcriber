import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)


class AudioCapture:
    """Захват аудио встречи через PulseAudio default sink monitor + FFmpeg.

    Подход из terra-clan/AI_meet_assistant:
    - FFmpeg пишет из virtual_output.monitor (default sink)
    - Не нужно перенаправлять sink-inputs
    - Chrome автоматически выводит звук в default sink
    """

    def __init__(self, output_path: str, session_id: str = "default"):
        self.output_path = output_path
        self.session_id = session_id
        self._ffmpeg_process: asyncio.subprocess.Process | None = None
        self._start_time: float | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self._start_time is None:
            return None
        return time.time() - self._start_time

    async def start(self, page=None):
        """Запуск записи аудио через FFmpeg из PulseAudio monitor."""
        self._start_time = time.time()
        await self._start_ffmpeg()

    async def _start_ffmpeg(self):
        """Запустить FFmpeg для записи из PulseAudio default sink monitor."""
        # Используем virtual_output.monitor — default sink, настроенный в entrypoint.sh
        monitor_source = os.getenv("PULSE_MONITOR", "virtual_output.monitor")

        user_id = os.getuid()
        xdg_runtime = os.getenv("XDG_RUNTIME_DIR", f"/run/user/{user_id}")

        env = {
            **os.environ,
            "DISPLAY": os.getenv("DISPLAY", ":99"),
            "XDG_RUNTIME_DIR": xdg_runtime,
        }

        args = [
            "ffmpeg", "-y", "-loglevel", "warning",
            "-f", "pulse",
            "-i", monitor_source,
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            self.output_path,
        ]

        try:
            self._ffmpeg_process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.sleep(2)
            if self._ffmpeg_process.returncode is not None:
                stderr = await self._ffmpeg_process.stderr.read()
                logger.error(
                    "[%s] FFmpeg failed to start: %s",
                    self.session_id, stderr.decode(),
                )
                self._ffmpeg_process = None
                raise RuntimeError("FFmpeg failed to start — check PulseAudio")
            else:
                logger.info(
                    "[%s] FFmpeg recording started (pid=%d) -> %s",
                    self.session_id, self._ffmpeg_process.pid, self.output_path,
                )
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("[%s] FFmpeg start failed: %s", self.session_id, e)
            raise

    async def stop(self) -> float | None:
        """Остановка записи. Возвращает длительность в секундах."""
        duration = self.duration_seconds
        await self._stop_ffmpeg()
        self._start_time = None
        return duration

    async def _stop_ffmpeg(self):
        """Остановить FFmpeg."""
        if self._ffmpeg_process is None:
            return

        logger.info("[%s] Stopping FFmpeg...", self.session_id)

        try:
            if self._ffmpeg_process.stdin:
                self._ffmpeg_process.stdin.write(b"q")
                await self._ffmpeg_process.stdin.drain()
                self._ffmpeg_process.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

        try:
            await asyncio.wait_for(self._ffmpeg_process.wait(), timeout=5)
        except asyncio.TimeoutError:
            self._ffmpeg_process.terminate()
            try:
                await asyncio.wait_for(self._ffmpeg_process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self._ffmpeg_process.kill()
                await self._ffmpeg_process.wait()

        self._ffmpeg_process = None
        logger.info("[%s] FFmpeg stopped", self.session_id)
