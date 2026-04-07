import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TranscribedSegment:
    speaker: Optional[str]
    start: float
    end: float
    text: str


class TranscriberPipeline:
    """WhisperX: транскрипция + word-level alignment + диаризация."""

    def __init__(self, device: str = "auto"):
        if device == "auto":
            import torch
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.compute_type = "float16" if self.device == "cuda" else "int8"
        self._asr_model = None
        self._diarize_model = None

    def preload(self):
        """Предзагрузка моделей при старте сервиса."""
        logger.info("Preloading WhisperX large-v3 on %s...", self.device)
        _ = self.asr_model
        logger.info("ASR model loaded")

        hf_token = os.getenv("HF_TOKEN")
        if hf_token:
            logger.info("Preloading diarization pipeline...")
            _ = self.diarize_model
            logger.info("Diarization pipeline loaded")
        else:
            logger.warning("HF_TOKEN not set, diarization will be skipped")

    @property
    def asr_model(self):
        if self._asr_model is None:
            import whisperx
            self._asr_model = whisperx.load_model(
                "large-v3",
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._asr_model

    @property
    def diarize_model(self):
        if self._diarize_model is None:
            hf_token = os.getenv("HF_TOKEN")
            if not hf_token:
                return None
            import whisperx
            self._diarize_model = whisperx.DiarizationPipeline(
                use_auth_token=hf_token,
                device=self.device,
            )
        return self._diarize_model

    def transcribe(self, audio_path: str) -> list[TranscribedSegment]:
        """Транскрипция + alignment + диаризация."""
        import whisperx

        logger.info("Transcribing %s", audio_path)

        # 1. Загрузка аудио
        audio = whisperx.load_audio(audio_path)

        # 2. Транскрипция
        result = self.asr_model.transcribe(audio, batch_size=16)
        logger.info("ASR produced %d segments", len(result["segments"]))

        # 3. Word-level alignment для русского
        model_a, metadata = whisperx.load_align_model(
            language_code="ru", device=self.device
        )
        result = whisperx.align(
            result["segments"], model_a, metadata, audio, device=self.device
        )

        # 4. Диаризация (если HF_TOKEN доступен)
        diarize_pipeline = self.diarize_model
        if diarize_pipeline is not None:
            logger.info("Running diarization...")
            diarize_segments = diarize_pipeline(audio)
            result = whisperx.assign_word_speakers(diarize_segments, result)

        # 5. Формирование результата
        segments = []
        seen_speakers = {}
        for seg in result["segments"]:
            speaker_raw = seg.get("speaker")
            speaker = None
            if speaker_raw:
                if speaker_raw not in seen_speakers:
                    seen_speakers[speaker_raw] = f"Спикер {len(seen_speakers) + 1}"
                speaker = seen_speakers[speaker_raw]

            segments.append(TranscribedSegment(
                speaker=speaker,
                start=seg["start"],
                end=seg["end"],
                text=seg["text"].strip(),
            ))

        logger.info(
            "Transcription complete: %d segments, %d speakers",
            len(segments),
            len(seen_speakers),
        )
        return segments
