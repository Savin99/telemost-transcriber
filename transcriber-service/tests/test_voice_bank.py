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
        self.embeddings = [
            l2_normalize(np.asarray(item, dtype=np.float32)) for item in embeddings
        ]

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
            expected_centroid = l2_normalize(
                (previous_centroid * 0.75) + (new_embedding * 0.25)
            )
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
                        "centroid": l2_normalize(
                            np.array([0.0, 1.0], dtype=np.float32)
                        ),
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

    def test_review_segments_skip_known_contamination_for_unknown_cluster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "speaker.wav"
            _write_test_wav(audio_path)
            voice_bank = VoiceBank(
                temp_dir,
                speaker_identifier=QueueIdentifier([[1.0, 0.0]]),
            )
            voice_bank.enroll("Вадим Л.", [str(audio_path)])

            vadim_segment = {"start": 0.0, "end": 1.2}
            new_speaker_segment = {"start": 1.2, "end": 2.4}
            bundle = {
                "threshold": 0.40,
                "cluster_profiles": {
                    "SPEAKER_01": {
                        "segments": [vadim_segment, new_speaker_segment],
                        "embedding_segments": [vadim_segment, new_speaker_segment],
                        "segment_embeddings": [
                            l2_normalize(np.array([1.0, 0.0], dtype=np.float32)),
                            l2_normalize(np.array([0.0, 1.0], dtype=np.float32)),
                        ],
                    }
                },
                "mapping": {
                    "SPEAKER_01": IdentificationResult(
                        name="Unknown Speaker 1",
                        confidence=0.0,
                        is_known=False,
                    )
                },
            }

            selected = voice_bank.select_review_segments(bundle)

            self.assertEqual(selected["SPEAKER_01"], [new_speaker_segment])

    def test_learning_from_bundle_skips_other_known_speaker_embeddings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "speaker.wav"
            _write_test_wav(audio_path)
            voice_bank = VoiceBank(
                temp_dir,
                speaker_identifier=QueueIdentifier([[1.0, 0.0]]),
            )
            voice_bank.enroll("Вадим Л.", [str(audio_path)])

            meeting_path = Path(temp_dir) / "meeting.wav"
            _write_test_wav(meeting_path, duration_seconds=2.5)
            bundle = {
                "threshold": 0.40,
                "cluster_profiles": {
                    "SPEAKER_01": {
                        "segments": [
                            {"start": 0.0, "end": 1.2},
                            {"start": 1.2, "end": 2.4},
                        ],
                        "embedding_segments": [
                            {"start": 0.0, "end": 1.2},
                            {"start": 1.2, "end": 2.4},
                        ],
                        "segment_embeddings": [
                            l2_normalize(np.array([1.0, 0.0], dtype=np.float32)),
                            l2_normalize(np.array([0.0, 1.0], dtype=np.float32)),
                        ],
                    }
                },
            }

            voice_bank.learn_from_diarization_label(
                "Егор В.",
                str(meeting_path),
                bundle,
                "SPEAKER_01",
            )

            np.testing.assert_allclose(
                voice_bank.get_centroid("Егор В."),
                l2_normalize(np.array([0.0, 1.0], dtype=np.float32)),
                atol=1e-6,
            )


