import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)


class AudioCapture:
    """Захват аудио встречи через PulseAudio parec + ffmpeg конвертация.

    FFmpeg не может читать из PulseAudio monitor (выдаёт тишину).
    parec работает корректно. Записываем raw PCM через parec,
    потом конвертируем в WAV.
    """

    def __init__(self, output_path: str, session_id: str = "default"):
        self.output_path = output_path
        self.session_id = session_id
        self._parec_process: asyncio.subprocess.Process | None = None
        self._raw_path: str = output_path + ".raw"
        self._start_time: float | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self._start_time is None:
            return None
        return time.time() - self._start_time

    async def start(self, page=None):
        """Запуск записи аудио через parec."""
        self._start_time = time.time()
        await self._start_parec()

    async def _start_parec(self):
        """Запустить parec для записи из PulseAudio monitor."""
        monitor_source = os.getenv("PULSE_MONITOR", "virtual_output.monitor")

        user_id = os.getuid()
        xdg_runtime = os.getenv("XDG_RUNTIME_DIR", f"/run/user/{user_id}")

        env = {
            **os.environ,
            "DISPLAY": os.getenv("DISPLAY", ":99"),
            "XDG_RUNTIME_DIR": xdg_runtime,
        }
        if "PULSE_SERVER" not in env:
            env["PULSE_SERVER"] = f"unix:{xdg_runtime}/pulse/native"

        args = [
            "parec",
            f"--device={monitor_source}",
            "--rate=16000",
            "--channels=1",
            "--format=s16le",
            "--file-format=wav",
            self.output_path,
        ]

        try:
            self._parec_process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.sleep(1)
            if self._parec_process.returncode is not None:
                stderr = await self._parec_process.stderr.read()
                logger.error(
                    "[%s] parec failed to start: %s",
                    self.session_id, stderr.decode(),
                )
                self._parec_process = None
                raise RuntimeError("parec failed to start — check PulseAudio")
            else:
                logger.info(
                    "[%s] parec recording started (pid=%d) -> %s",
                    self.session_id, self._parec_process.pid, self.output_path,
                )
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("[%s] parec start failed: %s", self.session_id, e)
            raise

    async def stop(self) -> float | None:
        """Остановка записи. Возвращает длительность в секундах."""
        duration = self.duration_seconds
        await self._stop_parec()
        self._start_time = None
        return duration

    async def _stop_parec(self):
        """Остановить parec."""
        if self._parec_process is None:
            return

        logger.info("[%s] Stopping parec...", self.session_id)

        if self._parec_process.returncode is not None:
            logger.warning(
                "[%s] parec already exited (code=%d)",
                self.session_id, self._parec_process.returncode,
            )
            self._parec_process = None
            return

        # SIGTERM для graceful stop
        self._parec_process.terminate()
        try:
            await asyncio.wait_for(self._parec_process.wait(), timeout=5)
        except asyncio.TimeoutError:
            self._parec_process.kill()
            await self._parec_process.wait()

        self._parec_process = None
        logger.info("[%s] parec stopped", self.session_id)
