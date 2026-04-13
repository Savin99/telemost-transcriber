import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audio_utils import l2_normalize
from app.speaker_identifier import SpeakerIdentifier


class FakeVoiceBank:
    def __init__(self, centroids):
        self._centroids = centroids

    def get_all_centroids(self):
        return self._centroids


class SpeakerIdentifierMatchingTests(unittest.TestCase):
    def test_greedy_assignment_is_global(self):
        identifier = SpeakerIdentifier(device="cpu")
        voice_bank = FakeVoiceBank(
            {
                "Alice": l2_normalize(np.array([1.0, 0.0], dtype=np.float32)),
                "Bob": l2_normalize(np.array([0.0, 1.0], dtype=np.float32)),
            }
        )
        cluster_embeddings = {
            "cluster_a": l2_normalize(np.array([0.95, 0.94], dtype=np.float32)),
            "cluster_b": l2_normalize(np.array([0.96, 0.10], dtype=np.float32)),
        }

        results = identifier.identify_speakers(
            cluster_embeddings=cluster_embeddings,
            voice_bank=voice_bank,
            threshold=0.1,
        )

        self.assertEqual(results["cluster_b"].name, "Alice")
        self.assertEqual(results["cluster_a"].name, "Bob")

    def test_same_name_is_not_reused(self):
        identifier = SpeakerIdentifier(device="cpu")
        voice_bank = FakeVoiceBank(
            {
                "Alice": l2_normalize(np.array([1.0, 0.0], dtype=np.float32)),
            }
        )
        cluster_embeddings = {
            "cluster_a": l2_normalize(np.array([1.0, 0.0], dtype=np.float32)),
            "cluster_b": l2_normalize(np.array([0.9, 0.1], dtype=np.float32)),
        }

        results = identifier.identify_speakers(
            cluster_embeddings=cluster_embeddings,
            voice_bank=voice_bank,
            threshold=0.1,
        )

        known_results = [result for result in results.values() if result.is_known]
        self.assertEqual(len(known_results), 1)
        self.assertEqual(known_results[0].name, "Alice")

    def test_threshold_leaves_cluster_unknown(self):
        identifier = SpeakerIdentifier(device="cpu")
        voice_bank = FakeVoiceBank(
            {
                "Alice": l2_normalize(np.array([1.0, 0.0], dtype=np.float32)),
            }
        )
        cluster_embeddings = {
            "cluster_a": l2_normalize(np.array([0.1, 1.0], dtype=np.float32)),
        }

        results = identifier.identify_speakers(
            cluster_embeddings=cluster_embeddings,
            voice_bank=voice_bank,
            threshold=0.95,
        )

        self.assertFalse(results["cluster_a"].is_known)
        self.assertEqual(results["cluster_a"].name, "Unknown Speaker 1")


if __name__ == "__main__":
    unittest.main()
