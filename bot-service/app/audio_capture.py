import asyncio
import base64
import logging
import os
import time

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# JS-код для захвата аудио через Web Audio API
# Перехватывает ВСЕ RTCPeerConnection треки и записывает через MediaRecorder
CAPTURE_JS = """
() => {
    return new Promise((resolve) => {
        // Подменяем RTCPeerConnection чтобы ловить входящие аудио-треки
        const audioTracks = [];
        const origAddTrack = RTCPeerConnection.prototype.addTrack;
        const origSetRemote = RTCPeerConnection.prototype.setRemoteDescription;

        // Ждём когда появятся remote аудио треки
        const checkInterval = setInterval(() => {
            const audios = document.querySelectorAll('audio, video');
            audios.forEach(el => {
                if (el.srcObject && !el._captured) {
                    el._captured = true;
                    el.srcObject.getAudioTracks().forEach(track => {
                        if (track.kind === 'audio') {
                            audioTracks.push(track);
                        }
                    });
                }
            });

            if (audioTracks.length > 0) {
                clearInterval(checkInterval);

                // Создаём AudioContext для микширования
                const ctx = new AudioContext({ sampleRate: 16000 });
                const dest = ctx.createMediaStreamDestination();

                audioTracks.forEach(track => {
                    const stream = new MediaStream([track]);
                    const source = ctx.createMediaStreamSource(stream);
                    source.connect(dest);
                });

                // MediaRecorder для записи
                const recorder = new MediaRecorder(dest.stream, {
                    mimeType: 'audio/webm;codecs=opus'
                });
                const chunks = [];
                recorder.ondataavailable = (e) => {
                    if (e.data.size > 0) chunks.push(e.data);
                };
                recorder.start(1000); // chunk каждую секунду

                window._audioRecorder = recorder;
                window._audioChunks = chunks;
                window._audioCtx = ctx;

                resolve('recording');
            }
        }, 500);

        // Таймаут 30 секунд
        setTimeout(() => {
            clearInterval(checkInterval);
            if (audioTracks.length === 0) {
                resolve('no_audio_tracks');
            }
        }, 30000);
    });
}
"""

STOP_AND_GET_JS = """
() => {
    return new Promise((resolve) => {
        const recorder = window._audioRecorder;
        const chunks = window._audioChunks;

        if (!recorder) {
            resolve(null);
            return;
        }

        recorder.onstop = async () => {
            const blob = new Blob(chunks, { type: 'audio/webm' });
            const buffer = await blob.arrayBuffer();
            const base64 = btoa(String.fromCharCode(...new Uint8Array(buffer)));

            if (window._audioCtx) {
                window._audioCtx.close();
            }

            resolve(base64);
        };

        recorder.stop();
    });
}
"""


class AudioCapture:
    """Захват аудио встречи через JS MediaRecorder + FFmpeg fallback."""

    def __init__(self, output_path: str):
        self.output_path = output_path
        self._page: Page | None = None
        self._ffmpeg_process: asyncio.subprocess.Process | None = None
        self._start_time: float | None = None
        self._js_capture_active = False

    @property
    def duration_seconds(self) -> float | None:
        if self._start_time is None:
            return None
        return time.time() - self._start_time

    async def start(self, page: Page = None):
        """Запуск записи аудио."""
        self._start_time = time.time()
        self._page = page

        if page:
            await self._start_js_capture(page)

        # Всегда запускаем FFmpeg как fallback
        await self._start_ffmpeg()

    async def _start_js_capture(self, page: Page):
        """Запустить захват через JavaScript MediaRecorder."""
        try:
            result = await page.evaluate(CAPTURE_JS)
            if result == 'recording':
                self._js_capture_active = True
                logger.info("JS audio capture started (MediaRecorder)")
            else:
                logger.warning("JS audio capture: %s", result)
        except Exception as e:
            logger.warning("JS audio capture failed: %s", e)

    async def _start_ffmpeg(self):
        """Запустить FFmpeg для записи из PulseAudio (fallback)."""
        user_id = os.getuid()
        xdg_runtime = os.getenv("XDG_RUNTIME_DIR", f"/run/user/{user_id}")

        env = {
            **os.environ,
            "DISPLAY": os.getenv("DISPLAY", ":99"),
            "XDG_RUNTIME_DIR": xdg_runtime,
        }

        args = [
            "ffmpeg", "-y", "-loglevel", "warning",
            "-f", "pulse", "-ac", "1", "-ar", "16000",
            "-i", "virtual_output.monitor",
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
                logger.warning("FFmpeg fallback failed: %s", stderr.decode())
                self._ffmpeg_process = None
            else:
                logger.info("FFmpeg fallback recording started (pid=%d)", self._ffmpeg_process.pid)
        except Exception as e:
            logger.warning("FFmpeg fallback start failed: %s", e)

    async def stop(self) -> float | None:
        """Остановка записи. Возвращает длительность в секундах."""
        duration = self.duration_seconds

        # 1. Попробовать получить аудио из JS
        js_saved = False
        if self._js_capture_active and self._page and not self._page.is_closed():
            js_saved = await self._stop_js_capture()

        # 2. Остановить FFmpeg
        await self._stop_ffmpeg()

        # Если JS захват дал данные, конвертировать webm → wav
        if js_saved:
            webm_path = self.output_path + ".webm"
            if os.path.exists(webm_path) and os.path.getsize(webm_path) > 100:
                await self._convert_webm_to_wav(webm_path)

        self._start_time = None
        return duration

    async def _stop_js_capture(self) -> bool:
        """Остановить JS MediaRecorder и сохранить аудио."""
        try:
            data_b64 = await asyncio.wait_for(
                self._page.evaluate(STOP_AND_GET_JS), timeout=15
            )
            if data_b64:
                webm_path = self.output_path + ".webm"
                data = base64.b64decode(data_b64)
                with open(webm_path, "wb") as f:
                    f.write(data)
                logger.info("JS audio saved: %s (%d bytes)", webm_path, len(data))
                return True
            else:
                logger.warning("JS audio capture returned no data")
        except Exception as e:
            logger.warning("JS audio stop failed: %s", e)
        return False

    async def _convert_webm_to_wav(self, webm_path: str):
        """Конвертировать webm в wav через FFmpeg."""
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", webm_path,
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            self.output_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0:
            logger.info("Converted webm → wav: %s", self.output_path)
            os.remove(webm_path)
        else:
            logger.warning("webm→wav conversion failed: %s", stderr.decode())

    async def _stop_ffmpeg(self):
        """Остановить FFmpeg."""
        if self._ffmpeg_process is None:
            return

        logger.info("Stopping FFmpeg...")
        try:
            if self._ffmpeg_process.stdin:
                self._ffmpeg_process.stdin.write(b"q\n")
                await self._ffmpeg_process.stdin.drain()
                self._ffmpeg_process.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

        try:
            await asyncio.wait_for(self._ffmpeg_process.wait(), timeout=10)
        except asyncio.TimeoutError:
            self._ffmpeg_process.terminate()
            try:
                await asyncio.wait_for(self._ffmpeg_process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._ffmpeg_process.kill()
                await self._ffmpeg_process.wait()

        self._ffmpeg_process = None
        logger.info("FFmpeg stopped")
