import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from .audio_utils import (
    default_voice_bank_dir,
    l2_normalize,
    load_wav_mono,
    normalized_audio_file,
    slice_waveform,
)
from .speaker_identifier import IdentificationResult, SpeakerIdentifier
from .speaker_refiner import env_bool
from .voice_bank import VoiceBank

logger = logging.getLogger(__name__)


REVIEW_NAME_PREFIXES = (
    "это тоже ",
    "это ",
    "тоже ",
    "тот же ",
    "та же ",
    "same ",
    "also ",
)

SHORT_REPLY_RE = re.compile(
    r"^(?:да|нет|угу|ага|ок|окей|конечно|верно|точно|ну да)[.!?…]*$",
    re.IGNORECASE,
)
MIN_WORD_SPEAKER_CHUNK_WORDS = 2
MIN_WORD_SPEAKER_CHUNK_SECONDS = 0.35
MIN_WORD_SPEAKER_CHUNK_CHARS = 5


def normalize_review_speaker_name(name: str) -> str:
    normalized = " ".join(str(name).strip().split())
    if not normalized:
        raise ValueError("Speaker name cannot be empty")

    candidate = normalized
    changed = True
    while changed:
        changed = False
        lowered = candidate.casefold()
        for prefix in REVIEW_NAME_PREFIXES:
            if lowered.startswith(prefix):
                stripped = candidate[len(prefix) :].strip(" \t-:,!?")
                if stripped:
                    candidate = stripped
                    changed = True
                break

    candidate = candidate.strip(" \t-:,!?")
    if not candidate:
        raise ValueError("Speaker name cannot be empty")
    return candidate


@dataclass
class TranscribedSegment:
    speaker: Optional[str]
    start: float
    end: float
    text: str


@dataclass
class AiStatus:
    speaker_refinement: str = "disabled"
    transcript_refinement: str = "disabled"


@dataclass
class SpeakerHint:
    """Подсказка для идентификации/авто-энроллмента.

    Поле ``role`` — произвольный идентификатор роли (``recruiter`` / ``candidate``),
    используется только для маппинга в результате. ``person_id`` прокидывается
    из потребителя, чтобы вернуть его в ``enrolled_voiceprints``.
    """

    display_name: str
    enrolled: bool
    voice_bank_id: Optional[str] = None
    role: Optional[str] = None
    person_id: Optional[int] = None


@dataclass
class EnrolledVoiceprint:
    voice_bank_id: str
    display_name: str
    person_id: Optional[int] = None
    role: Optional[str] = None


