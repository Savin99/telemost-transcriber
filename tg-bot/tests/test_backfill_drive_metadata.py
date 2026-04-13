import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backfill_drive_metadata import (  # noqa: E402
    parse_markdown_transcript,
    rewrite_markdown_title,
)


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


if __name__ == "__main__":
    unittest.main()
