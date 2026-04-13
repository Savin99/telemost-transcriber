import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meeting_metadata import (  # noqa: E402
    AnthropicMeetingMetadataGenerator,
    MeetingMetadata,
    build_rule_based_metadata,
    classify_meeting_by_rules,
    resolve_meeting_metadata,
)


def _transcript(segments):
    return {
        "meeting_url": "https://telemost.yandex.ru/j/example",
        "duration_seconds": 1200,
        "segments": segments,
    }


class MeetingMetadataTests(unittest.TestCase):
    def test_harness_rule_wins_for_harness_discussion(self):
        transcript = _transcript(
            [
                {"speaker": "Егор В.", "start": 0, "end": 10, "text": "Давайте доделаем harness и eval пайплайн."},
                {"speaker": "Вадим Л.", "start": 10, "end": 20, "text": "Нужно сравнить benchmark по моделям."},
                {"speaker": "Илья С.", "start": 20, "end": 30, "text": "Окей, раскладываем по задачам."},
            ]
        )

        match = classify_meeting_by_rules(transcript)

        self.assertIsNotNone(match)
        self.assertEqual(match.rule.rule_id, "harness")
        metadata = build_rule_based_metadata(transcript)
        self.assertEqual(metadata.folder_path, ["Projects", "Harness"])
        self.assertIn("Harness", metadata.title)

    def test_hiring_rule_matches_interview_keywords(self):
        transcript = _transcript(
            [
                {"speaker": "Вячеслав Т.", "start": 0, "end": 10, "text": "Расскажите про вакансии и кандидатов."},
                {"speaker": "Unknown Speaker 1", "start": 10, "end": 20, "text": "У меня опыт в рекрутинге и собеседованиях."},
            ]
        )

        metadata = build_rule_based_metadata(transcript)

        self.assertEqual(metadata.rule_id, "hiring")
        self.assertEqual(metadata.folder_path, ["Hiring"])
        self.assertIn("Собеседование", metadata.title)

    def test_resolve_metadata_without_llm_uses_rule_result(self):
        transcript = _transcript(
            [
                {"speaker": "Вячеслав Т.", "start": 0, "end": 10, "text": "Обсудим roadmap и релиз продукта."},
            ]
        )
        old_value = os.environ.get("MEETING_METADATA_LLM_ENABLED")
        os.environ["MEETING_METADATA_LLM_ENABLED"] = "false"
        try:
            metadata = resolve_meeting_metadata(transcript)
        finally:
            if old_value is None:
                os.environ.pop("MEETING_METADATA_LLM_ENABLED", None)
            else:
                os.environ["MEETING_METADATA_LLM_ENABLED"] = old_value

        self.assertEqual(metadata.folder_path, ["Product"])
        self.assertEqual(metadata.source, "rule")

    def test_llm_cannot_override_locked_rule_folder(self):
        transcript = _transcript(
            [
                {"speaker": "Вячеслав Т.", "start": 0, "end": 10, "text": "Это собеседование на вакансию ML инженера."},
            ]
        )
        base = build_rule_based_metadata(transcript)
        generator = AnthropicMeetingMetadataGenerator(api_key="test")
        generator._call_messages_api = lambda payload: {
            "content": [
                {
                    "type": "text",
                    "text": '{"title":"ML hiring sync","folder_path":["Projects","Wrong"],"filename":"ml-hiring-sync.md"}',
                }
            ]
        }

        metadata = generator.refine(transcript, base)

        self.assertEqual(metadata.folder_path, ["Hiring"])
        self.assertEqual(metadata.title, "ML hiring sync")
        self.assertEqual(metadata.filename, "ml-hiring-sync.md")


if __name__ == "__main__":
    unittest.main()
