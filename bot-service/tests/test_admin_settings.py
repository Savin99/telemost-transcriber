"""Тесты /admin/api/settings — GET/PATCH + atomic JSON write."""

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _clear_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


class AdminSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tempdir.name) / "admin-settings.db"
        recordings_dir = Path(self.tempdir.name) / "recordings"
        self.settings_path = Path(self.tempdir.name) / "test_settings.json"
        self.env = patch.dict(
            os.environ,
            {
                "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
                "RECORDINGS_DIR": str(recordings_dir),
                "TRANSCRIBER_URL": "http://transcriber.test",
                "TELEMOST_SERVICE_API_KEY": "supersecret",
                "ADMIN_USERNAME": "testadmin",
                "ADMIN_PASSWORD": "testpass",
                "ADMIN_SETTINGS_PATH": str(self.settings_path),
            },
            clear=False,
        )
        self.env.start()
        _clear_app_modules()
        importlib.invalidate_caches()
        self.main = importlib.import_module("app.main")
        self.auth = ("testadmin", "testpass")

    def tearDown(self):
        self.env.stop()
        _clear_app_modules()
        self.tempdir.cleanup()

    def test_get_requires_auth(self):
        with TestClient(self.main.app) as client:
            response = client.get("/admin/api/settings")
        self.assertEqual(response.status_code, 401)

    def test_get_returns_defaults_when_no_file(self):
        self.assertFalse(self.settings_path.exists())
        with TestClient(self.main.app) as client:
            response = client.get("/admin/api/settings", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # Все 7 секций присутствуют
        for section in (
            "general",
            "asr",
            "diarization",
            "llm",
            "voice_bank",
            "integrations",
            "advanced",
        ):
            self.assertIn(section, body)
        self.assertEqual(body["general"]["bot_name"], "Транскрибатор")
        self.assertEqual(body["llm"]["refiner_enabled"], True)

    def test_patch_updates_and_persists(self):
        with TestClient(self.main.app) as client:
            response = client.patch(
                "/admin/api/settings",
                json={
                    "general": {"bot_name": "Новый бот"},
                    "diarization": {"min_confidence": 0.55},
                },
                auth=self.auth,
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["general"]["bot_name"], "Новый бот")
            self.assertAlmostEqual(body["diarization"]["min_confidence"], 0.55)
            # Файл должен существовать и быть валидным JSON
            self.assertTrue(self.settings_path.is_file())
            with self.settings_path.open("r", encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["general"]["bot_name"], "Новый бот")
            self.assertAlmostEqual(saved["diarization"]["min_confidence"], 0.55)

            # Повторный GET возвращает обновлённое значение
            response2 = client.get("/admin/api/settings", auth=self.auth)
            self.assertEqual(response2.status_code, 200)
            body2 = response2.json()
            self.assertEqual(body2["general"]["bot_name"], "Новый бот")
            # Остальные секции сохраняют дефолты
            self.assertEqual(body2["asr"]["modal_backend"], "modal")

    def test_patch_merges_section_without_overwriting(self):
        # Первый PATCH
        with TestClient(self.main.app) as client:
            client.patch(
                "/admin/api/settings",
                json={"general": {"bot_name": "A", "timezone": "UTC"}},
                auth=self.auth,
            )
            # Второй PATCH меняет только bot_name
            response = client.patch(
                "/admin/api/settings",
                json={"general": {"bot_name": "B"}},
                auth=self.auth,
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            # timezone не должен потеряться
            self.assertEqual(body["general"]["bot_name"], "B")
            self.assertEqual(body["general"]["timezone"], "UTC")

    def test_patch_extra_fields_allowed_forward_compat(self):
        """Новые секции/поля не ломают PATCH (extra=allow)."""
        with TestClient(self.main.app) as client:
            response = client.patch(
                "/admin/api/settings",
                json={"new_section_v2": {"foo": "bar"}},
                auth=self.auth,
            )
            self.assertEqual(response.status_code, 200)
            # На диске появилось новое поле
            with self.settings_path.open("r", encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["new_section_v2"], {"foo": "bar"})


if __name__ == "__main__":
    unittest.main()
