import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audio_utils import DEFAULT_SAMPLE_RATE, l2_normalize
from app.speaker_identifier import IdentificationResult, SpeakerIdentifier
from app.transcribe import TranscriberPipeline, normalize_review_speaker_name
from app.voice_bank import VoiceBank


def _write_test_wav(path: Path, duration_seconds: float = 3.0):
    sample_count = int(DEFAULT_SAMPLE_RATE * duration_seconds)
    samples = np.sin(np.linspace(0.0, 4.0 * np.pi, sample_count)).astype(np.float32) * 0.1
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(DEFAULT_SAMPLE_RATE)
        wav_file.writeframes(pcm.tobytes())


class FakeAsrModel:
    def __init__(self, segments):
        self.segments = segments
        self.transcribe_calls = []

    def transcribe(self, audio, batch_size=16, **kwargs):
        self.transcribe_calls.append({"batch_size": batch_size, **kwargs})
        return {"segments": [dict(segment) for segment in self.segments]}


class FakeDiarizePipeline:
    def __init__(self, diarization_segments, speaker_embeddings=None):
        self.diarization_segments = diarization_segments
        self.speaker_embeddings = speaker_embeddings

    def __call__(
        self,
        audio,
        num_speakers=None,
        min_speakers=None,
        max_speakers=None,
        return_embeddings=True,
    ):
        return ([dict(segment) for segment in self.diarization_segments], self.speaker_embeddings)


class FakeIdentifier:
    def __init__(self, embeddings):
        self.embeddings = [l2_normalize(np.asarray(item, dtype=np.float32)) for item in embeddings]
        self.calls = 0
        self._matcher = SpeakerIdentifier(device="cpu")

    def extract_embedding(self, waveform, sample_rate):
        self.calls += 1
        if not self.embeddings:
            raise AssertionError("Embedding queue exhausted")
        return self.embeddings.pop(0)

    def identify_speakers(self, cluster_embeddings, voice_bank, threshold=0.40):
        return self._matcher.identify_speakers(
            cluster_embeddings=cluster_embeddings,
            voice_bank=voice_bank,
            threshold=threshold,
        )


class FakeVoiceBank:
    def __init__(self, centroids):
        self.centroids = centroids
        self.saved_bundles = []

    def get_all_centroids(self):
        return self.centroids

    def save_meeting_bundle(self, **kwargs):
        self.saved_bundles.append(kwargs)
        return Path("/tmp/fake-bundle")

    def meeting_key_for(self, audio_path: str) -> str:
        return "fake-meeting"


def _fake_whisperx_module():
    def load_audio(path):
        return {"path": path}

    def load_align_model(language_code, device):
        return object(), {"language_code": language_code, "device": device}

    def align(segments, model_a, metadata, audio, device):
        return {"segments": [dict(segment) for segment in segments]}

    def assign_word_speakers(diarize_segments, result, speaker_embeddings=None):
        assigned_segments = []
        for segment in result["segments"]:
            assigned = dict(segment)
            assigned["speaker"] = None
            for diarized in diarize_segments:
                if diarized["start"] <= segment["start"] < diarized["end"]:
                    assigned["speaker"] = diarized["speaker"]
                    break
            assigned_segments.append(assigned)
        return {"segments": assigned_segments}

    return types.SimpleNamespace(
        load_audio=load_audio,
        load_align_model=load_align_model,
        align=align,
        assign_word_speakers=assign_word_speakers,
    )


