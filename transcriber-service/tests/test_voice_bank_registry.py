import json
import tempfile
import unittest
from pathlib import Path

from app.voice_bank_registry import (
    VoiceBankRegistry,
    generate_voice_bank_id,
    slugify,
)


class SlugifyTests(unittest.TestCase):
    def test_cyrillic_transliteration(self):
        self.assertEqual(slugify("Илья Савин"), "ilya-savin")
        self.assertEqual(slugify("Иван Петров"), "ivan-petrov")
        self.assertEqual(slugify("О’Нил"), "o-nil")

    def test_empty_falls_back_to_speaker(self):
        self.assertEqual(slugify(""), "speaker")
        self.assertEqual(slugify("   "), "speaker")

    def test_generate_voice_bank_id_has_slug_prefix(self):
        vb_id = generate_voice_bank_id("Илья Савин")
        self.assertTrue(vb_id.startswith("ilya-savin-"))
        self.assertEqual(len(vb_id.split("-")[-1]), 8)


class VoiceBankRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "voice_bank_ids.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_assign_creates_persistent_entry(self):
        registry = VoiceBankRegistry(self.path)
        vb_id = registry.assign("Илья Савин", "recruiter-ilya-savin")
        self.assertEqual(vb_id, "recruiter-ilya-savin")

        reloaded = VoiceBankRegistry(self.path)
        self.assertEqual(reloaded.get_name("recruiter-ilya-savin"), "Илья Савин")
        self.assertEqual(
            reloaded.find_id_for_name("Илья Савин"), "recruiter-ilya-savin"
        )

    def test_assign_generates_id_when_missing(self):
        registry = VoiceBankRegistry(self.path)
        vb_id = registry.assign("Иван Петров")
        self.assertTrue(vb_id.startswith("ivan-petrov-"))
        self.assertEqual(registry.get_name(vb_id), "Иван Петров")

    def test_remove_deletes_entry(self):
        registry = VoiceBankRegistry(self.path)
        registry.assign("Мария", "maria-1")
        registry.remove("maria-1")
        self.assertIsNone(registry.get_name("maria-1"))

    def test_rename_updates_display_name(self):
        registry = VoiceBankRegistry(self.path)
        registry.assign("Старое имя", "speaker-1")
        registry.rename("speaker-1", "Новое имя")
        self.assertEqual(registry.get_name("speaker-1"), "Новое имя")
        self.assertIsNone(registry.find_id_for_name("Старое имя"))

    def test_malformed_json_is_treated_as_empty(self):
        self.path.write_text("{broken", encoding="utf-8")
        registry = VoiceBankRegistry(self.path)
        self.assertEqual(registry.list_entries(), [])
        vb_id = registry.assign("Alice", "alice-1")
        self.assertEqual(vb_id, "alice-1")

    def test_legacy_flat_format_is_read(self):
        # Поддерживаем как вложенную форму, так и «плоскую» для обратной совместимости.
        self.path.write_text(json.dumps({"speaker-1": "Alice"}), encoding="utf-8")
        registry = VoiceBankRegistry(self.path)
        self.assertEqual(registry.get_name("speaker-1"), "Alice")


if __name__ == "__main__":
    unittest.main()
