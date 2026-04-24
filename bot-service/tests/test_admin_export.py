"""Тесты /admin/api/meetings/{id}/export?format=md|txt|json."""

import asyncio
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


class AdminExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tempdir.name) / "admin-export.db"
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
        self.auth = ("testadmin", "testpass")

    def tearDown(self) -> None:
        self.env.stop()
        _clear_app_modules()
        self.tempdir.cleanup()

    async def _seed(self) -> None:
        from app.database import (
            Meeting,
            TranscriptSegmentDB,
            async_session,
            init_db,
        )

        await init_db()
        async with async_session() as session:
            meeting = Meeting(
                id="exp1",
                meeting_url="https://telemost.yandex.ru/j/exp",
                bot_name="Bot",
                status="done",
                duration_seconds=125.5,
                recording_path="/tmp/exp1.wav",
                drive_filename="Planning · sync.md",
                admin_meta=json.dumps(
                    {
                        "title": "Planning · sync",
                        "tags": ["weekly", "product"],
                        "metrics": {"modal_seconds": 7.5},
                    }
                ),
            )
            session.add(meeting)
            # 3 сегмента: Илья -> Азиз -> Илья — проверяем группировку
            session.add(
                TranscriptSegmentDB(
                    meeting_id="exp1",
                    speaker="Илья",
                    start_time=0.0,
                    end_time=5.0,
                    text="Привет, начнём",
                )
            )
            session.add(
                TranscriptSegmentDB(
                    meeting_id="exp1",
                    speaker="Азиз",
                    start_time=5.0,
                    end_time=12.5,
                    text="Окей, я готов",
                )
            )
            session.add(
                TranscriptSegmentDB(
                    meeting_id="exp1",
                    speaker="Илья",
                    start_time=12.5,
                    end_time=20.0,
                    text="Обсудим roadmap",
                )
            )
            # Второй meeting — безымянный (title из filename)
            session.add(
                Meeting(
                    id="exp2",
                    meeting_url="https://telemost.yandex.ru/j/exp2",
                    bot_name="Bot",
                    status="done",
                    recording_path="/tmp/exp2.wav",
                    admin_meta="{}",
                )
            )
            await session.commit()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_export_requires_basic_auth(self) -> None:
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/meetings/exp1/export?format=md")
        self.assertEqual(response.status_code, 401)

    def test_export_md_format(self) -> None:
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get(
                "/admin/api/meetings/exp1/export?format=md", auth=self.auth
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/markdown", response.headers["content-type"])
        cd = response.headers["content-disposition"]
        self.assertIn("attachment", cd)
        # ASCII fallback + RFC 5987 для `·` (U+00B7)
        self.assertIn("filename=", cd)
        self.assertIn("filename*=UTF-8''", cd)
        self.assertIn("Planning", cd)
        self.assertIn(".md", cd)
        body = response.text
        # Заголовок и секции per speaker
        self.assertIn("# Planning · sync", body)
        self.assertIn("## ", body)
        self.assertIn("Илья", body)
        self.assertIn("Азиз", body)
        # hh:mm:ss форматирование
        self.assertIn("[00:00:00]", body)
        self.assertIn("[00:00:05]", body)
        self.assertIn("[00:00:12]", body)
        # Tags в шапке
        self.assertIn("weekly", body)

    def test_export_txt_format(self) -> None:
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get(
                "/admin/api/meetings/exp1/export?format=txt", auth=self.auth
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        cd = response.headers["content-disposition"]
        self.assertIn("filename=", cd)
        self.assertIn("filename*=UTF-8''", cd)
        self.assertIn(".txt", cd)
        lines = response.text.strip().split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "[00:00:00] Илья: Привет, начнём")
        self.assertEqual(lines[1], "[00:00:05] Азиз: Окей, я готов")
        self.assertEqual(lines[2], "[00:00:12] Илья: Обсудим roadmap")

    def test_export_json_format(self) -> None:
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get(
                "/admin/api/meetings/exp1/export?format=json", auth=self.auth
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.headers["content-type"])
        cd = response.headers["content-disposition"]
        self.assertIn("filename=", cd)
        self.assertIn("filename*=UTF-8''", cd)
        self.assertIn(".json", cd)
        payload = json.loads(response.text)
        self.assertEqual(payload["id"], "exp1")
        self.assertEqual(payload["title"], "Planning · sync")
        self.assertEqual(payload["segment_count"], 3)
        self.assertEqual(len(payload["segments"]), 3)
        self.assertEqual(payload["segments"][0]["index"], 0)
        self.assertEqual(payload["segments"][0]["speaker"], "Илья")
        self.assertEqual(payload["segments"][0]["start"], 0.0)
        self.assertEqual(payload["tags"], ["weekly", "product"])
        self.assertEqual(len(payload["speakers"]), 2)
        # exported_at присутствует
        self.assertIn("exported_at", payload)

    def test_export_defaults_to_md(self) -> None:
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/meetings/exp1/export", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/markdown", response.headers["content-type"])

    def test_export_title_falls_back_to_filename(self) -> None:
        """Без admin_meta.title заголовок берём из recording_path basename."""
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get(
                "/admin/api/meetings/exp2/export?format=md", auth=self.auth
            )
        self.assertEqual(response.status_code, 200)
        # filename = exp2.wav -> title = exp2
        self.assertIn('filename="exp2.md"', response.headers["content-disposition"])
        self.assertIn("# exp2", response.text)

    def test_export_404_on_missing_meeting(self) -> None:
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get(
                "/admin/api/meetings/nope/export?format=md", auth=self.auth
            )
        self.assertEqual(response.status_code, 404)

    def test_export_422_on_invalid_format(self) -> None:
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get(
                "/admin/api/meetings/exp1/export?format=pdf", auth=self.auth
            )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
