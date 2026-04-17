import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.transcribe import TranscribedSegment, TranscriberPipeline  # noqa: E402
from app.transcript_refiner import (  # noqa: E402
    ADVISOR_BETA_HEADER,
    AnthropicAdvisorTranscriptRefiner,
)


class FakeTranscriptRefiner:
    def __init__(self):
        self.calls = 0

    def refine(self, segments):
        self.calls += 1
        return [
            segment
            if index != 0
            else TranscribedSegment(
                speaker=segment.speaker,
                start=segment.start,
                end=segment.end,
                text="Исправленный текст",
            )
            for index, segment in enumerate(segments)
        ]


class TranscriptRefinerTests(unittest.TestCase):
    def test_apply_changes_updates_only_text(self):
        refiner = AnthropicAdvisorTranscriptRefiner(api_key="test", max_changes=10)
        segments = [
            TranscribedSegment("Вячеслав Т.", 0.0, 1.0, "сырой текст"),
            TranscribedSegment("Ольга", 1.1, 1.4, "да"),
        ]

        refined = refiner.apply_changes(
            segments=segments,
            payload={
                "changes": [
                    {"index": 0, "text": "Сырой текст."},
                    {"index": 99, "text": "лишнее"},
                    {"index": 1, "text": ""},
                ]
            },
        )

        self.assertEqual(refined[0].text, "Сырой текст.")
        self.assertEqual(refined[0].speaker, "Вячеслав Т.")
        self.assertEqual(refined[1].text, "да")
        self.assertEqual(segments[0].text, "сырой текст")

    def test_request_payload_uses_advisor_tool(self):
        refiner = AnthropicAdvisorTranscriptRefiner(
            api_key="test", advisor_enabled=True
        )
        payload = refiner._build_request_payload(
            [
                TranscribedSegment("Вячеслав Т.", 0.0, 1.0, "привет"),
                TranscribedSegment("Ольга", 1.1, 1.4, "да"),
            ]
        )

        self.assertEqual(payload["tools"][0]["type"], "advisor_20260301")
        self.assertEqual(payload["tools"][0]["model"], refiner.advisor_model)
        self.assertEqual(ADVISOR_BETA_HEADER, "advisor-tool-2026-03-01")

    def test_pipeline_transcript_refiner_is_optional(self):
        pipeline = TranscriberPipeline(device="cpu")
        pipeline.transcript_llm_refinement_enabled = False
        segments = [TranscribedSegment("Вячеслав Т.", 0.0, 1.0, "Привет")]

        refined, status = pipeline._refine_transcript_text_with_llm(segments)
        self.assertIs(refined, segments)
        self.assertEqual(status, "disabled")

    def test_pipeline_calls_transcript_refiner_when_enabled(self):
        pipeline = TranscriberPipeline(device="cpu")
        pipeline.transcript_llm_refinement_enabled = True
        pipeline._transcript_refiner = FakeTranscriptRefiner()
        segments = [
            TranscribedSegment("Вячеслав Т.", 0.0, 1.0, "сырой текст"),
            TranscribedSegment("Ольга", 1.1, 1.4, "да"),
        ]

        refined, status = pipeline._refine_transcript_text_with_llm(segments)

        self.assertEqual(pipeline._transcript_refiner.calls, 1)
        self.assertEqual(refined[0].text, "Исправленный текст")
        self.assertTrue(status.startswith("applied"))


if __name__ == "__main__":
    unittest.main()
