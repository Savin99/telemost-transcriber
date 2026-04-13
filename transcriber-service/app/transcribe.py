import logging
import os
from dataclasses import dataclass
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
                stripped = candidate[len(prefix):].strip(" \t-:,!?")
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
        self._voice_bank = None
        self.voice_match_threshold = float(os.getenv("VOICE_MATCH_THRESHOLD", "0.40"))
        self.min_embedding_segment_seconds = float(
            os.getenv("MIN_EMBEDDING_SEGMENT_SEC", "1.0")
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
                        "min_cluster_size": int(os.getenv("CLUSTERING_MIN_CLUSTER_SIZE", "15")),
                        "threshold": float(os.getenv("CLUSTERING_THRESHOLD", "0.55")),
                    },
                    "segmentation": {"min_duration_off": 0.0},
                }

            self._diarize_model.model.instantiate(params)
            logger.info(
                "Diarization model loaded: %s, params: %s", model_name, params,
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
            raise FileNotFoundError(f"Review bundle not found for meeting {meeting_key}")

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
    ) -> list[TranscribedSegment]:
        """Транскрипция + alignment + диаризация."""
        import whisperx

        logger.info("Transcribing %s (num_speakers=%s)", audio_path, num_speakers)

        with normalized_audio_file(audio_path) as normalized_audio:
            audio = whisperx.load_audio(normalized_audio.normalized_path)

            # 1. Транскрипция
            result = self.asr_model.transcribe(audio, batch_size=16)
            logger.info("ASR produced %d segments", len(result["segments"]))

            # 2. Word-level alignment для русского
            model_a, metadata = whisperx.load_align_model(
                language_code="ru", device=self.device
            )
            result = whisperx.align(
                result["segments"], model_a, metadata, audio, device=self.device
            )

            diarize_pipeline = self.diarize_model
            mapping: dict[str, IdentificationResult] = {}
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
                )
                self.voice_bank.save_meeting_bundle(
                    audio_path=audio_path,
                    cluster_profiles=cluster_profiles,
                    mapping=mapping,
                    threshold=self.voice_match_threshold,
                    ordered_labels=ordered_labels,
                )

            # 3. Формирование результата
            segments = []
            seen_names = set()
            for seg in result["segments"]:
                speaker_raw = seg.get("speaker")
                speaker = None
                if speaker_raw:
                    identification = mapping.get(str(speaker_raw))
                    speaker = identification.name if identification else str(speaker_raw)
                    seen_names.add(speaker)

                segments.append(
                    TranscribedSegment(
                        speaker=speaker,
                        start=seg["start"],
                        end=seg["end"],
                        text=seg["text"].strip(),
                    )
                )

            logger.info(
                "Transcription complete: %d segments, %d speakers",
                len(segments),
                len(seen_names),
            )
            return segments

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

        native_embeddings = self._normalize_native_speaker_embeddings(speaker_embeddings)
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

    def _normalize_native_speaker_embeddings(self, speaker_embeddings) -> dict[str, np.ndarray]:
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
