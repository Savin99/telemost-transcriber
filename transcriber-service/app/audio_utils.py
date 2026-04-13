import contextlib
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_MIN_SEGMENT_SECONDS = 1.0
SUPPORTED_AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".webm",
    ".mp4",
    ".ogg",
    ".flac",
    ".aac",
}


@dataclass
class NormalizedAudioFile:
    source_path: str
    normalized_path: str
    temp_dir: str

    def cleanup(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cleanup()


def default_voice_bank_dir() -> str:
    explicit = os.getenv("VOICE_BANK_DIR")
    if explicit:
        return explicit

    for candidate in ("/workspace/voice_bank", "/app/voice_bank"):
        parent = os.path.dirname(candidate)
        if os.path.isdir(parent):
            return candidate

    return str(Path.cwd() / "voice_bank")


def default_recordings_dir() -> str:
    explicit = os.getenv("RECORDINGS_DIR")
    if explicit:
        return explicit

    for candidate in ("/workspace/recordings", "/app/recordings"):
        if os.path.isdir(candidate):
            return candidate

    return str(Path.cwd() / "recordings")


def l2_normalize(vector: np.ndarray) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = np.linalg.norm(array)
    if norm == 0:
        raise ValueError("Cannot normalize a zero vector")
    return array / norm


def slice_waveform(
    waveform: np.ndarray,
    sample_rate: int,
    start: float,
    end: float,
) -> np.ndarray:
    start_idx = max(0, int(round(start * sample_rate)))
    end_idx = min(len(waveform), int(round(end * sample_rate)))
    if end_idx <= start_idx:
        return np.asarray([], dtype=np.float32)
    return np.asarray(waveform[start_idx:end_idx], dtype=np.float32)


def load_wav_mono(audio_path: str) -> tuple[np.ndarray, int]:
    with wave.open(audio_path, "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        n_frames = wav_file.getnframes()
        frames = wav_file.readframes(n_frames)

    if sample_width != 2:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")

    waveform = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        waveform = waveform.reshape(-1, channels).mean(axis=1)

    return waveform, sample_rate


def write_wav_mono(audio_path: str | Path, waveform: np.ndarray, sample_rate: int):
    output_path = Path(audio_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.asarray(waveform, dtype=np.float32).reshape(-1)
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def make_meeting_key(audio_path: str) -> str:
    resolved = os.path.abspath(audio_path)
    stat_result = os.stat(audio_path)
    fingerprint = hashlib.sha1(
        f"{resolved}|{stat_result.st_size}|{stat_result.st_mtime_ns}".encode("utf-8")
    ).hexdigest()[:16]
    stem = Path(audio_path).stem
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "meeting"
    return f"{slug}-{fingerprint}"


def _looks_like_normalized_wav(audio_path: str) -> bool:
    try:
        with wave.open(audio_path, "rb") as wav_file:
            return (
                wav_file.getframerate() == DEFAULT_SAMPLE_RATE
                and wav_file.getnchannels() == 1
                and wav_file.getsampwidth() == 2
            )
    except (wave.Error, FileNotFoundError):
        return False


def _materialize_normalized_audio(source_path: str, normalized_path: str):
    if _looks_like_normalized_wav(source_path):
        shutil.copyfile(source_path, normalized_path)
        return

    ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
    command = [
        ffmpeg_bin,
        "-y",
        "-i",
        source_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(DEFAULT_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        normalized_path,
    ]
    logger.info("Normalizing audio with ffmpeg: %s -> %s", source_path, normalized_path)
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed to normalize audio: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


@contextlib.contextmanager
def normalized_audio_file(audio_path: str):
    temp_dir = tempfile.mkdtemp(prefix="normalized_audio_")
    normalized_path = os.path.join(temp_dir, "audio.wav")
    _materialize_normalized_audio(audio_path, normalized_path)
    normalized_audio = NormalizedAudioFile(
        source_path=audio_path,
        normalized_path=normalized_path,
        temp_dir=temp_dir,
    )
    try:
        yield normalized_audio
    finally:
        normalized_audio.cleanup()
