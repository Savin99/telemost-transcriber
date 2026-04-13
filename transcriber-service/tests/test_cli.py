import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli
from app.speaker_identifier import IdentificationResult


class FakeVoiceBank:
    def __init__(self, bundle):
        self.bundle = bundle
        self.load_calls = 0
        self.enroll_calls = []
        self.root_dir = Path("/tmp/voice-bank")

    def load_meeting_bundle(self, audio_path):
        self.load_calls += 1
        return self.bundle

    def meeting_dir_for(self, audio_path):
        return Path("/tmp/fake-bundle")

    def learn_from_diarization_label(self, name, audio_path, diarization, speaker_label, alpha=0.05):
        self.enroll_calls.append((name, audio_path, speaker_label))

    def export_meeting_samples(self, audio_path, bundle, samples_per_speaker=3, sample_max_seconds=12.0):
        return {
            speaker_label: [f"/tmp/fake-bundle/samples/{speaker_label}_0.wav"]
            for speaker_label in bundle.get("cluster_profiles", {})
        }

    def list_speakers(self):
        return []

    def remove(self, name):
        pass

    def enroll(self, name, audio_paths):
        pass


class FakePipeline:
    def __init__(self, voice_bank, inspect_bundle):
        self.voice_bank = voice_bank
        self.inspect_bundle = inspect_bundle
        self.inspect_calls = 0

    def inspect_speakers(self, audio_path, num_speakers=None, min_speakers=None, max_speakers=None):
        self.inspect_calls += 1
        return self.inspect_bundle


class CliTests(unittest.TestCase):
    def test_enroll_from_meeting_uses_existing_bundle(self):
        bundle = {
            "bundle_dir": "/tmp/fake-bundle",
            "ordered_labels": ["SPEAKER_00"],
            "cluster_profiles": {
                "SPEAKER_00": {
                    "segments": [{"start": 0.0, "end": 1.1}],
                    "embedding_segments": [{"start": 0.0, "end": 1.1}],
                    "segment_embeddings": [],
                }
            },
            "mapping": {
                "SPEAKER_00": IdentificationResult(
                    name="Unknown Speaker 1",
                    confidence=0.0,
                    is_known=False,
                )
            },
        }
        fake_voice_bank = FakeVoiceBank(bundle)
        fake_pipeline = FakePipeline(fake_voice_bank, inspect_bundle=bundle)

        with patch.object(cli, "_build_runtime", return_value=(fake_pipeline, fake_voice_bank)):
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = cli.main(
                    [
                        "--voice-bank-dir",
                        "/tmp/voice-bank",
                        "enroll-from-meeting",
                        "Вячеслав",
                        "/tmp/meeting.wav",
                        "--speaker",
                        "SPEAKER_00",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_pipeline.inspect_calls, 0)
        self.assertEqual(
            fake_voice_bank.enroll_calls,
            [("Вячеслав", "/tmp/meeting.wav", "SPEAKER_00")],
        )

    def test_enroll_from_meeting_falls_back_to_diarization(self):
        inspect_bundle = {
            "bundle_dir": "/tmp/generated-bundle",
            "ordered_labels": ["SPEAKER_01"],
            "cluster_profiles": {
                "SPEAKER_01": {
                    "segments": [{"start": 2.0, "end": 3.3}],
                    "embedding_segments": [{"start": 2.0, "end": 3.3}],
                    "segment_embeddings": [],
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
        fake_voice_bank = FakeVoiceBank(bundle=None)
        fake_pipeline = FakePipeline(fake_voice_bank, inspect_bundle=inspect_bundle)

        with patch.object(cli, "_build_runtime", return_value=(fake_pipeline, fake_voice_bank)):
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = cli.main(
                    [
                        "--voice-bank-dir",
                        "/tmp/voice-bank",
                        "enroll-from-meeting",
                        "Мария",
                        "/tmp/meeting.wav",
                        "--speaker",
                        "SPEAKER_01",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_pipeline.inspect_calls, 1)
        self.assertEqual(
            fake_voice_bank.enroll_calls,
            [("Мария", "/tmp/meeting.wav", "SPEAKER_01")],
        )

    def test_label_meeting_prompts_and_enrolls_multiple_speakers(self):
        bundle = {
            "bundle_dir": "/tmp/fake-bundle",
            "ordered_labels": ["SPEAKER_00", "SPEAKER_01"],
            "cluster_profiles": {
                "SPEAKER_00": {
                    "segments": [{"start": 0.0, "end": 1.1}],
                    "embedding_segments": [{"start": 0.0, "end": 1.1}],
                    "segment_embeddings": [],
                },
                "SPEAKER_01": {
                    "segments": [{"start": 2.0, "end": 3.3}],
                    "embedding_segments": [{"start": 2.0, "end": 3.3}],
                    "segment_embeddings": [],
                },
            },
            "mapping": {
                "SPEAKER_00": IdentificationResult(
                    name="Unknown Speaker 1",
                    confidence=0.0,
                    is_known=False,
                ),
                "SPEAKER_01": IdentificationResult(
                    name="Unknown Speaker 2",
                    confidence=0.0,
                    is_known=False,
                ),
            },
        }
        fake_voice_bank = FakeVoiceBank(bundle)
        fake_pipeline = FakePipeline(fake_voice_bank, inspect_bundle=bundle)

        with patch.object(cli, "_build_runtime", return_value=(fake_pipeline, fake_voice_bank)):
            with patch("builtins.input", side_effect=["Вячеслав", "skip"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = cli.main(
                        [
                            "--voice-bank-dir",
                            "/tmp/voice-bank",
                            "label-meeting",
                            "/tmp/meeting.wav",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            fake_voice_bank.enroll_calls,
            [("Вячеслав", "/tmp/meeting.wav", "SPEAKER_00")],
        )


if __name__ == "__main__":
    unittest.main()
