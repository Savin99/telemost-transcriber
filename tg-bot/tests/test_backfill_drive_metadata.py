import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backfill_drive_metadata import (  # noqa: E402
    DriveMarkdownFile,
    extract_date_from_filename,
    rebuild_filename_with_original_date,
    parse_markdown_transcript,
    uniquify_target_name,
    rewrite_markdown_title,
)
from meeting_metadata import MeetingMetadata  # noqa: E402


class BackfillDriveMetadataTests(unittest.TestCase):
    def test_parse_markdown_transcript_extracts_segments(self):
        markdown = """# Транскрипт встречи

**Дата:** 2026-04-13 13:50
**Ссылка:** https://telemost.yandex.ru/j/123
**Длительность:** 23:48
**Сегментов:** 3

---

### Вячеслав Т. [2:14]

Ничего страшного.

Спасибо за отклик.

### Unknown Speaker 1 [3:02]

Я фулл-тайм рассматриваю.
"""
        transcript = parse_markdown_transcript(markdown)

        self.assertEqual(transcript["meeting_date"], "2026-04-13")
        self.assertEqual(transcript["meeting_url"], "https://telemost.yandex.ru/j/123")
        self.assertEqual(transcript["duration_seconds"], 23 * 60 + 48)
        self.assertEqual(len(transcript["segments"]), 2)
        self.assertEqual(transcript["segments"][0]["speaker"], "Вячеслав Т.")
        self.assertEqual(transcript["segments"][0]["start"], 134.0)
        self.assertIn("Спасибо за отклик.", transcript["segments"][0]["text"])
        self.assertEqual(transcript["segments"][1]["speaker"], "Unknown Speaker 1")

    def test_rewrite_markdown_title_replaces_first_heading(self):
        original = "# Старый заголовок\n\nТекст\n"
        rewritten = rewrite_markdown_title(original, "Новый заголовок")
        self.assertEqual(rewritten, "# Новый заголовок\n\nТекст\n")

    def test_rebuild_filename_uses_original_meeting_date(self):
        metadata = MeetingMetadata(
            title="Harness - Егор В. и Илья С.",
            folder_path=["Projects", "Harness"],
            filename="harness-2026-04-13.md",
            source="rule",
        )
        transcript = {"meeting_date": "2026-04-07"}
        self.assertEqual(
            rebuild_filename_with_original_date(
                metadata,
                transcript,
                source_filename="transcript.md",
            ),
            "harness-егор-в-и-илья-с_2026-04-07.md",
        )

    def test_rebuild_filename_prefers_date_from_source_filename(self):
        metadata = MeetingMetadata(
            title="Harness - Егор В. и Илья С.",
            folder_path=["Projects", "Harness"],
            filename="harness-2026-04-13.md",
            source="rule",
        )
        transcript = {"meeting_date": "2026-04-13"}
        self.assertEqual(
            rebuild_filename_with_original_date(
                metadata,
                transcript,
                source_filename="Запись встречи 07.04.2026 13-26-45 - запись 3_transcript.md",
            ),
            "harness-егор-в-и-илья-с_2026-04-07.md",
        )

    def test_extract_date_from_filename_supports_short_russian_dates(self):
        self.assertEqual(
            extract_date_from_filename("Встреча в Телемосте 08.04.26 11-33-51 — запись_transcript.md"),
            "2026-04-08",
        )

    def test_uniquify_target_name_appends_stable_suffix_on_collision(self):
        seen_targets = {}
        folder_path = ["Hiring"]
        first = DriveMarkdownFile(
            file_id="aaaaaaaa11111111",
            name="transcript_4f927285-66a0-439a-bcaa-fe82e0b123de_ru.md",
            parents=[],
            folder_path=[],
        )
        second = DriveMarkdownFile(
            file_id="bbbbbbbb22222222",
            name="transcript_4f927285-66a0-439a-bcaa-fe82e0b123de_ru_speakers_v2.md",
            parents=[],
            folder_path=[],
        )
        base_name = "собеседование-вячеслав-т_2026-04-13.md"
        self.assertEqual(
            uniquify_target_name(base_name, folder_path, first, seen_targets),
            base_name,
        )
        self.assertEqual(
            uniquify_target_name(base_name, folder_path, second, seen_targets),
            "собеседование-вячеслав-т_2026-04-13_4f927285.md",
        )


if __name__ == "__main__":
    unittest.main()