class TranscriberPipelineTests(unittest.TestCase):
    def _make_pipeline(
        self,
        asr_segments,
        diarization_segments,
        identifier_embeddings,
        centroids,
        speaker_embeddings=None,
    ):
        pipeline = TranscriberPipeline(device="cpu")
        pipeline._asr_model = FakeAsrModel(asr_segments)
        pipeline._diarize_model = FakeDiarizePipeline(diarization_segments, speaker_embeddings)
        pipeline._speaker_identifier = FakeIdentifier(identifier_embeddings)
        pipeline._voice_bank = FakeVoiceBank(centroids)
        return pipeline

    def test_manual_extraction_is_used_for_community_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "meeting.wav"
            _write_test_wav(audio_path)
            pipeline = self._make_pipeline(
                asr_segments=[
                    {"start": 0.0, "end": 1.2, "text": "Привет"},
                    {"start": 1.2, "end": 2.4, "text": "Пока"},
                ],
                diarization_segments=[
                    {"start": 0.0, "end": 1.2, "speaker": "SPEAKER_00"},
                    {"start": 1.2, "end": 2.4, "speaker": "SPEAKER_01"},
                ],
                identifier_embeddings=[
                    [1.0, 0.0],
                    [0.0, 1.0],
                ],
                centroids={
                    "Alice": l2_normalize(np.array([1.0, 0.0], dtype=np.float32)),
                },
                speaker_embeddings={
                    "SPEAKER_00": l2_normalize(np.array([0.0, 1.0], dtype=np.float32)),
                    "SPEAKER_01": l2_normalize(np.array([1.0, 0.0], dtype=np.float32)),
                },
            )

            with patch.dict("sys.modules", {"whisperx": _fake_whisperx_module()}):
                with patch.dict("os.environ", {"DIARIZATION_MODEL": "pyannote/speaker-diarization-community-1"}):
                    segments = pipeline.transcribe(str(audio_path))

            self.assertEqual([segment.speaker for segment in segments], ["Alice", "Unknown Speaker 1"])
            self.assertEqual(pipeline.speaker_identifier.calls, 2)
            self.assertEqual(len(pipeline.voice_bank.saved_bundles), 1)

    def test_empty_voice_bank_leaves_all_unknown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "meeting.wav"
            _write_test_wav(audio_path)
            pipeline = self._make_pipeline(
                asr_segments=[
                    {"start": 0.0, "end": 1.4, "text": "Раз"},
                    {"start": 1.4, "end": 2.8, "text": "Два"},
                ],
                diarization_segments=[
                    {"start": 0.0, "end": 1.4, "speaker": "SPEAKER_00"},
                    {"start": 1.4, "end": 2.8, "speaker": "SPEAKER_01"},
                ],
                identifier_embeddings=[
                    [1.0, 0.0],
                    [0.0, 1.0],
                ],
                centroids={},
            )

            with patch.dict("sys.modules", {"whisperx": _fake_whisperx_module()}):
                with patch.dict("os.environ", {"DIARIZATION_MODEL": "pyannote/speaker-diarization-community-1"}):
                    segments = pipeline.transcribe(str(audio_path))

            self.assertEqual(
                [segment.speaker for segment in segments],
                ["Unknown Speaker 1", "Unknown Speaker 2"],
            )

    def test_short_segments_without_embeddings_still_get_unknown_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "meeting.wav"
            _write_test_wav(audio_path, duration_seconds=1.0)
            pipeline = self._make_pipeline(
                asr_segments=[
                    {"start": 0.0, "end": 0.4, "text": "Коротко"},
                    {"start": 0.4, "end": 0.8, "text": "Ещё"},
                ],
                diarization_segments=[
                    {"start": 0.0, "end": 0.4, "speaker": "SPEAKER_00"},
                    {"start": 0.4, "end": 0.8, "speaker": "SPEAKER_01"},
                ],
                identifier_embeddings=[],
                centroids={},
            )

            with patch.dict("sys.modules", {"whisperx": _fake_whisperx_module()}):
                with patch.dict("os.environ", {"DIARIZATION_MODEL": "pyannote/speaker-diarization-community-1"}):
                    segments = pipeline.transcribe(str(audio_path))

            self.assertEqual(
                [segment.speaker for segment in segments],
                ["Unknown Speaker 1", "Unknown Speaker 2"],
            )
            self.assertEqual(pipeline.speaker_identifier.calls, 0)

    def test_review_name_normalization_preserves_initial_dots(self):
        self.assertEqual(normalize_review_speaker_name("Тоже Вадим Л."), "Вадим Л.")

    def test_asr_language_defaults_to_russian(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "meeting.wav"
            _write_test_wav(audio_path)
            with patch.dict("os.environ", {"ASR_LANGUAGE": "ru"}, clear=False):
                pipeline = self._make_pipeline(
                    asr_segments=[
                        {"start": 0.0, "end": 1.2, "text": "Привет"},
                    ],
                    diarization_segments=[
                        {"start": 0.0, "end": 1.2, "speaker": "SPEAKER_00"},
                    ],
                    identifier_embeddings=[
                        [1.0, 0.0],
                    ],
                    centroids={},
                )

                with patch.dict("sys.modules", {"whisperx": _fake_whisperx_module()}):
                    with patch.dict("os.environ", {"DIARIZATION_MODEL": "pyannote/speaker-diarization-community-1"}, clear=False):
                        pipeline.transcribe(str(audio_path))

            self.assertEqual(pipeline.asr_model.transcribe_calls[0]["language"], "ru")

    def test_asr_language_auto_uses_detected_alignment_language(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "meeting.wav"
            _write_test_wav(audio_path)
            with patch.dict("os.environ", {"ASR_LANGUAGE": "auto"}, clear=False):
                pipeline = self._make_pipeline(
                    asr_segments=[
                        {"start": 0.0, "end": 1.2, "text": "Hello"},
                    ],
                    diarization_segments=[
                        {"start": 0.0, "end": 1.2, "speaker": "SPEAKER_00"},
                    ],
                    identifier_embeddings=[
                        [1.0, 0.0],
                    ],
                    centroids={},
                )
                pipeline.asr_model.transcribe = lambda audio, batch_size=16, **kwargs: {
                    "language": "en",
                    "segments": [{"start": 0.0, "end": 1.2, "text": "Hello"}],
                }

                with patch.dict("sys.modules", {"whisperx": _fake_whisperx_module()}):
                    with patch.dict("os.environ", {"DIARIZATION_MODEL": "pyannote/speaker-diarization-community-1"}, clear=False):
                        pipeline.transcribe(str(audio_path))

            self.assertIsNone(pipeline.asr_language)

    def test_review_label_normalizes_name_and_auto_merges_similar_unknown_clusters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "meeting.wav"
            _write_test_wav(audio_path, duration_seconds=3.0)

            voice_bank = VoiceBank(temp_dir)
            pipeline = TranscriberPipeline(device="cpu")
            pipeline._voice_bank = voice_bank

            cluster_profiles = {
                "SPEAKER_00": {
                    "segments": [{"start": 0.0, "end": 1.2}],
                    "embedding_segments": [{"start": 0.0, "end": 1.2}],
                    "segment_embeddings": [
                        l2_normalize(np.array([1.0, 0.0], dtype=np.float32)),
                    ],
                    "centroid": l2_normalize(np.array([1.0, 0.0], dtype=np.float32)),
                },
                "SPEAKER_01": {
                    "segments": [{"start": 1.2, "end": 2.4}],
                    "embedding_segments": [{"start": 1.2, "end": 2.4}],
                    "segment_embeddings": [
                        l2_normalize(np.array([0.96, 0.04], dtype=np.float32)),
                    ],
                    "centroid": l2_normalize(np.array([0.96, 0.04], dtype=np.float32)),
                },
            }
            voice_bank.save_meeting_bundle(
                audio_path=str(audio_path),
                cluster_profiles=cluster_profiles,
                mapping={
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
                threshold=0.40,
                ordered_labels=["SPEAKER_00", "SPEAKER_01"],
            )

            result = pipeline.label_speaker_from_review(
                meeting_key=voice_bank.meeting_key_for(str(audio_path)),
                speaker_label="SPEAKER_00",
                name="Тоже Азиз",
            )

            self.assertEqual(result["name"], "Азиз")
            self.assertEqual(
                [item["speaker_label"] for item in result["merged_labels"]],
                ["SPEAKER_01"],
            )

            bundle = voice_bank.load_meeting_bundle(str(audio_path))
            self.assertEqual(bundle["mapping"]["SPEAKER_00"].name, "Азиз")
            self.assertEqual(bundle["mapping"]["SPEAKER_01"].name, "Азиз")
            self.assertEqual(
                {speaker["name"] for speaker in voice_bank.list_speakers()},
                {"Азиз"},
            )


if __name__ == "__main__":
    unittest.main()
