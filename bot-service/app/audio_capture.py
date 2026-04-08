import asyncio
import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)


class AudioCapture:
    """Захват аудио встречи через PulseAudio per-session sink + FFmpeg.

    Подход из hh-recruiter-bot:
    1. Создаём отдельный PulseAudio null-sink для сессии
    2. Перенаправляем аудио-выход Chrome на этот sink
    3. FFmpeg пишет из sink.monitor
    """

    def __init__(self, output_path: str, session_id: str = "default"):
        self.output_path = output_path
        self.session_id = session_id
        self._ffmpeg_process: asyncio.subprocess.Process | None = None
        self._start_time: float | None = None
        self._sink_name: str | None = None
        self._sink_module_index: int | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self._start_time is None:
            return None
        return time.time() - self._start_time

    def _create_session_sink(self):
        """Создать per-session PulseAudio null-sink."""
        sink_name = f"sink_{self.session_id.replace('-', '_')}"
        try:
            result = subprocess.run(
                [
                    "pactl", "load-module", "module-null-sink",
                    f"sink_name={sink_name}",
                    f'sink_properties=device.description="{sink_name}"',
                ],
                capture_output=True, text=True, timeout=5,
            )
            module_index = int(result.stdout.strip())
            self._sink_name = sink_name
            self._sink_module_index = module_index
            logger.info(
                "[%s] Created PulseAudio sink: %s (module %d)",
                self.session_id, sink_name, module_index,
            )
        except Exception as e:
            logger.error("[%s] Failed to create PulseAudio sink: %s", self.session_id, e)
            raise RuntimeError("PulseAudio sink creation failed") from e

    def _move_chrome_to_sink(self):
        """Перенаправить аудио-выход Chrome на наш sink. Возвращает кол-во перемещённых."""
        moved = 0
        try:
            result = subprocess.run(
                ["pactl", "list", "sink-inputs", "short"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                input_index = parts[0]
                try:
                    subprocess.run(
                        ["pactl", "move-sink-input", input_index, self._sink_name],
                        check=True, timeout=5,
                    )
                    moved += 1
                    logger.info(
                        "[%s] Moved sink-input %s -> %s",
                        self.session_id, input_index, self._sink_name,
                    )
                except Exception:
                    pass
        except Exception:
            pass

        if moved == 0:
            logger.debug("[%s] No sink-inputs found yet", self.session_id)
        return moved

    def _destroy_session_sink(self):
        """Удалить per-session PulseAudio sink."""
        if self._sink_module_index is not None:
            try:
                subprocess.run(
                    ["pactl", "unload-module", str(self._sink_module_index)],
                    check=False, timeout=5,
                )
                logger.info(
                    "[%s] Destroyed PulseAudio sink (module %d)",
                    self.session_id, self._sink_module_index,
                )
            except Exception:
                logger.info("[%s] Sink already destroyed or not found", self.session_id)
            self._sink_module_index = None
            self._sink_name = None

    async def start(self, page=None):
        """Запуск записи аудио через PulseAudio + FFmpeg."""
        self._start_time = time.time()

        # 1. Создаём per-session PulseAudio sink
        self._create_session_sink()

        # 2. Ждём пока Chrome создаст sink-input (до 10 секунд)
        #    Chrome создаёт аудио-выход с задержкой после входа в WebRTC-комнату
        for attempt in range(10):
            moved = self._move_chrome_to_sink()
            if moved > 0:
                break
            await asyncio.sleep(1)

        # 3. Запускаем FFmpeg для записи из sink.monitor
        await self._start_ffmpeg()

    async def _start_ffmpeg(self):
        """Запустить FFmpeg для записи из per-session PulseAudio sink."""
        monitor_source = f"{self._sink_name}.monitor"

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
                raise RuntimeError("FFmpeg failed to start — check PulseAudio configuration")
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

        # 1. Остановить FFmpeg
        await self._stop_ffmpeg()

        # 2. Удалить PulseAudio sink
        self._destroy_session_sink()

        self._start_time = None
        return duration

    async def _stop_ffmpeg(self):
        """Остановить FFmpeg."""
        if self._ffmpeg_process is None:
            return

        logger.info("[%s] Stopping FFmpeg...", self.session_id)

        # Отправляем 'q' для graceful stop
        try:
            if self._ffmpeg_process.stdin:
                self._ffmpeg_process.stdin.write(b"q")
                await self._ffmpeg_process.stdin.drain()
                self._ffmpeg_process.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

        # Ждём завершения до 5 секунд
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
