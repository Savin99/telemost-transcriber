import logging
import os
from dataclasses import dataclass

import numpy as np

from .audio_utils import DEFAULT_SAMPLE_RATE, l2_normalize

logger = logging.getLogger(__name__)


@dataclass
class IdentificationResult:
    name: str
    confidence: float
    is_known: bool


class SpeakerIdentifier:
    MODEL_NAME = "pyannote/wespeaker-voxceleb-resnet34-LM"

    def __init__(self, device: str = "auto"):
        if device == "auto":
            import torch

            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self._inference = None

    @property
    def inference(self):
        if self._inference is None:
            import torch
            from pyannote.audio import Inference, Model

            hf_token = os.getenv("HF_TOKEN")
            logger.info(
                "Loading speaker embedding model %s on %s",
                self.MODEL_NAME,
                self.device,
            )
            model = Model.from_pretrained(
                self.MODEL_NAME,
                token=hf_token,
            )
            model.to(torch.device(self.device))
            self._inference = Inference(
                model,
                window="whole",
                device=torch.device(self.device),
            )
        return self._inference

    def extract_embedding(self, waveform, sample_rate: int) -> np.ndarray:
        import torch
        import torchaudio.functional as audio_functional

        tensor = torch.as_tensor(waveform, dtype=torch.float32)
        if tensor.ndim == 1:
            tensor = tensor.unsqueeze(0)
        elif tensor.ndim == 2 and tensor.shape[0] > tensor.shape[1] and tensor.shape[1] <= 2:
            tensor = tensor.transpose(0, 1)
        elif tensor.ndim != 2:
            raise ValueError(f"Unsupported waveform shape: {tuple(tensor.shape)}")

        if tensor.shape[0] > 1:
            tensor = tensor.mean(dim=0, keepdim=True)

        if sample_rate != DEFAULT_SAMPLE_RATE:
            tensor = audio_functional.resample(tensor, sample_rate, DEFAULT_SAMPLE_RATE)
            sample_rate = DEFAULT_SAMPLE_RATE

        if tensor.numel() == 0:
            raise ValueError("Cannot extract embedding from empty waveform")

        embedding = self.inference(
            {
                "waveform": tensor,
                "sample_rate": sample_rate,
            }
        )
        embedding_array = np.asarray(embedding, dtype=np.float32).reshape(-1)
        return l2_normalize(embedding_array)

    def identify_speakers(
        self,
        cluster_embeddings: dict[str, np.ndarray],
        voice_bank,
        threshold: float = 0.40,
    ) -> dict[str, IdentificationResult]:
        centroids = voice_bank.get_all_centroids()
        results: dict[str, IdentificationResult] = {}

        if not cluster_embeddings:
            return results

        if not centroids:
            logger.info("Voice bank is empty; all speakers will stay unknown")
            for index, cluster_label in enumerate(cluster_embeddings, start=1):
                results[cluster_label] = IdentificationResult(
                    name=f"Unknown Speaker {index}",
                    confidence=0.0,
                    is_known=False,
                )
            return results

        scored_pairs: list[tuple[float, str, str]] = []
        for cluster_label, cluster_embedding in cluster_embeddings.items():
            normalized_cluster_embedding = l2_normalize(cluster_embedding)
            for speaker_name, centroid in centroids.items():
                score = float(np.dot(normalized_cluster_embedding, centroid))
                logger.info(
                    "Speaker match candidate: cluster=%s name=%s score=%.4f",
                    cluster_label,
                    speaker_name,
                    score,
                )
                scored_pairs.append((score, cluster_label, speaker_name))

        assigned_clusters: set[str] = set()
        assigned_names: set[str] = set()
        for score, cluster_label, speaker_name in sorted(scored_pairs, reverse=True):
            if score < threshold:
                continue
            if cluster_label in assigned_clusters or speaker_name in assigned_names:
                continue
            results[cluster_label] = IdentificationResult(
                name=speaker_name,
                confidence=score,
                is_known=True,
            )
            assigned_clusters.add(cluster_label)
            assigned_names.add(speaker_name)
            logger.info(
                "Speaker assignment accepted: cluster=%s -> %s (%.4f)",
                cluster_label,
                speaker_name,
                score,
            )

        next_unknown_index = 1
        for cluster_label in cluster_embeddings:
            if cluster_label in results:
                continue
            while any(
                existing.name == f"Unknown Speaker {next_unknown_index}"
                for existing in results.values()
            ):
                next_unknown_index += 1
            results[cluster_label] = IdentificationResult(
                name=f"Unknown Speaker {next_unknown_index}",
                confidence=0.0,
                is_known=False,
            )
            logger.info(
                "Speaker assignment fallback: cluster=%s -> %s",
                cluster_label,
                results[cluster_label].name,
            )
            next_unknown_index += 1

        return results