class VoiceBankAdminMethodsTests(unittest.TestCase):
    """Unit-тесты методов rename/merge/similarity_matrix/list_review_queue."""

    def _enroll(self, voice_bank: VoiceBank, name: str, embedding: list[float]):
        audio_path = Path(voice_bank.root_dir) / f"{name}.wav"
        _write_test_wav(audio_path)
        voice_bank._speaker_identifier = QueueIdentifier([embedding])
        voice_bank.enroll(name, [str(audio_path)])

    def test_rename_moves_metadata_and_embeddings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_bank = VoiceBank(temp_dir)
            self._enroll(voice_bank, "Alice", [1.0, 0.0, 0.0])

            self.assertTrue(voice_bank.rename("Alice", "Алиса"))
            names = {speaker["name"] for speaker in voice_bank.list_speakers()}
            self.assertEqual(names, {"Алиса"})
            # Центроид перенесён
            centroid = voice_bank.get_centroid("Алиса")
            self.assertAlmostEqual(float(np.linalg.norm(centroid)), 1.0, places=5)

    def test_rename_missing_returns_false(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_bank = VoiceBank(temp_dir)
            self.assertFalse(voice_bank.rename("ghost", "anyone"))

    def test_rename_conflict_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_bank = VoiceBank(temp_dir)
            self._enroll(voice_bank, "Alice", [1.0, 0.0, 0.0])
            self._enroll(voice_bank, "Bob", [0.0, 1.0, 0.0])
            with self.assertRaises(ValueError):
                voice_bank.rename("Alice", "Bob")

    def test_merge_combines_embeddings_and_removes_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_bank = VoiceBank(temp_dir)
            self._enroll(voice_bank, "Alice", [1.0, 0.0, 0.0])
            self._enroll(voice_bank, "AliceDup", [0.9, 0.1, 0.0])

            new_n = voice_bank.merge("AliceDup", "Alice")
            self.assertEqual(new_n, 2)

            names = {speaker["name"] for speaker in voice_bank.list_speakers()}
            self.assertEqual(names, {"Alice"})
            meta = voice_bank.list_speakers()[0]
            self.assertEqual(meta["num_embeddings"], 2)

            # Centroid — среднее нормализованных векторов
            expected = l2_normalize(
                (
                    l2_normalize(np.array([1.0, 0.0, 0.0], dtype=np.float32))
                    + l2_normalize(np.array([0.9, 0.1, 0.0], dtype=np.float32))
                )
                / 2.0
            )
            np.testing.assert_allclose(
                voice_bank.get_centroid("Alice"), expected, atol=1e-6
            )

    def test_merge_aggregates_n_samples_when_present(self):
        """Если у index есть n_samples/sample_segments — они суммируются/склеиваются."""
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_bank = VoiceBank(temp_dir)
            self._enroll(voice_bank, "Alice", [1.0, 0.0, 0.0])
            self._enroll(voice_bank, "AliceDup", [0.9, 0.1, 0.0])

            # Эмулируем старый формат index с sample_segments/n_samples
            index = voice_bank._load_index()
            index["Alice"]["n_samples"] = 3
            index["Alice"]["sample_segments"] = [{"start": 0.0, "end": 1.0}]
            index["AliceDup"]["n_samples"] = 5
            index["AliceDup"]["sample_segments"] = [{"start": 2.0, "end": 3.0}]
            voice_bank._persist(index, voice_bank._load_embeddings())

            new_n = voice_bank.merge("AliceDup", "Alice")
            self.assertEqual(new_n, 8)  # 3+5

            meta = voice_bank.list_speakers()[0]
            self.assertEqual(meta["n_samples"], 8)
            self.assertEqual(
                meta["sample_segments"],
                [{"start": 0.0, "end": 1.0}, {"start": 2.0, "end": 3.0}],
            )

    def test_merge_missing_source_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_bank = VoiceBank(temp_dir)
            self._enroll(voice_bank, "Alice", [1.0, 0.0, 0.0])
            with self.assertRaises(KeyError):
                voice_bank.merge("ghost", "Alice")

    def test_merge_same_name_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_bank = VoiceBank(temp_dir)
            self._enroll(voice_bank, "Alice", [1.0, 0.0, 0.0])
            with self.assertRaises(ValueError):
                voice_bank.merge("Alice", "Alice")

    def test_similarity_matrix_shape_and_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_bank = VoiceBank(temp_dir)
            self._enroll(voice_bank, "Alice", [1.0, 0.0, 0.0])
            self._enroll(voice_bank, "Bob", [0.0, 1.0, 0.0])
            self._enroll(voice_bank, "Charlie", [1.0, 1.0, 0.0])

            matrix = voice_bank.similarity_matrix()
            self.assertEqual(set(matrix.keys()), {"Alice", "Bob", "Charlie"})
            # Диагональ — 1.0
            for name in matrix:
                self.assertAlmostEqual(matrix[name][name], 1.0, places=5)
            # Симметричность
            self.assertAlmostEqual(
                matrix["Alice"]["Bob"], matrix["Bob"]["Alice"], places=6
            )
            # Alice ⊥ Bob => 0; Alice · Charlie(norm) == cos(45°) ≈ 0.7071
            self.assertAlmostEqual(matrix["Alice"]["Bob"], 0.0, places=5)
            self.assertAlmostEqual(
                matrix["Alice"]["Charlie"], 1.0 / np.sqrt(2.0), places=5
            )

    def test_similarity_matrix_empty_when_no_speakers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_bank = VoiceBank(temp_dir)
            self.assertEqual(voice_bank.similarity_matrix(), {})

    def test_list_review_queue_empty_without_meetings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_bank = VoiceBank(temp_dir)
            self.assertEqual(voice_bank.list_review_queue(), [])

    def test_list_review_queue_collects_unknown_clusters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_bank = VoiceBank(temp_dir)
            # Уже есть известный спикер — кандидат для unknown-кластера
            self._enroll(voice_bank, "Alice", [1.0, 0.0, 0.0])

            meeting_path = Path(temp_dir) / "meeting.wav"
            _write_test_wav(meeting_path, duration_seconds=2.0)

            cluster_profiles = {
                "SPEAKER_00": {
                    "segments": [{"start": 0.0, "end": 1.2}],
                    "embedding_segments": [{"start": 0.0, "end": 1.2}],
                    "segment_embeddings": [
                        l2_normalize(np.array([1.0, 0.0, 0.0], dtype=np.float32)),
                    ],
                    "centroid": l2_normalize(
                        np.array([0.95, 0.05, 0.0], dtype=np.float32)
                    ),
                },
                "SPEAKER_01": {
                    "segments": [{"start": 1.3, "end": 2.0}],
                    "embedding_segments": [{"start": 1.3, "end": 2.0}],
                    "segment_embeddings": [
                        l2_normalize(np.array([0.0, 1.0, 0.0], dtype=np.float32)),
                    ],
                    "centroid": l2_normalize(
                        np.array([0.0, 1.0, 0.0], dtype=np.float32)
                    ),
                },
            }
            mapping = {
                "SPEAKER_00": IdentificationResult(
                    name="Unknown Speaker 1", confidence=0.1, is_known=False
                ),
                # SPEAKER_01 — уже known => должен отфильтроваться
                "SPEAKER_01": IdentificationResult(
                    name="Alice", confidence=0.95, is_known=True
                ),
            }
            voice_bank.save_meeting_bundle(
                audio_path=str(meeting_path),
                cluster_profiles=cluster_profiles,
                mapping=mapping,
                threshold=0.40,
                ordered_labels=["SPEAKER_00", "SPEAKER_01"],
            )

            items = voice_bank.list_review_queue()
            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(item["cluster_label"], "SPEAKER_00")
            self.assertAlmostEqual(item["confidence"], 0.1, places=5)
            # candidates — top-3 по centroid, Alice должна быть первой
            self.assertTrue(item["candidates"])
            self.assertEqual(item["candidates"][0]["name"], "Alice")


if __name__ == "__main__":
    unittest.main()