@dataclass
class TranscribeResult:
    segments: list[TranscribedSegment]
    ai_status: AiStatus = field(default_factory=AiStatus)
    enrolled_voiceprints: list[EnrolledVoiceprint] = field(default_factory=list)
    speaker_roles: dict[str, str] = field(default_factory=dict)


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
        self._speaker_identifier = None
        self._speaker_refiner = None
        self._transcript_refiner = None
        self._voice_bank = None
        self.voice_match_threshold = float(os.getenv("VOICE_MATCH_THRESHOLD", "0.40"))
        self.min_embedding_segment_seconds = float(
            os.getenv("MIN_EMBEDDING_SEGMENT_SEC", "1.0")
        )
        asr_language = os.getenv("ASR_LANGUAGE", "ru").strip().lower()
        self.asr_language = None if asr_language in {"", "auto"} else asr_language
        self.speaker_llm_refinement_enabled = env_bool(
            "SPEAKER_LLM_REFINEMENT_ENABLED",
            False,
        )
        self.transcript_llm_refinement_enabled = env_bool(
            "TRANSCRIPT_LLM_REFINEMENT_ENABLED",
            False,
        )

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
    def speaker_identifier(self):
        if self._speaker_identifier is None:
            self._speaker_identifier = SpeakerIdentifier(device=self.device)
        return self._speaker_identifier

    @property
    def voice_bank(self):
        if self._voice_bank is None:
            self._voice_bank = VoiceBank(
                default_voice_bank_dir(),
                speaker_identifier=self.speaker_identifier,
                min_segment_seconds=self.min_embedding_segment_seconds,
            )
        return self._voice_bank

    @property
    def speaker_refiner(self):
        if self._speaker_refiner is None:
            from .speaker_refiner import AnthropicAdvisorSpeakerRefiner

            self._speaker_refiner = AnthropicAdvisorSpeakerRefiner.from_env()
        return self._speaker_refiner

    @property
    def transcript_refiner(self):
        if self._transcript_refiner is None:
            from .transcript_refiner import AnthropicAdvisorTranscriptRefiner

            self._transcript_refiner = AnthropicAdvisorTranscriptRefiner.from_env()
        return self._transcript_refiner

    @property
    def diarize_model(self):
        if self._diarize_model is None:
            hf_token = os.getenv("HF_TOKEN")
            if not hf_token:
                return None
            from whisperx.diarize import DiarizationPipeline

            model_name = os.getenv(
                "DIARIZATION_MODEL",
                "pyannote/speaker-diarization-community-1",
            )
            self._diarize_model = DiarizationPipeline(
                model_name=model_name,
                token=hf_token,
                device=self.device,
            )

            # Тюнинг параметров кластеризации для лучшего разделения похожих голосов
            threshold = float(os.getenv("CLUSTERING_THRESHOLD", "0.35"))
            if "community" in model_name:
                # VBx-кластеризация (community-1)
                params = {
                    "clustering": {
                        "threshold": threshold,
                        "Fa": float(os.getenv("CLUSTERING_FA", "0.04")),
                        "Fb": float(os.getenv("CLUSTERING_FB", "0.9")),
                    },
                    "segmentation": {"min_duration_off": 0.0},
                }
            else:
                # Агломеративная кластеризация (3.1)
                params = {
                    "clustering": {
                        "method": "centroid",
                        "min_cluster_size": int(
                            os.getenv("CLUSTERING_MIN_CLUSTER_SIZE", "15")
                        ),
                        "threshold": float(os.getenv("CLUSTERING_THRESHOLD", "0.55")),
                    },
                    "segmentation": {"min_duration_off": 0.0},
                }

            self._diarize_model.model.instantiate(params)
            logger.info(
                "Diarization model loaded: %s, params: %s",
                model_name,
                params,
            )

        return self._diarize_model

    def inspect_speakers(
        self,
        audio_path: str,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> dict[str, Any]:
        import whisperx

        diarize_pipeline = self.diarize_model
        if diarize_pipeline is None:
            raise RuntimeError("Diarization is unavailable because HF_TOKEN is not set")

        with normalized_audio_file(audio_path) as normalized_audio:
            audio = whisperx.load_audio(normalized_audio.normalized_path)
            diarize_segments, speaker_embeddings = self._run_diarization(
                diarize_pipeline,
                audio,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
            ordered_labels = self._ordered_speaker_labels_from_diarization(
                diarize_segments
            )
            cluster_profiles, mapping = self._build_speaker_analysis(
                audio_path=audio_path,
                normalized_audio_path=normalized_audio.normalized_path,
                diarization=diarize_segments,
                speaker_embeddings=speaker_embeddings,
                ordered_labels=ordered_labels,
            )
            bundle_dir = self.voice_bank.save_meeting_bundle(
                audio_path=audio_path,
                cluster_profiles=cluster_profiles,
                mapping=mapping,
                threshold=self.voice_match_threshold,
                ordered_labels=ordered_labels,
            )
            return {
                "meeting_key": self.voice_bank.meeting_key_for(audio_path),
                "bundle_dir": str(bundle_dir),
                "ordered_labels": ordered_labels,
                "cluster_profiles": cluster_profiles,
                "mapping": mapping,
            }

    def prepare_speaker_review(
        self,
        audio_path: str,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        samples_per_speaker: int = 3,
        sample_max_seconds: float = 12.0,
    ) -> dict[str, Any]:
        bundle = self.voice_bank.load_meeting_bundle(audio_path)
        if bundle is None:
            bundle = self.inspect_speakers(
                audio_path,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )

        sample_segments = self.voice_bank.select_review_segments(bundle)
        sample_paths = self.voice_bank.export_meeting_samples(
            audio_path=audio_path,
            bundle=bundle,
            samples_per_speaker=samples_per_speaker,
            sample_max_seconds=sample_max_seconds,
            sample_segments=sample_segments,
        )
        bundle["sample_segments"] = sample_segments
        bundle["sample_paths"] = sample_paths
        return bundle

    def label_speaker_from_review(
        self,
        meeting_key: str,
        speaker_label: str,
        name: str,
        alpha: float = 0.05,
    ) -> dict[str, Any]:
        bundle = self.voice_bank.load_meeting_bundle_by_key(meeting_key)
        if bundle is None:
            raise FileNotFoundError(
                f"Review bundle not found for meeting {meeting_key}"
            )

        mapping = bundle.get("mapping", {})
        current_assignment = mapping.get(speaker_label)
        previous_name = current_assignment.name if current_assignment else speaker_label
        audio_path = str(bundle["audio_path"])
        normalized_name = normalize_review_speaker_name(name)
        result = self.voice_bank.learn_from_diarization_label(
            name=normalized_name,
            audio_path=audio_path,
            diarization=bundle,
            speaker_label=speaker_label,
            alpha=alpha,
        )
        self.voice_bank.update_bundle_assignment(
            meeting_key=meeting_key,
            speaker_label=speaker_label,
            result=result,
        )
        merged_labels = self._auto_merge_review_clusters(
            meeting_key=meeting_key,
            labeled_speaker_label=speaker_label,
            labeled_name=result.name,
        )
        return {
            "meeting_key": meeting_key,
            "speaker_label": speaker_label,
            "previous_name": previous_name,
            "name": result.name,
            "audio_path": audio_path,
            "merged_labels": merged_labels,
        }

    def transcribe(
        self,
        audio_path: str,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        *,
        speakers_hint: list[SpeakerHint] | None = None,
        auto_enroll_unknown: bool = False,
        initial_prompt: str | None = None,
    ) -> TranscribeResult:
        """Транскрипция + alignment + диаризация."""
        import whisperx

        logger.info("Transcribing %s (num_speakers=%s)", audio_path, num_speakers)

        with normalized_audio_file(audio_path) as normalized_audio:
            audio = whisperx.load_audio(normalized_audio.normalized_path)

            # 1. Транскрипция
            transcribe_kwargs: dict[str, Any] = {"batch_size": 16}
            if self.asr_language:
                transcribe_kwargs["language"] = self.asr_language
            if initial_prompt:
                transcribe_kwargs["initial_prompt"] = initial_prompt
            result = self.asr_model.transcribe(audio, **transcribe_kwargs)
            logger.info("ASR produced %d segments", len(result["segments"]))

            # 2. Word-level alignment для русского
            alignment_language = self.asr_language or result.get("language") or "ru"
            model_a, metadata = whisperx.load_align_model(
                language_code=alignment_language, device=self.device
            )
            result = whisperx.align(
                result["segments"], model_a, metadata, audio, device=self.device
            )

            allowed_names = self._allowed_names_from_hints(speakers_hint)

            diarize_pipeline = self.diarize_model
            mapping: dict[str, IdentificationResult] = {}
            cluster_profiles: dict[str, dict[str, Any]] = {}
            diarize_segments = None
            if diarize_pipeline is not None:
                diarize_segments, speaker_embeddings = self._run_diarization(
                    diarize_pipeline,
                    audio,
                    num_speakers=num_speakers,
                    min_speakers=min_speakers,
                    max_speakers=max_speakers,
                )
                result = whisperx.assign_word_speakers(
                    diarize_segments,
                    result,
                    speaker_embeddings=speaker_embeddings,
                )
                ordered_labels = self._ordered_speaker_labels_from_result(
                    result["segments"]
                ) or self._ordered_speaker_labels_from_diarization(diarize_segments)
                cluster_profiles, mapping = self._build_speaker_analysis(
                    audio_path=audio_path,
                    normalized_audio_path=normalized_audio.normalized_path,
                    diarization=diarize_segments,
                    speaker_embeddings=speaker_embeddings,
                    ordered_labels=ordered_labels,
                    allowed_names=allowed_names,
                )
                self.voice_bank.save_meeting_bundle(
                    audio_path=audio_path,
                    cluster_profiles=cluster_profiles,
                    mapping=mapping,
                    threshold=self.voice_match_threshold,
                    ordered_labels=ordered_labels,
                )

            enrolled_voiceprints = self._auto_enroll_from_hints(
                audio_path=audio_path,
                cluster_profiles=cluster_profiles,
                mapping=mapping,
                speakers_hint=speakers_hint,
                auto_enroll_unknown=auto_enroll_unknown,
            )

            # 3. Формирование результата
            ai_status = AiStatus()
            segments = self._build_transcribed_segments(result["segments"], mapping)
            segments = self._repair_short_replies(segments)
            segments, ai_status.speaker_refinement = self._refine_speakers_with_llm(
                segments
            )
            segments, ai_status.transcript_refinement = (
                self._refine_transcript_text_with_llm(segments)
            )
            seen_names = {
                segment.speaker for segment in segments if segment.speaker is not None
            }

            speaker_roles = self._build_speaker_roles(
                speakers_hint, enrolled_voiceprints
            )

            logger.info(
                "Transcription complete: %d segments, %d speakers, ai_status=%s",
                len(segments),
                len(seen_names),
                ai_status,
            )
            return TranscribeResult(
                segments=segments,
                ai_status=ai_status,
                enrolled_voiceprints=enrolled_voiceprints,
                speaker_roles=speaker_roles,
            )

    @staticmethod
    def _allowed_names_from_hints(
        speakers_hint: list[SpeakerHint] | None,
    ) -> set[str] | None:
        if not speakers_hint:
            return None
        names = {
            hint.display_name
            for hint in speakers_hint
            if hint.enrolled and hint.display_name
        }
        return names or set()

    def _auto_enroll_from_hints(
        self,
        audio_path: str,
        cluster_profiles: dict[str, dict[str, Any]],
        mapping: dict[str, IdentificationResult],
        speakers_hint: list[SpeakerHint] | None,
        auto_enroll_unknown: bool,
    ) -> list[EnrolledVoiceprint]:
        if not auto_enroll_unknown or not speakers_hint or not cluster_profiles:
            return []
        unenrolled_hints = [hint for hint in speakers_hint if not hint.enrolled]
        if len(unenrolled_hints) != 1:
            logger.info(
                "Skipping auto-enroll: expected exactly 1 unenrolled hint, got %d",
                len(unenrolled_hints),
            )
            return []
        unknown_clusters = [
            label for label, result in mapping.items() if not result.is_known
        ]
        if len(unknown_clusters) != 1:
            logger.info(
                "Skipping auto-enroll: expected exactly 1 unknown cluster, got %d",
                len(unknown_clusters),
            )
            return []

        hint = unenrolled_hints[0]
        target_cluster = unknown_clusters[0]
        registry = self._voice_bank_registry_instance()
        display_name = hint.display_name.strip()
        if not display_name:
            logger.warning("Skipping auto-enroll: hint display_name is empty")
            return []

        try:
            self.voice_bank.learn_from_diarization_label(
                name=display_name,
                audio_path=audio_path,
                diarization={"cluster_profiles": cluster_profiles},
                speaker_label=target_cluster,
            )
        except Exception as exc:
            logger.warning("Auto-enroll failed for cluster %s: %s", target_cluster, exc)
            return []

        assigned_id = registry.assign(display_name, hint.voice_bank_id)
        mapping[target_cluster] = IdentificationResult(
            name=display_name,
            confidence=1.0,
            is_known=True,
        )
        logger.info(
            "Auto-enrolled cluster %s as %s (voice_bank_id=%s)",
            target_cluster,
            display_name,
            assigned_id,
        )
        return [
            EnrolledVoiceprint(
                voice_bank_id=assigned_id,
                display_name=display_name,
                person_id=hint.person_id,
                role=hint.role,
            )
        ]

    def _voice_bank_registry_instance(self):
        from .voice_bank_registry import VoiceBankRegistry

        return VoiceBankRegistry(
            os.path.join(str(self.voice_bank.root_dir), "voice_bank_ids.json")
        )

    @staticmethod
    def _build_speaker_roles(
        speakers_hint: list[SpeakerHint] | None,
        enrolled_voiceprints: list[EnrolledVoiceprint],
    ) -> dict[str, str]:
        roles: dict[str, str] = {}
        if speakers_hint:
            for hint in speakers_hint:
                if hint.role and hint.display_name:
                    roles[hint.display_name] = hint.role
        for entry in enrolled_voiceprints:
            if entry.role and entry.display_name:
                roles[entry.display_name] = entry.role
        return roles

    def _refine_speakers_with_llm(
        self,
        segments: list[TranscribedSegment],
    ) -> tuple[list[TranscribedSegment], str]:
        if not self.speaker_llm_refinement_enabled:
            return segments, "disabled"
        try:
            refined = self.speaker_refiner.refine(segments)
            changed = sum(
                1 for a, b in zip(segments, refined) if a.speaker != b.speaker
            )
            return refined, f"applied ({changed} changes)"
        except Exception as exc:
            logger.warning("Speaker LLM refinement failed: %s", exc)
            return segments, f"failed: {exc}"

    def _refine_transcript_text_with_llm(
        self,
        segments: list[TranscribedSegment],
    ) -> tuple[list[TranscribedSegment], str]:
        if not self.transcript_llm_refinement_enabled:
            return segments, "disabled"
        try:
            refined = self.transcript_refiner.refine(segments)
            changed = sum(1 for a, b in zip(segments, refined) if a.text != b.text)
            return refined, f"applied ({changed} changes)"
        except Exception as exc:
            logger.warning("Transcript LLM refinement failed: %s", exc)
            return segments, f"failed: {exc}"

    def _build_transcribed_segments(
        self,
        result_segments: list[dict[str, Any]],
        mapping: dict[str, IdentificationResult],
    ) -> list[TranscribedSegment]:
        segments: list[TranscribedSegment] = []
        for segment in result_segments:
            split_segments = self._split_segment_by_word_speakers(segment, mapping)
            if split_segments:
                segments.extend(split_segments)
                continue

            segments.append(
                TranscribedSegment(
                    speaker=self._map_speaker_name(segment.get("speaker"), mapping),
                    start=float(segment["start"]),
                    end=float(segment["end"]),
                    text=str(segment.get("text", "")).strip(),
                )
            )
        return [segment for segment in segments if segment.text]

    def _split_segment_by_word_speakers(
        self,
        segment: dict[str, Any],
        mapping: dict[str, IdentificationResult],
    ) -> list[TranscribedSegment]:
        words = segment.get("words") or []
        if not words:
            return []

        if sum(1 for word in words if word.get("speaker")) <= 1:
            return []

        chunks: list[dict[str, Any]] = []
        current_chunk: dict[str, Any] | None = None
        fallback_speaker = self._map_speaker_name(segment.get("speaker"), mapping)

        for word in words:
            text = str(word.get("word", "")).strip()
            if not text:
                continue

            speaker = (
                self._map_speaker_name(word.get("speaker"), mapping) or fallback_speaker
            )
            start = float(word.get("start", segment["start"]))
            end = float(word.get("end", start))
            if current_chunk is None or current_chunk["speaker"] != speaker:
                current_chunk = {
                    "speaker": speaker,
                    "start": start,
                    "end": end,
                    "words": [],
                }
                chunks.append(current_chunk)

            current_chunk["words"].append(text)
            current_chunk["end"] = end

        if len(chunks) <= 1:
            return []

        chunks = self._merge_unstable_word_speaker_chunks(chunks)
        if len(chunks) <= 1:
            return []

        return [
            TranscribedSegment(
                speaker=chunk["speaker"],
                start=float(chunk["start"]),
                end=float(chunk["end"]),
                text=self._join_word_text(chunk["words"]),
            )
            for chunk in chunks
            if chunk["words"]
        ]

    def _merge_unstable_word_speaker_chunks(
        self,
        chunks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged = [dict(chunk, words=list(chunk["words"])) for chunk in chunks]
        while True:
            unstable_index = next(
                (
                    index
                    for index, chunk in enumerate(merged)
                    if not self._is_stable_word_speaker_chunk(chunk)
                ),
                None,
            )
            if unstable_index is None:
                break
            if len(merged) <= 1:
                break

            target_index = self._nearest_stable_word_chunk_index(
                merged,
                unstable_index,
            )
            if target_index is None:
                return []

            chunk = merged.pop(unstable_index)
            if unstable_index < target_index:
                target_index -= 1

            target = merged[target_index]
            if target["start"] <= chunk["start"]:
                target["words"].extend(chunk["words"])
            else:
                target["words"] = chunk["words"] + target["words"]
            target["start"] = min(float(target["start"]), float(chunk["start"]))
            target["end"] = max(float(target["end"]), float(chunk["end"]))

        return self._coalesce_adjacent_word_chunks(merged)

    def _nearest_stable_word_chunk_index(
        self,
        chunks: list[dict[str, Any]],
        unstable_index: int,
    ) -> int | None:
        previous_index = None
        for index in range(unstable_index - 1, -1, -1):
            if self._is_stable_word_speaker_chunk(chunks[index]):
                previous_index = index
                break

        next_index = None
        for index in range(unstable_index + 1, len(chunks)):
            if self._is_stable_word_speaker_chunk(chunks[index]):
                next_index = index
                break

        if previous_index is None:
            return next_index
        if next_index is None:
            return previous_index

        previous_chunk = chunks[previous_index]
        next_chunk = chunks[next_index]
        current_chunk = chunks[unstable_index]
        if previous_chunk["speaker"] == next_chunk["speaker"]:
            return previous_index
        if previous_chunk["speaker"] == current_chunk["speaker"]:
            return previous_index
        if next_chunk["speaker"] == current_chunk["speaker"]:
            return next_index

        previous_gap = abs(float(current_chunk["start"]) - float(previous_chunk["end"]))
        next_gap = abs(float(next_chunk["start"]) - float(current_chunk["end"]))
        return previous_index if previous_gap <= next_gap else next_index

    def _is_stable_word_speaker_chunk(self, chunk: dict[str, Any]) -> bool:
        word_count = len(chunk.get("words") or [])
        duration = float(chunk["end"]) - float(chunk["start"])
        text = self._join_word_text(chunk.get("words") or [])
        return (
            word_count >= MIN_WORD_SPEAKER_CHUNK_WORDS
            and duration >= MIN_WORD_SPEAKER_CHUNK_SECONDS
            and len(text) >= MIN_WORD_SPEAKER_CHUNK_CHARS
        )

    def _coalesce_adjacent_word_chunks(
        self,
        chunks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        coalesced: list[dict[str, Any]] = []
        for chunk in chunks:
            if coalesced and coalesced[-1]["speaker"] == chunk["speaker"]:
                coalesced[-1]["words"].extend(chunk["words"])
                coalesced[-1]["end"] = max(
                    float(coalesced[-1]["end"]),
                    float(chunk["end"]),
                )
                continue
            coalesced.append(chunk)
        return coalesced

    def _repair_short_replies(
        self,
        segments: list[TranscribedSegment],
        max_lookback_seconds: float = 45.0,
    ) -> list[TranscribedSegment]:
        repaired = list(segments)
        for index, segment in enumerate(repaired):
            if index == 0 or index + 1 >= len(repaired):
                continue
            if not segment.speaker or not self._is_short_reply(segment.text):
                continue

            previous_segment = repaired[index - 1]
            next_segment = repaired[index + 1]
            if previous_segment.speaker != segment.speaker:
                continue
            if next_segment.speaker != segment.speaker:
                continue
            if not previous_segment.text.strip().endswith("?"):
                continue

            alternative_speaker = self._find_recent_alternative_speaker(
                repaired,
                index - 1,
                segment.speaker,
                max_lookback_seconds=max_lookback_seconds,
            )
            if alternative_speaker is None:
                continue

            logger.info(
                "Reassigned short reply %.2f-%.2f from %s to %s: %s",
                segment.start,
                segment.end,
                segment.speaker,
                alternative_speaker,
                segment.text,
            )
            repaired[index] = TranscribedSegment(
                speaker=alternative_speaker,
                start=segment.start,
                end=segment.end,
                text=segment.text,
            )
        return repaired

    def _find_recent_alternative_speaker(
        self,
        segments: list[TranscribedSegment],
        start_index: int,
        current_speaker: str,
        max_lookback_seconds: float,
    ) -> str | None:
        reference_start = segments[start_index].start
        for index in range(start_index - 1, -1, -1):
            segment = segments[index]
            if reference_start - segment.end > max_lookback_seconds:
                break
            if segment.speaker and segment.speaker != current_speaker:
                return segment.speaker
        return None

    def _is_short_reply(self, text: str) -> bool:
        normalized = " ".join(str(text).strip().split())
        return bool(SHORT_REPLY_RE.match(normalized))

    def _map_speaker_name(
        self,
        speaker_raw,
        mapping: dict[str, IdentificationResult],
    ) -> str | None:
        if not speaker_raw:
            return None
        speaker_label = str(speaker_raw)
        identification = mapping.get(speaker_label)
        return identification.name if identification else speaker_label

    def _join_word_text(self, words: list[str]) -> str:
        text = " ".join(word.strip() for word in words if word.strip())
        return re.sub(r"\s+([,.;:!?…])", r"\1", text).strip()

    def _run_diarization(
        self,
        diarize_pipeline,
        audio,
        num_speakers: int | None,
        min_speakers: int | None,
        max_speakers: int | None,
    ):
        logger.info(
            "Running diarization (num=%s, min=%s, max=%s)...",
            num_speakers,
            min_speakers,
            max_speakers,
        )
        diarize_segments, speaker_embeddings = diarize_pipeline(
            audio,
            num_speakers=num_speakers,
            min_speakers=min_speakers or num_speakers,
            max_speakers=max_speakers or num_speakers,
            return_embeddings=True,
        )
        logger.info("Diarization found %d segments", len(diarize_segments))
        return diarize_segments, speaker_embeddings

    def _build_speaker_analysis(
        self,
        audio_path: str,
        normalized_audio_path: str,
        diarization,
        speaker_embeddings,
        ordered_labels: list[str],
        allowed_names: set[str] | None = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, IdentificationResult]]:
        cluster_profiles = self._extract_cluster_profiles(
            normalized_audio_path=normalized_audio_path,
            diarization=diarization,
            speaker_embeddings=speaker_embeddings,
            ordered_labels=ordered_labels,
        )
        cluster_embeddings = {}
        for speaker_label in ordered_labels:
            profile = cluster_profiles.get(speaker_label)
            if profile and profile.get("centroid") is not None:
                cluster_embeddings[speaker_label] = profile["centroid"]
        for speaker_label, profile in cluster_profiles.items():
            if speaker_label in cluster_embeddings:
                continue
            centroid = profile.get("centroid")
            if centroid is not None:
                cluster_embeddings[speaker_label] = centroid

        mapping = self.speaker_identifier.identify_speakers(
            cluster_embeddings=cluster_embeddings,
            voice_bank=self.voice_bank,
            threshold=self.voice_match_threshold,
            allowed_names=allowed_names,
        )

        used_unknown_names = {
            result.name for result in mapping.values() if not result.is_known
        }
        next_unknown_index = 1
        for speaker_label in ordered_labels:
            if speaker_label in mapping:
                continue
            while f"Unknown Speaker {next_unknown_index}" in used_unknown_names:
                next_unknown_index += 1
            unknown_name = f"Unknown Speaker {next_unknown_index}"
            mapping[speaker_label] = IdentificationResult(
                name=unknown_name,
                confidence=0.0,
                is_known=False,
            )
            used_unknown_names.add(unknown_name)
            next_unknown_index += 1

        for speaker_label in cluster_profiles:
            if speaker_label in mapping:
                continue
            while f"Unknown Speaker {next_unknown_index}" in used_unknown_names:
                next_unknown_index += 1
            unknown_name = f"Unknown Speaker {next_unknown_index}"
            mapping[speaker_label] = IdentificationResult(
                name=unknown_name,
                confidence=0.0,
                is_known=False,
            )
            used_unknown_names.add(unknown_name)
            next_unknown_index += 1

        logger.info(
            "Speaker identification complete for %s: %s",
            audio_path,
            {
                speaker_label: {
                    "name": result.name,
                    "confidence": round(result.confidence, 4),
                    "is_known": result.is_known,
                }
                for speaker_label, result in mapping.items()
            },
        )
        return cluster_profiles, mapping

    def _auto_merge_review_clusters(
        self,
        meeting_key: str,
        labeled_speaker_label: str,
        labeled_name: str,
    ) -> list[dict[str, Any]]:
        bundle = self.voice_bank.load_meeting_bundle_by_key(meeting_key)
        if bundle is None:
            return []

        try:
            target_centroid = self.voice_bank.get_centroid(labeled_name)
        except KeyError:
            return []

        merged: list[dict[str, Any]] = []
        mapping = bundle.get("mapping", {})
        cluster_profiles = bundle.get("cluster_profiles", {})

        for candidate_label, profile in cluster_profiles.items():
            if candidate_label == labeled_speaker_label:
                continue

            current_assignment = mapping.get(candidate_label)
            if current_assignment and current_assignment.is_known:
                continue

            centroid = profile.get("centroid")
            if centroid is None:
                continue

            score = float(np.dot(l2_normalize(centroid), target_centroid))
            logger.info(
                "Review merge candidate: labeled=%s target=%s cluster=%s score=%.4f",
                labeled_speaker_label,
                labeled_name,
                candidate_label,
                score,
            )
            if score < self.voice_match_threshold:
                continue

            previous_name = (
                current_assignment.name if current_assignment else candidate_label
            )
            merged_result = IdentificationResult(
                name=labeled_name,
                confidence=score,
                is_known=True,
            )
            self.voice_bank.update_bundle_assignment(
                meeting_key=meeting_key,
                speaker_label=candidate_label,
                result=merged_result,
            )
            merged.append(
                {
                    "speaker_label": candidate_label,
                    "previous_name": previous_name,
                    "name": labeled_name,
                    "confidence": score,
                }
            )
            logger.info(
                "Review merge accepted: cluster=%s -> %s (%.4f)",
                candidate_label,
                labeled_name,
                score,
            )

        return merged

    def _extract_cluster_profiles(
        self,
        normalized_audio_path: str,
        diarization,
        speaker_embeddings,
        ordered_labels: list[str],
    ) -> dict[str, dict[str, Any]]:
        cluster_profiles: dict[str, dict[str, Any]] = {}
        for speaker_label in ordered_labels:
            cluster_profiles[speaker_label] = {
                "speaker_label": speaker_label,
                "segments": [],
                "embedding_segments": [],
                "segment_embeddings": [],
                "centroid": None,
            }

        for start, end, speaker_label in self._iter_diarization_segments(diarization):
            profile = cluster_profiles.setdefault(
                speaker_label,
                {
                    "speaker_label": speaker_label,
                    "segments": [],
                    "embedding_segments": [],
                    "segment_embeddings": [],
                    "centroid": None,
                },
            )
            profile["segments"].append({"start": start, "end": end})

        native_embeddings = self._normalize_native_speaker_embeddings(
            speaker_embeddings
        )
        if self._can_use_native_speaker_embeddings() and native_embeddings:
            for speaker_label, embedding in native_embeddings.items():
                profile = cluster_profiles.setdefault(
                    speaker_label,
                    {
                        "speaker_label": speaker_label,
                        "segments": [],
                        "embedding_segments": [],
                        "segment_embeddings": [],
                        "centroid": None,
                    },
                )
                profile["centroid"] = embedding
            return cluster_profiles

        waveform, sample_rate = load_wav_mono(normalized_audio_path)
        for speaker_label, profile in cluster_profiles.items():
            segment_embeddings = []
            embedding_segments = []
            for segment in profile["segments"]:
                duration = float(segment["end"]) - float(segment["start"])
                if duration < self.min_embedding_segment_seconds:
                    continue
                segment_waveform = slice_waveform(
                    waveform,
                    sample_rate,
                    float(segment["start"]),
                    float(segment["end"]),
                )
                if segment_waveform.size == 0:
                    continue
                embedding = self.speaker_identifier.extract_embedding(
                    segment_waveform,
                    sample_rate,
                )
                segment_embeddings.append(embedding)
                embedding_segments.append(segment)

            profile["segment_embeddings"] = segment_embeddings
            profile["embedding_segments"] = embedding_segments
            if segment_embeddings:
                profile["centroid"] = l2_normalize(
                    np.mean(np.vstack(segment_embeddings), axis=0)
                )

        return cluster_profiles

    def _normalize_native_speaker_embeddings(
        self, speaker_embeddings
    ) -> dict[str, np.ndarray]:
        if not speaker_embeddings:
            return {}
        if not hasattr(speaker_embeddings, "items"):
            return {}

        normalized: dict[str, np.ndarray] = {}
        for speaker_label, embedding in speaker_embeddings.items():
            array = np.asarray(embedding, dtype=np.float32).reshape(-1)
            if array.size == 0:
                continue
            normalized[str(speaker_label)] = l2_normalize(array)
        return normalized

    def _can_use_native_speaker_embeddings(self) -> bool:
        model_name = os.getenv("DIARIZATION_MODEL", "")
        return "3.1" in model_name and "community" not in model_name

    def _ordered_speaker_labels_from_result(
        self,
        segments: list[dict[str, Any]],
    ) -> list[str]:
        ordered_labels: list[str] = []
        seen_labels: set[str] = set()
        for segment in segments:
            speaker_label = segment.get("speaker")
            if not speaker_label:
                continue
            speaker_label = str(speaker_label)
            if speaker_label in seen_labels:
                continue
            seen_labels.add(speaker_label)
            ordered_labels.append(speaker_label)
        return ordered_labels

    def _ordered_speaker_labels_from_diarization(self, diarization) -> list[str]:
        ordered_labels: list[str] = []
        seen_labels: set[str] = set()
        for _, _, speaker_label in self._iter_diarization_segments(diarization):
            if speaker_label in seen_labels:
                continue
            seen_labels.add(speaker_label)
            ordered_labels.append(speaker_label)
        return ordered_labels

    def _iter_diarization_segments(self, diarization):
        if diarization is None:
            return

        if hasattr(diarization, "itertracks"):
            for segment, _, speaker_label in diarization.itertracks(yield_label=True):
                yield (float(segment.start), float(segment.end), str(speaker_label))
            return

        if hasattr(diarization, "to_dict"):
            records = diarization.to_dict("records")
        else:
            records = diarization

        for record in records:
            if not isinstance(record, dict):
                continue
            start = record.get("start")
            end = record.get("end")
            speaker_label = (
                record.get("speaker")
                or record.get("label")
                or record.get("speaker_label")
            )
            if (start is None or end is None) and "segment" in record:
                segment = record["segment"]
                start = getattr(segment, "start", None)
                end = getattr(segment, "end", None)
            if start is None or end is None or speaker_label is None:
                continue
            yield (float(start), float(end), str(speaker_label))
