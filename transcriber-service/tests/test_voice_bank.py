import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audio_utils import DEFAULT_SAMPLE_RATE, l2_normalize
from app.speaker_identifier import IdentificationResult
from app.voice_bank import VoiceBank


def _write_test_wav(path: Path, amplitude: float = 0.1, duration_seconds: float = 1.2):
    sample_count = int(DEFAULT_SAMPLE_RATE * duration_seconds)
    samples = np.full(sample_count, amplitude, dtype=np.float32)
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(DEFAULT_SAMPLE_RATE)
        wav_file.writeframes(pcm.tobytes())


class QueueIdentifier:
    def __init__(self, embeddings):
        self.embeddings = [l2_normalize(np.asarray(item, dtype=np.float32)) for item in embeddings]

    def extract_embedding(self, waveform, sample_rate):
        if not self.embeddings:
            raise AssertionError("No embeddings left in queue")
        return self.embeddings.pop(0)


class VoiceBankTests(unittest.TestCase):
    def test_enroll_persists_centroid_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_paths = []
            for index in range(3):
                audio_path = Path(temp_dir) / f"sample_{index}.wav"
                _write_test_wav(audio_path, amplitude=0.05 * (index + 1))
                audio_paths.append(str(audio_path))

            voice_bank = VoiceBank(
                temp_dir,
                speaker_identifier=QueueIdentifier(
                    [
                        [1.0, 0.0, 0.0],
                        [0.8, 0.2, 0.0],
                        [0.7, 0.3, 0.0],
                    ]
                ),
            )
            voice_bank.enroll("Вячеслав", audio_paths)

            reloaded_voice_bank = VoiceBank(temp_dir)
            speakers = reloaded_voice_bank.list_speakers()
            self.assertEqual(len(speakers), 1)
            self.assertEqual(speakers[0]["name"], "Вячеслав")
            self.assertEqual(speakers[0]["num_embeddings"], 3)

            centroid = reloaded_voice_bank.get_centroid("Вячеслав")
            self.assertAlmostEqual(float(np.linalg.norm(centroid)), 1.0, places=5)

    def test_update_uses_ema_for_centroid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "speaker.wav"
            _write_test_wav(audio_path)
            voice_bank = VoiceBank(
                temp_dir,
                speaker_identifier=QueueIdentifier([[1.0, 0.0, 0.0]]),
            )
            voice_bank.enroll("Alice", [str(audio_path)])

            previous_centroid = voice_bank.get_centroid("Alice")
            new_embedding = l2_normalize(np.array([0.0, 1.0, 0.0], dtype=np.float32))
            voice_bank.update("Alice", new_embedding, alpha=0.25)
            updated_centroid = voice_bank.get_centroid("Alice")
            expected_centroid = l2_normalize((previous_centroid * 0.75) + (new_embedding * 0.25))
            np.testing.assert_allclose(updated_centroid, expected_centroid, atol=1e-6)

    def test_remove_deletes_speaker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "speaker.wav"
            _write_test_wav(audio_path)
            voice_bank = VoiceBank(
                temp_dir,
                speaker_identifier=QueueIdentifier([[1.0, 0.0, 0.0]]),
            )
            voice_bank.enroll("Alice", [str(audio_path)])
            voice_bank.remove("Alice")

            self.assertEqual(voice_bank.list_speakers(), [])
            self.assertEqual(voice_bank.get_all_centroids(), {})

    def test_meeting_bundle_round_trip_and_enroll_from_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_path = Path(temp_dir) / "meeting.wav"
            _write_test_wav(meeting_path, duration_seconds=2.5)
            voice_bank = VoiceBank(
                temp_dir,
                speaker_identifier=QueueIdentifier([[1.0, 0.0], [0.9, 0.1]]),
            )
            cluster_profiles = {
                "SPEAKER_00": {
                    "segments": [{"start": 0.0, "end": 1.2}],
                    "embedding_segments": [{"start": 0.0, "end": 1.2}],
                    "segment_embeddings": [
                        l2_normalize(np.array([1.0, 0.0], dtype=np.float32)),
                        l2_normalize(np.array([0.9, 0.1], dtype=np.float32)),
                    ],
                    "centroid": l2_normalize(np.array([0.95, 0.05], dtype=np.float32)),
                }
            }
            mapping = {
                "SPEAKER_00": IdentificationResult(
                    name="Unknown Speaker 1",
                    confidence=0.0,
                    is_known=False,
                )
            }

            voice_bank.save_meeting_bundle(
                audio_path=str(meeting_path),
                cluster_profiles=cluster_profiles,
                mapping=mapping,
                threshold=0.40,
                ordered_labels=["SPEAKER_00"],
            )

            bundle = voice_bank.load_meeting_bundle(str(meeting_path))
            self.assertIsNotNone(bundle)
            self.assertIn("SPEAKER_00", bundle["cluster_profiles"])
            self.assertEqual(bundle["mapping"]["SPEAKER_00"].name, "Unknown Speaker 1")

            voice_bank.enroll_from_diarization(
                "Мария",
                str(meeting_path),
                bundle,
                "SPEAKER_00",
            )
            speakers = voice_bank.list_speakers()
            self.assertEqual({speaker["name"] for speaker in speakers}, {"Мария"})

    def test_learn_from_diarization_label_updates_existing_speaker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_path = Path(temp_dir) / "meeting.wav"
            _write_test_wav(meeting_path, duration_seconds=2.5)
            audio_path = Path(temp_dir) / "speaker.wav"
            _write_test_wav(audio_path, duration_seconds=1.5)

            voice_bank = VoiceBank(
                temp_dir,
                speaker_identifier=QueueIdentifier([[1.0, 0.0], [0.0, 1.0]]),
            )
            voice_bank.enroll("Мария", [str(audio_path)])
            previous_centroid = voice_bank.get_centroid("Мария").copy()

            bundle = {
                "cluster_profiles": {
                    "SPEAKER_00": {
                        "segments": [{"start": 0.0, "end": 1.2}],
                        "embedding_segments": [{"start": 0.0, "end": 1.2}],
                        "segment_embeddings": [
                            l2_normalize(np.array([0.0, 1.0], dtype=np.float32)),
                        ],
                        "centroid": l2_normalize(np.array([0.0, 1.0], dtype=np.float32)),
                    }
                }
            }
            voice_bank.learn_from_diarization_label(
                "Мария",
                str(meeting_path),
                bundle,
                "SPEAKER_00",
                alpha=0.5,
            )

            updated_centroid = voice_bank.get_centroid("Мария")
            expected = l2_normalize(
                (previous_centroid * 0.5)
                + (l2_normalize(np.array([0.0, 1.0], dtype=np.float32)) * 0.5)
            )
            np.testing.assert_allclose(updated_centroid, expected, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
