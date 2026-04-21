import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.cluster import KMeans

from .audio_utils import (
    DEFAULT_MIN_SEGMENT_SECONDS,
    l2_normalize,
    load_wav_mono,
    make_meeting_key,
    normalized_audio_file,
    slice_waveform,
    write_wav_mono,
)
from .speaker_identifier import IdentificationResult

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VoiceBank:
    def __init__(
        self,
        root_dir: str,
        speaker_identifier=None,
        min_segment_seconds: float = DEFAULT_MIN_SEGMENT_SECONDS,
    ):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.meetings_dir = self.root_dir / "meetings"
        self.meetings_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root_dir / "index.json"
        self.embeddings_path = self.root_dir / "embeddings.npz"
        self.min_segment_seconds = min_segment_seconds
        self._speaker_identifier = speaker_identifier

    @property
    def speaker_identifier(self):
        if self._speaker_identifier is None:
            from .speaker_identifier import SpeakerIdentifier

            self._speaker_identifier = SpeakerIdentifier()
        return self._speaker_identifier

    def enroll(self, name: str, audio_paths: list[str]):
        embeddings: list[np.ndarray] = []
        for audio_path in audio_paths:
            with normalized_audio_file(audio_path) as normalized_audio:
                waveform, sample_rate = load_wav_mono(normalized_audio.normalized_path)
                embeddings.append(
                    self.speaker_identifier.extract_embedding(waveform, sample_rate)
                )

        if not embeddings:
            raise ValueError("No enrollment embeddings were extracted")

        representatives = self._select_representatives(embeddings)
        self._store_speaker_embeddings(name, representatives)
        logger.info(
            "Enrolled speaker %s with %d embeddings", name, len(representatives)
        )

    def enroll_from_diarization(
        self,
        name: str,
        audio_path: str,
        diarization,
        speaker_label: str,
    ):
        embeddings = self._extract_diarization_embeddings(
            audio_path=audio_path,
            diarization=diarization,
            speaker_label=speaker_label,
        )
        embeddings = self._filter_known_contamination(
            target_name=name,
            embeddings=embeddings,
            context=diarization,
            speaker_label=speaker_label,
        )
        if not embeddings:
            raise ValueError(
                f"No diarization embeddings available for speaker label {speaker_label}"
            )

        representatives = self._select_representatives(embeddings)
        self._store_speaker_embeddings(name, representatives)
        logger.info(
            "Enrolled speaker %s from diarization label %s using %d embeddings",
            name,
            speaker_label,
            len(representatives),
        )

    def update(self, name: str, new_embedding: np.ndarray, alpha: float = 0.05):
        index = self._load_index()
        embeddings = self._load_embeddings()
        centroid_key = f"{name}_centroid"
        if name not in index or centroid_key not in embeddings:
            raise KeyError(f"Speaker not found: {name}")

        normalized_new_embedding = l2_normalize(new_embedding)
        current_centroid = embeddings[centroid_key]
        updated_centroid = l2_normalize(
            ((1.0 - alpha) * current_centroid) + (alpha * normalized_new_embedding)
        )
        embeddings[centroid_key] = updated_centroid
        index[name]["updated_at"] = _utc_now_iso()
        self._persist(index, embeddings)

    def remove(self, name: str):
        index = self._load_index()
        embeddings = self._load_embeddings()
        if name not in index:
            return

        del index[name]
        keys_to_remove = [key for key in embeddings if key.startswith(f"{name}_")]
        for key in keys_to_remove:
            del embeddings[key]
        self._persist(index, embeddings)

    def find_duplicate_candidates(
        self,
        voice_threshold: float = 0.70,
    ) -> list[dict[str, Any]]:
        """Найти пары спикеров с подозрительно близкими центроидами.

        Возвращает список словарей с полями name_a, name_b, voice_sim, name_sim,
        отсортированный по убыванию voice_sim.
        """
        index = self._load_index()
        centroids = self.get_all_centroids()
        names = sorted(centroids.keys())
        pairs: list[dict[str, Any]] = []
        for i, name_a in enumerate(names):
            for name_b in names[i + 1 :]:
                voice_sim = float(np.dot(centroids[name_a], centroids[name_b]))
                if voice_sim < voice_threshold:
                    continue
                name_sim = SequenceMatcher(
                    None,
                    name_a.casefold().strip(),
                    name_b.casefold().strip(),
                ).ratio()
                pairs.append(
                    {
                        "name_a": name_a,
                        "name_b": name_b,
                        "voice_sim": voice_sim,
                        "name_sim": name_sim,
                        "num_embeddings_a": index.get(name_a, {}).get(
                            "num_embeddings", 0
                        ),
                        "num_embeddings_b": index.get(name_b, {}).get(
                            "num_embeddings", 0
                        ),
                        "enrolled_at_a": index.get(name_a, {}).get("enrolled_at"),
                        "enrolled_at_b": index.get(name_b, {}).get("enrolled_at"),
                    }
                )
        pairs.sort(key=lambda pair: pair["voice_sim"], reverse=True)
        return pairs

    def merge_speakers(self, keep_name: str, merge_name: str):
        """Слить ``merge_name`` в ``keep_name``: объединить эмбеддинги и пересчитать центроид."""
        if keep_name == merge_name:
            raise ValueError("keep_name и merge_name совпадают")

        index = self._load_index()
        embeddings = self._load_embeddings()
        if keep_name not in index:
            raise KeyError(f"Speaker not found: {keep_name}")
        if merge_name not in index:
            raise KeyError(f"Speaker not found: {merge_name}")

        keep_vectors: list[np.ndarray] = []
        merge_vectors: list[np.ndarray] = []
        prefix_keep = f"{keep_name}_emb_"
        prefix_merge = f"{merge_name}_emb_"
        for key, value in embeddings.items():
            if key.startswith(prefix_keep):
                keep_vectors.append(value)
            elif key.startswith(prefix_merge):
                merge_vectors.append(value)

        if not keep_vectors and not merge_vectors:
            raise RuntimeError(
                f"Нет эмбеддингов ни у {keep_name}, ни у {merge_name} — мердж невозможен"
            )

        combined = keep_vectors + merge_vectors
        normalized = [l2_normalize(np.asarray(v, dtype=np.float32)) for v in combined]

        for key in [
            k
            for k in embeddings
            if k.startswith(f"{keep_name}_") or k.startswith(f"{merge_name}_")
        ]:
            del embeddings[key]

        embeddings[f"{keep_name}_centroid"] = l2_normalize(np.mean(normalized, axis=0))
        for idx, vector in enumerate(normalized):
            embeddings[f"{keep_name}_emb_{idx}"] = vector

        keep_metadata = index.get(keep_name, {})
        index[keep_name] = {
            "num_embeddings": len(normalized),
            "enrolled_at": keep_metadata.get("enrolled_at", _utc_now_iso()),
            "updated_at": _utc_now_iso(),
        }
        del index[merge_name]
        self._persist(index, embeddings)
        logger.info(
            "Merged speaker '%s' into '%s' (%d embeddings total)",
            merge_name,
            keep_name,
            len(normalized),
        )

    def list_speakers(self) -> list[dict]:
        index = self._load_index()
        speakers: list[dict] = []
        for name in sorted(index):
            speakers.append({"name": name, **index[name]})
        return speakers

    def get_centroid(self, name: str) -> np.ndarray:
        embeddings = self._load_embeddings()
        centroid_key = f"{name}_centroid"
        if centroid_key not in embeddings:
            raise KeyError(f"Speaker centroid not found: {name}")
        return embeddings[centroid_key]

    def get_all_centroids(self) -> dict[str, np.ndarray]:
        index = self._load_index()
        embeddings = self._load_embeddings()
        centroids: dict[str, np.ndarray] = {}
        for name in index:
            centroid_key = f"{name}_centroid"
            if centroid_key in embeddings:
                centroids[name] = embeddings[centroid_key]
        return centroids

    def meeting_key_for(self, audio_path: str) -> str:
        return make_meeting_key(audio_path)

    def meeting_dir_for(self, audio_path: str) -> Path:
        return self.meetings_dir / self.meeting_key_for(audio_path)

    def meeting_dir_for_key(self, meeting_key: str) -> Path:
        return self.meetings_dir / meeting_key

    def save_meeting_bundle(
        self,
        audio_path: str,
        cluster_profiles: dict[str, dict[str, Any]],
        mapping: dict[str, IdentificationResult],
        threshold: float,
        ordered_labels: list[str],
    ) -> Path:
        meeting_dir = self.meeting_dir_for(audio_path)
        meeting_dir.mkdir(parents=True, exist_ok=True)

        bundle_json_path = meeting_dir / "bundle.json"
        bundle_embeddings_path = meeting_dir / "embeddings.npz"

        payload = {
            "meeting_key": self.meeting_key_for(audio_path),
            "audio_path": audio_path,
            "created_at": _utc_now_iso(),
            "threshold": threshold,
            "ordered_labels": ordered_labels,
            "cluster_profiles": {},
        }
        bundle_embeddings: dict[str, np.ndarray] = {}

        for speaker_label, profile in cluster_profiles.items():
            cluster_payload = {
                "segments": profile.get("segments", []),
                "embedding_segments": profile.get("embedding_segments", []),
                "num_segment_embeddings": len(profile.get("segment_embeddings", [])),
                "assignment": None,
            }
            assignment = mapping.get(speaker_label)
            if assignment is not None:
                cluster_payload["assignment"] = {
                    "name": assignment.name,
                    "confidence": assignment.confidence,
                    "is_known": assignment.is_known,
                }

            centroid = profile.get("centroid")
            if centroid is not None:
                bundle_embeddings[f"{speaker_label}_centroid"] = np.asarray(
                    centroid,
                    dtype=np.float32,
                )

            for index, embedding in enumerate(profile.get("segment_embeddings", [])):
                bundle_embeddings[f"{speaker_label}_seg_{index}"] = np.asarray(
                    embedding,
                    dtype=np.float32,
                )

            payload["cluster_profiles"][speaker_label] = cluster_payload

        self._write_json_atomic(bundle_json_path, payload)
        self._write_npz_atomic(bundle_embeddings_path, bundle_embeddings)
        return meeting_dir

    def load_meeting_bundle(self, audio_path: str) -> dict[str, Any] | None:
        return self._load_meeting_bundle_from_dir(self.meeting_dir_for(audio_path))

    def load_meeting_bundle_by_key(self, meeting_key: str) -> dict[str, Any] | None:
        return self._load_meeting_bundle_from_dir(self.meeting_dir_for_key(meeting_key))

    def learn_from_diarization_label(
        self,
        name: str,
        audio_path: str,
        diarization,
        speaker_label: str,
        alpha: float = 0.05,
    ) -> IdentificationResult:
        embeddings = self._extract_diarization_embeddings(
            audio_path=audio_path,
            diarization=diarization,
            speaker_label=speaker_label,
        )
        embeddings = self._filter_known_contamination(
            target_name=name,
            embeddings=embeddings,
            context=diarization,
            speaker_label=speaker_label,
        )
        if not embeddings:
            raise ValueError(
                f"No diarization embeddings available for speaker label {speaker_label}"
            )

        centroid = l2_normalize(np.mean(np.vstack(embeddings), axis=0))
        existing_names = {speaker["name"] for speaker in self.list_speakers()}

        canonical_name = self._resolve_duplicate_name(
            candidate_name=name,
            candidate_centroid=centroid,
            existing_names=existing_names,
        )
        if canonical_name != name:
            logger.info(
                "Fuzzy-dedup: '%s' merged into existing speaker '%s'",
                name,
                canonical_name,
            )
            name = canonical_name

        if name in existing_names:
            self.update(name, centroid, alpha=alpha)
            logger.info(
                "Updated existing speaker %s from diarization label %s",
                name,
                speaker_label,
            )
            return IdentificationResult(name=name, confidence=1.0, is_known=True)

        representatives = self._select_representatives(embeddings)
        self._store_speaker_embeddings(name, representatives)
        logger.info(
            "Learned new speaker %s from diarization label %s",
            name,
            speaker_label,
        )
        return IdentificationResult(name=name, confidence=1.0, is_known=True)

    def _resolve_duplicate_name(
        self,
        candidate_name: str,
        candidate_centroid: np.ndarray,
        existing_names: set[str],
    ) -> str:
        if candidate_name in existing_names:
            return candidate_name

        name_threshold = float(os.getenv("NAME_DUP_THRESHOLD", "0.75"))
        voice_threshold = float(os.getenv("VOICE_DUP_THRESHOLD", "0.80"))

        candidate_lower = candidate_name.casefold().strip()
        all_centroids = self.get_all_centroids()

        best_name: str | None = None
        best_score = 0.0
        for existing_name in existing_names:
            name_sim = SequenceMatcher(
                None,
                candidate_lower,
                existing_name.casefold().strip(),
            ).ratio()
            if name_sim < name_threshold:
                continue
            existing_centroid = all_centroids.get(existing_name)
            if existing_centroid is None:
                continue
            voice_sim = float(
                np.dot(l2_normalize(candidate_centroid), existing_centroid)
            )
            if voice_sim < voice_threshold:
                continue
            combined = name_sim + voice_sim
            logger.info(
                "Fuzzy-dedup candidate: %r vs existing %r name_sim=%.3f voice_sim=%.3f",
                candidate_name,
                existing_name,
                name_sim,
                voice_sim,
            )
            if combined > best_score:
                best_score = combined
                best_name = existing_name

        return best_name or candidate_name

    def select_review_segments(
        self, bundle: dict[str, Any]
    ) -> dict[str, list[dict[str, Any]]]:
        centroids = self.get_all_centroids()
        threshold = self._threshold_from_context(bundle)
        selected: dict[str, list[dict[str, Any]]] = {}
        for speaker_label, profile in bundle.get("cluster_profiles", {}).items():
            selected[speaker_label] = self._select_review_segments_for_label(
                speaker_label=speaker_label,
                profile=profile,
                bundle=bundle,
                centroids=centroids,
                threshold=threshold,
            )
        return selected

    def export_meeting_samples(
        self,
        audio_path: str,
        bundle: dict[str, Any],
        samples_per_speaker: int = 3,
        sample_max_seconds: float = 12.0,
        sample_segments: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, list[str]]:
        bundle_dir = Path(bundle.get("bundle_dir") or self.meeting_dir_for(audio_path))
        samples_dir = bundle_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

        if sample_segments is None:
            sample_segments = self.select_review_segments(bundle)

        sample_paths: dict[str, list[str]] = {}
        with normalized_audio_file(audio_path) as normalized_audio:
            waveform, sample_rate = load_wav_mono(normalized_audio.normalized_path)
            for speaker_label, profile in bundle.get("cluster_profiles", {}).items():
                if speaker_label in sample_segments:
                    segments = sample_segments[speaker_label]
                else:
                    segments = (
                        profile.get("embedding_segments")
                        or profile.get("segments")
                        or []
                    )
                exported = []
                for index, segment in enumerate(segments[:samples_per_speaker]):
                    start = float(segment["start"])
                    end = min(float(segment["end"]), start + sample_max_seconds)
                    clip = slice_waveform(waveform, sample_rate, start, end)
                    if clip.size == 0:
                        continue
                    output_path = samples_dir / f"{speaker_label}_{index}.wav"
                    write_wav_mono(output_path, clip, sample_rate)
                    exported.append(str(output_path))
                sample_paths[speaker_label] = exported
        return sample_paths

    def _select_review_segments_for_label(
        self,
        speaker_label: str,
        profile: dict[str, Any],
        bundle: dict[str, Any],
        centroids: dict[str, np.ndarray],
        threshold: float,
    ) -> list[dict[str, Any]]:
        embedding_segments = profile.get("embedding_segments") or []
        segment_embeddings = profile.get("segment_embeddings") or []
        fallback_segments = profile.get("segments") or []
        if not embedding_segments:
            return fallback_segments

        if not centroids or not segment_embeddings:
            return embedding_segments

        mapping = bundle.get("mapping", {})
        assignment = mapping.get(speaker_label)
        if self._assignment_is_known(assignment):
            return embedding_segments

        if isinstance(assignment, dict):
            assignment_name = assignment.get("name")
        else:
            assignment_name = getattr(assignment, "name", None)

        selected: list[dict[str, Any]] = []
        for segment, embedding in zip(embedding_segments, segment_embeddings):
            best_name, best_score = self._best_centroid_match(embedding, centroids)
            if (
                best_name is not None
                and best_name != assignment_name
                and best_score >= threshold
            ):
                logger.info(
                    "Skipping review sample for %s %.2f-%.2f: already matches %s (%.4f)",
                    speaker_label,
                    float(segment["start"]),
                    float(segment["end"]),
                    best_name,
                    best_score,
                )
                continue
            selected.append(segment)

        if len(embedding_segments) > len(segment_embeddings):
            selected.extend(embedding_segments[len(segment_embeddings) :])
        return selected

    def update_bundle_assignment(
        self,
        meeting_key: str,
        speaker_label: str,
        result: IdentificationResult,
    ):
        meeting_dir = self.meeting_dir_for_key(meeting_key)
        bundle_json_path = meeting_dir / "bundle.json"
        if not bundle_json_path.exists():
            raise FileNotFoundError(f"Bundle not found for meeting key {meeting_key}")

        with bundle_json_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        cluster_profile = payload.setdefault("cluster_profiles", {}).get(speaker_label)
        if cluster_profile is None:
            raise KeyError(f"Speaker label not found in bundle: {speaker_label}")

        cluster_profile["assignment"] = {
            "name": result.name,
            "confidence": result.confidence,
            "is_known": result.is_known,
        }
        self._write_json_atomic(bundle_json_path, payload)

    def _load_meeting_bundle_from_dir(self, meeting_dir: Path) -> dict[str, Any] | None:
        bundle_json_path = meeting_dir / "bundle.json"
        bundle_embeddings_path = meeting_dir / "embeddings.npz"
        if not bundle_json_path.exists() or not bundle_embeddings_path.exists():
            return None

        with bundle_json_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        embeddings = self._load_npz(bundle_embeddings_path)
        cluster_profiles: dict[str, dict[str, Any]] = {}
        mapping: dict[str, IdentificationResult] = {}
        for speaker_label, profile in payload.get("cluster_profiles", {}).items():
            assignment_data = profile.get("assignment")
            if assignment_data:
                mapping[speaker_label] = IdentificationResult(
                    name=assignment_data["name"],
                    confidence=float(assignment_data["confidence"]),
                    is_known=bool(assignment_data["is_known"]),
                )

            segment_embeddings = []
            for index in range(int(profile.get("num_segment_embeddings", 0))):
                key = f"{speaker_label}_seg_{index}"
                if key in embeddings:
                    segment_embeddings.append(embeddings[key])

            cluster_profiles[speaker_label] = {
                "speaker_label": speaker_label,
                "segments": profile.get("segments", []),
                "embedding_segments": profile.get("embedding_segments", []),
                "segment_embeddings": segment_embeddings,
                "centroid": embeddings.get(f"{speaker_label}_centroid"),
            }

        payload["cluster_profiles"] = cluster_profiles
        payload["mapping"] = mapping
        payload["bundle_dir"] = str(meeting_dir)
        return payload

    def _extract_diarization_embeddings(
        self,
        audio_path: str,
        diarization,
        speaker_label: str,
    ) -> list[np.ndarray]:
        if isinstance(diarization, dict):
            cluster_profiles = diarization.get("cluster_profiles", {})
            profile = cluster_profiles.get(speaker_label)
            if profile:
                stored_embeddings = profile.get("segment_embeddings", [])
                if stored_embeddings:
                    return [
                        l2_normalize(np.asarray(embedding, dtype=np.float32))
                        for embedding in stored_embeddings
                    ]

        segments = []
        for start, end, label in self._iter_diarization_segments(diarization):
            if label != speaker_label:
                continue
            if (end - start) < self.min_segment_seconds:
                continue
            segments.append((start, end))

        if not segments:
            return []

        with normalized_audio_file(audio_path) as normalized_audio:
            waveform, sample_rate = load_wav_mono(normalized_audio.normalized_path)
            embeddings = []
            for start, end in segments:
                segment_waveform = slice_waveform(waveform, sample_rate, start, end)
                if segment_waveform.size == 0:
                    continue
                embeddings.append(
                    self.speaker_identifier.extract_embedding(
                        segment_waveform, sample_rate
                    )
                )
            return embeddings

    def _filter_known_contamination(
        self,
        target_name: str,
        embeddings: list[np.ndarray],
        context,
        speaker_label: str,
    ) -> list[np.ndarray]:
        if not embeddings or not isinstance(context, dict):
            return embeddings

        centroids = self.get_all_centroids()
        if not centroids:
            return embeddings

        threshold = self._threshold_from_context(context)
        filtered: list[np.ndarray] = []
        for embedding in embeddings:
            normalized_embedding = l2_normalize(np.asarray(embedding, dtype=np.float32))
            best_name, best_score = self._best_centroid_match(
                normalized_embedding,
                centroids,
            )
            if (
                best_name is not None
                and best_name != target_name
                and best_score >= threshold
            ):
                logger.info(
                    "Skipping enrollment embedding for %s -> %s: matches %s (%.4f)",
                    speaker_label,
                    target_name,
                    best_name,
                    best_score,
                )
                continue
            filtered.append(normalized_embedding)

        if len(filtered) != len(embeddings):
            logger.info(
                "Filtered %d known-contamination embeddings from %s -> %s",
                len(embeddings) - len(filtered),
                speaker_label,
                target_name,
            )
        return filtered

    def _threshold_from_context(self, context) -> float:
        if isinstance(context, dict):
            try:
                return float(context.get("threshold", 0.40))
            except (TypeError, ValueError):
                return 0.40
        return 0.40

    def _assignment_is_known(self, assignment) -> bool:
        if assignment is None:
            return False
        if isinstance(assignment, dict):
            return bool(assignment.get("is_known"))
        return bool(getattr(assignment, "is_known", False))

    def _best_centroid_match(
        self,
        embedding: np.ndarray,
        centroids: dict[str, np.ndarray],
    ) -> tuple[str | None, float]:
        best_name: str | None = None
        best_score = float("-inf")
        normalized_embedding = l2_normalize(np.asarray(embedding, dtype=np.float32))
        for name, centroid in centroids.items():
            score = float(np.dot(normalized_embedding, centroid))
            if score > best_score:
                best_name = name
                best_score = score
        return best_name, best_score

    def _iter_diarization_segments(self, diarization):
        if diarization is None:
            return

        if isinstance(diarization, dict):
            cluster_profiles = diarization.get("cluster_profiles")
            if cluster_profiles:
                for speaker_label, profile in cluster_profiles.items():
                    for segment in profile.get("embedding_segments") or profile.get(
                        "segments", []
                    ):
                        yield (
                            float(segment["start"]),
                            float(segment["end"]),
                            speaker_label,
                        )
                return

        if hasattr(diarization, "itertracks"):
            for segment, _, label in diarization.itertracks(yield_label=True):
                yield (float(segment.start), float(segment.end), str(label))
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
            label = (
                record.get("speaker")
                or record.get("label")
                or record.get("speaker_label")
            )
            if (start is None or end is None) and "segment" in record:
                segment = record["segment"]
                start = getattr(segment, "start", None)
                end = getattr(segment, "end", None)
            if start is None or end is None or label is None:
                continue
            yield (float(start), float(end), str(label))

    def _select_representatives(self, embeddings: list[np.ndarray]) -> list[np.ndarray]:
        normalized_embeddings = [
            l2_normalize(np.asarray(embedding, dtype=np.float32))
            for embedding in embeddings
        ]
        if len(normalized_embeddings) < 3:
            return normalized_embeddings

        n_clusters = min(5, len(normalized_embeddings))
        matrix = np.vstack(normalized_embeddings)
        kmeans = KMeans(n_clusters=n_clusters, n_init=10, random_state=0)
        kmeans.fit(matrix)
        return [
            l2_normalize(center.astype(np.float32))
            for center in kmeans.cluster_centers_
        ]

    def _store_speaker_embeddings(self, name: str, embeddings: list[np.ndarray]):
        normalized_embeddings = [
            l2_normalize(np.asarray(embedding, dtype=np.float32))
            for embedding in embeddings
        ]
        centroid = l2_normalize(np.mean(normalized_embeddings, axis=0))

        index = self._load_index()
        npz_embeddings = self._load_embeddings()
        for key in [
            existing_key
            for existing_key in npz_embeddings
            if existing_key.startswith(f"{name}_")
        ]:
            del npz_embeddings[key]

        npz_embeddings[f"{name}_centroid"] = centroid
        for idx, embedding in enumerate(normalized_embeddings):
            npz_embeddings[f"{name}_emb_{idx}"] = embedding

        existing_metadata = index.get(name, {})
        index[name] = {
            "num_embeddings": len(normalized_embeddings),
            "enrolled_at": existing_metadata.get("enrolled_at", _utc_now_iso()),
            "updated_at": _utc_now_iso(),
        }
        self._persist(index, npz_embeddings)

    def _persist(self, index: dict[str, dict], embeddings: dict[str, np.ndarray]):
        self._write_json_atomic(self.index_path, index)
        self._write_npz_atomic(self.embeddings_path, embeddings)

    def _load_index(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        with self.index_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _load_embeddings(self) -> dict[str, np.ndarray]:
        return self._load_npz(self.embeddings_path)

    def _load_npz(self, path: Path) -> dict[str, np.ndarray]:
        if not path.exists():
            return {}
        with np.load(path, allow_pickle=False) as handle:
            return {
                key: np.asarray(handle[key], dtype=np.float32) for key in handle.files
            }

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            temp_name = handle.name
        os.replace(temp_name, path)

    def _write_npz_atomic(self, path: Path, payload: dict[str, np.ndarray]):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            delete=False,
            suffix=".npz",
        ) as handle:
            np.savez_compressed(handle, **payload)
            temp_name = handle.name
        os.replace(temp_name, path)
