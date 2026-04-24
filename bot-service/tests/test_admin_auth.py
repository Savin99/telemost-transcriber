"""Тесты Basic-auth для /admin/api/*."""

import importlib
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


class AdminBasicAuthTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tempdir.name) / "bot-service.db"
        recordings_dir = Path(self.tempdir.name) / "recordings"
        self.env = patch.dict(
            os.environ,
            {
                "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
                "RECORDINGS_DIR": str(recordings_dir),
                "TRANSCRIBER_URL": "http://transcriber.test",
                "TELEMOST_SERVICE_API_KEY": "supersecret",
                "ADMIN_USERNAME": "testadmin",
                "ADMIN_PASSWORD": "testpass",
            },
            clear=False,
        )
        self.env.start()
        _clear_app_modules()
        importlib.invalidate_caches()
        self.main = importlib.import_module("app.main")

    def tearDown(self):
        self.env.stop()
        _clear_app_modules()
        self.tempdir.cleanup()

    def test_admin_api_me_requires_basic_auth(self):
        with TestClient(self.main.app) as client:
            response = client.get("/admin/api/me")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response.headers.get("WWW-Authenticate", ""))

    def test_admin_api_me_bad_credentials(self):
        with TestClient(self.main.app) as client:
            response = client.get("/admin/api/me", auth=("testadmin", "wrong"))
        self.assertEqual(response.status_code, 401)

    def test_admin_api_me_good_credentials(self):
        with TestClient(self.main.app) as client:
            response = client.get("/admin/api/me", auth=("testadmin", "testpass"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"username": "testadmin"})

    def test_admin_api_me_does_not_accept_api_key(self):
        """X-API-Key не должен проходить на /admin/* — только Basic."""
        with TestClient(self.main.app) as client:
            response = client.get(
                "/admin/api/me",
                headers={"X-API-Key": "supersecret"},
            )
        self.assertEqual(response.status_code, 401)

    def test_admin_index_html_served(self):
        with TestClient(self.main.app) as client:
            response = client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("TeleScribe", response.text)

    def test_logout_returns_401_with_new_realm(self):
        """GET /admin/api/logout после валидной auth отдаёт 401 с realm=logout,
        чтобы браузер сбросил Basic-кэш."""
        with TestClient(self.main.app) as client:
            response = client.get("/admin/api/logout", auth=("testadmin", "testpass"))
        self.assertEqual(response.status_code, 401)
        www_auth = response.headers.get("WWW-Authenticate", "")
        self.assertIn("Basic", www_auth)
        self.assertIn('realm="logout"', www_auth)


if __name__ == "__main__":
    unittest.main()
