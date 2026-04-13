import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.speaker_refiner import (  # noqa: E402
    ADVISOR_BETA_HEADER,
    AnthropicAdvisorSpeakerRefiner,
)
from app.transcribe import TranscribedSegment, TranscriberPipeline  # noqa: E402


class FakeRefiner:
    def __init__(self):
        self.calls = 0

    def refine(self, segments):
        self.calls += 1
        return [
            segment if index != 1 else TranscribedSegment(
                speaker="Ольга",
                start=segment.start,
                end=segment.end,
                text=segment.text,
            )
            for index, segment in enumerate(segments)
        ]


class SpeakerRefinerTests(unittest.TestCase):
    def test_apply_changes_only_allows_existing_speakers(self):
        refiner = AnthropicAdvisorSpeakerRefiner(api_key="test", max_changes=10)
        segments = [
            TranscribedSegment("Вячеслав Т.", 0.0, 1.0, "Вопрос?"),
            TranscribedSegment("Вячеслав Т.", 1.1, 1.4, "Да."),
        ]

        refined = refiner.apply_changes(
            segments=segments,
            payload={
                "changes": [
                    {"index": 1, "speaker": "Ольга"},
                    {"index": 0, "speaker": "Invented Speaker"},
                    {"index": 99, "speaker": "Ольга"},
                ]
            },
            allowed_speakers={"Вячеслав Т.", "Ольга"},
        )

        self.assertEqual(refined[0].speaker, "Вячеслав Т.")
        self.assertEqual(refined[1].speaker, "Ольга")
        self.assertEqual(segments[1].speaker, "Вячеслав Т.")

    def test_request_payload_uses_advisor_tool(self):
        refiner = AnthropicAdvisorSpeakerRefiner(api_key="test", advisor_enabled=True)
        payload = refiner._build_request_payload(
            [
                TranscribedSegment("Вячеслав Т.", 0.0, 1.0, "Вопрос?"),
                TranscribedSegment("Ольга", 1.1, 1.4, "Да."),
            ],
            ["Вячеслав Т.", "Ольга"],
        )

        self.assertEqual(payload["tools"][0]["type"], "advisor_20260301")
        self.assertEqual(payload["tools"][0]["model"], refiner.advisor_model)
        self.assertEqual(ADVISOR_BETA_HEADER, "advisor-tool-2026-03-01")

    def test_pipeline_refiner_is_optional(self):
        pipeline = TranscriberPipeline(device="cpu")
        pipeline.speaker_llm_refinement_enabled = False
        segments = [TranscribedSegment("Вячеслав Т.", 0.0, 1.0, "Привет")]

        self.assertIs(pipeline._refine_speakers_with_llm(segments), segments)

    def test_pipeline_calls_refiner_when_enabled(self):
        pipeline = TranscriberPipeline(device="cpu")
        pipeline.speaker_llm_refinement_enabled = True
        pipeline._speaker_refiner = FakeRefiner()
        segments = [
            TranscribedSegment("Вячеслав Т.", 0.0, 1.0, "Вопрос?"),
            TranscribedSegment("Вячеслав Т.", 1.1, 1.4, "Да."),
        ]

        refined = pipeline._refine_speakers_with_llm(segments)

        self.assertEqual(pipeline._speaker_refiner.calls, 1)
        self.assertEqual(refined[1].speaker, "Ольга")


if __name__ == "__main__":
    unittest.main()
