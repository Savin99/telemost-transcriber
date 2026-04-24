"""Тесты read-only /admin/api/meetings[/{id}]."""

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


class AdminMeetingsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tempdir.name) / "admin-meetings.db"
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

    def tearDown(self):
        self.env.stop()
        _clear_app_modules()
        self.tempdir.cleanup()

    async def _seed(self):
        from app.database import Meeting, TranscriptSegmentDB, async_session, init_db

        await init_db()
        async with async_session() as session:
            m1 = Meeting(
                id="m1",
                meeting_url="https://telemost.yandex.ru/j/1",
                bot_name="Bot",
                status="done",
                duration_seconds=120.0,
                recording_path="/tmp/rec/m1.wav",
                drive_file_id="f1",
                drive_web_view_link="https://drive/m1",
                drive_filename="m1.md",
                drive_folder_id="folder1",
                admin_meta=json.dumps(
                    {
                        "title": "Harness · sync",
                        "tags": ["harness", "daily"],
                        "ai_status": {"speaker_refinement": "applied"},
                        "metrics": {"modal_seconds": 12.3},
                    }
                ),
            )
            m2 = Meeting(
                id="m2",
                meeting_url="https://telemost.yandex.ru/j/2",
                bot_name="Bot",
                status="pending",
                admin_meta="{}",
            )
            m3 = Meeting(
                id="m3",
                meeting_url="https://telemost.yandex.ru/j/3",
                bot_name="Bot",
                status="done",
                admin_meta=json.dumps({"deleted_at": "2026-04-01T00:00:00Z"}),
            )
            session.add_all([m1, m2, m3])
            for i in range(3):
                session.add(
                    TranscriptSegmentDB(
                        meeting_id="m1",
                        speaker="Илья",
                        start_time=float(i * 10),
                        end_time=float(i * 10 + 8),
                        text=f"segment {i}",
                    )
                )
            session.add(
                TranscriptSegmentDB(
                    meeting_id="m1",
                    speaker="SPEAKER_02",
                    start_time=30.0,
                    end_time=35.0,
                    text="hi",
                )
            )
            await session.commit()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_list_requires_basic_auth(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/meetings")
        self.assertEqual(response.status_code, 401)

    def test_list_returns_items_with_aggregates(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/meetings", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        ids = [i["id"] for i in body["items"]]
        self.assertIn("m1", ids)
        self.assertIn("m2", ids)
        self.assertNotIn("m3", ids)  # soft-deleted фильтруется
        m1 = next(i for i in body["items"] if i["id"] == "m1")
        self.assertEqual(m1["title"], "Harness · sync")
        self.assertEqual(m1["tags"], ["harness", "daily"])
        self.assertEqual(m1["segment_count"], 4)
        self.assertEqual(m1["unknown_speaker_count"], 1)
        self.assertEqual(len(m1["speakers"]), 2)
        self.assertEqual(m1["filename"], "m1.md")
        self.assertEqual(m1["ai_status"]["speaker_refinement"], "applied")
        self.assertEqual(m1["metrics"]["modal_seconds"], 12.3)

    def test_list_filters_by_status(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/meetings?status=pending", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        ids = [i["id"] for i in response.json()["items"]]
        self.assertEqual(ids, ["m2"])

    def test_list_filters_by_tag(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/meetings?tag=harness", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        ids = [i["id"] for i in response.json()["items"]]
        self.assertEqual(ids, ["m1"])

    def test_list_include_deleted(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get(
                "/admin/api/meetings?include_deleted=true", auth=self.auth
            )
        ids = [i["id"] for i in response.json()["items"]]
        self.assertIn("m3", ids)

    def test_detail_returns_segments(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/meetings/m1", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], "m1")
        self.assertEqual(len(body["segments"]), 4)
        self.assertEqual(body["segments"][0]["index"], 0)
        self.assertEqual(body["meeting_url"], "https://telemost.yandex.ru/j/1")
        self.assertEqual(body["segment_count"], 4)

    def test_detail_404(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/meetings/nope", auth=self.auth)
        self.assertEqual(response.status_code, 404)

    def test_patch_meeting_updates_admin_meta(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.patch(
                "/admin/api/meetings/m2",
                json={"title": "New title", "tags": ["custom"]},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["title"], "New title")
        self.assertEqual(body["tags"], ["custom"])

    def test_patch_meeting_extra_fields_rejected(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.patch(
                "/admin/api/meetings/m2",
                json={"title": "X", "status": "done"},  # status — не разрешён
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 422)

    def test_soft_delete_then_restore(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            delete = client.delete("/admin/api/meetings/m1", auth=self.auth)
            self.assertEqual(delete.status_code, 204)
            # Отфильтрована из листинга
            lst = client.get("/admin/api/meetings", auth=self.auth).json()
            self.assertNotIn("m1", [i["id"] for i in lst["items"]])
            # Восстановление
            restored = client.post("/admin/api/meetings/m1/restore", auth=self.auth)
            self.assertEqual(restored.status_code, 200)
            lst2 = client.get("/admin/api/meetings", auth=self.auth).json()
            self.assertIn("m1", [i["id"] for i in lst2["items"]])

    def test_patch_segment_updates_text_and_speaker(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.patch(
                "/admin/api/meetings/m1/segments/0",
                json={"speaker": "Азиз", "text": "обновлённый"},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["speaker"], "Азиз")
        self.assertEqual(body["text"], "обновлённый")
        self.assertEqual(body["index"], 0)

    def test_patch_segment_empty_text_rejected(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.patch(
                "/admin/api/meetings/m1/segments/0",
                json={"text": "   "},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 422)

    def test_patch_segment_out_of_range(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.patch(
                "/admin/api/meetings/m1/segments/99",
                json={"text": "x"},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 404)

    def test_get_audio_404_when_no_file(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/meetings/m1/audio", auth=self.auth)
        # recording_path "/tmp/rec/m1.wav" не существует
        self.assertEqual(response.status_code, 404)

    def test_get_audio_streams_file(self):
        audio_path = Path(self.tempdir.name) / "audio.wav"
        audio_path.write_bytes(b"RIFF....WAVEfmt fake-audio-bytes")

        async def _patch():
            from app.database import async_session, Meeting, init_db

            await init_db()
            async with async_session() as session:
                m = Meeting(
                    id="m-audio",
                    meeting_url="https://x",
                    bot_name="Bot",
                    status="done",
                    recording_path=str(audio_path),
                    admin_meta="{}",
                )
                session.add(m)
                await session.commit()

        with TestClient(self.main.app) as client:
            self._run(_patch())
            response = client.get("/admin/api/meetings/m-audio/audio", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn("audio/wav", response.headers["content-type"])
        self.assertEqual(response.content, audio_path.read_bytes())

    # ----------------------------- retry --------------------------------

    def _stub_bot_workflow(self):
        """Заменяет main._bot_workflow корутиной-заглушкой и возвращает
        список, куда запишутся все вызовы (meeting_id, url, bot, num)."""
        calls: list[tuple] = []

        async def _stub(meeting_id, meeting_url, bot_name, num_speakers=None):
            calls.append((meeting_id, meeting_url, bot_name, num_speakers))
            # Имитируем мгновенный успех: убираем себя из active_sessions.
            self.main.active_sessions.pop(meeting_id, None)

        self.main._bot_workflow = _stub
        return calls

    def test_retry_starts_workflow(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            calls = self._stub_bot_workflow()
            response = client.post("/admin/api/meetings/m1/retry", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], "m1")
        # Статус сбросился в pending (до того, как stub удалил сессию).
        self.assertEqual(body["status"], "pending")
        # Workflow был запущен ровно один раз с корректными параметрами.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "m1")
        self.assertEqual(calls[0][1], "https://telemost.yandex.ru/j/1")
        self.assertIsNone(calls[0][3])

    def test_retry_conflict_if_active(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            calls = self._stub_bot_workflow()
            # Искусственно «занимаем» сессию до запроса retry.
            self.main.active_sessions["m1"] = {
                "task": None,
                "session": None,
                "capture": None,
                "stop_requested": False,
                "stop_before_recording": False,
                "recording_started": False,
            }
            response = client.post("/admin/api/meetings/m1/retry", auth=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(calls, [])  # workflow не запускался

    def test_retry_conflict_if_status_not_final(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            calls = self._stub_bot_workflow()
            # m2 имеет status="pending" — не финальный.
            response = client.post("/admin/api/meetings/m2/retry", auth=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(calls, [])

    def test_retry_not_found(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            self._stub_bot_workflow()
            response = client.post(
                "/admin/api/meetings/does-not-exist/retry", auth=self.auth
            )
        self.assertEqual(response.status_code, 404)

    # ------------------------------ bulk --------------------------------

    def test_bulk_delete_marks_all(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.post(
                "/admin/api/meetings/bulk",
                json={"ids": ["m1", "m2"], "action": "delete"},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(sorted(body["updated"]), ["m1", "m2"])
        self.assertEqual(body["not_found"], [])
        # Оба отфильтрованы из обычного листинга.
        with TestClient(self.main.app) as client:
            lst = client.get("/admin/api/meetings", auth=self.auth).json()
        ids = [i["id"] for i in lst["items"]]
        self.assertNotIn("m1", ids)
        self.assertNotIn("m2", ids)

    def test_bulk_tag_add_appends(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.post(
                "/admin/api/meetings/bulk",
                json={
                    "ids": ["m1", "m2"],
                    "action": "tag_add",
                    "payload": {"tag": "review"},
                },
                auth=self.auth,
            )
            self.assertEqual(response.status_code, 200)
            # Повторный вызов не должен дублировать тег.
            client.post(
                "/admin/api/meetings/bulk",
                json={
                    "ids": ["m1"],
                    "action": "tag_add",
                    "payload": {"tag": "review"},
                },
                auth=self.auth,
            )
            m1 = client.get("/admin/api/meetings/m1", auth=self.auth).json()
            m2 = client.get("/admin/api/meetings/m2", auth=self.auth).json()
        # m1 имел ["harness", "daily"] — добавился review, без дублей.
        self.assertIn("review", m1["tags"])
        self.assertEqual(m1["tags"].count("review"), 1)
        self.assertIn("harness", m1["tags"])
        self.assertEqual(m2["tags"], ["review"])

    def test_bulk_tag_remove(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.post(
                "/admin/api/meetings/bulk",
                json={
                    "ids": ["m1"],
                    "action": "tag_remove",
                    "payload": {"tag": "harness"},
                },
                auth=self.auth,
            )
            self.assertEqual(response.status_code, 200)
            m1 = client.get("/admin/api/meetings/m1", auth=self.auth).json()
        self.assertNotIn("harness", m1["tags"])
        self.assertIn("daily", m1["tags"])

    def test_bulk_invalid_action(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.post(
                "/admin/api/meetings/bulk",
                json={"ids": ["m1"], "action": "explode"},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 422)

    def test_bulk_tag_add_requires_tag_payload(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.post(
                "/admin/api/meetings/bulk",
                json={"ids": ["m1"], "action": "tag_add"},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 422)

    def test_bulk_partial_not_found(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.post(
                "/admin/api/meetings/bulk",
                json={"ids": ["m1", "ghost"], "action": "delete"},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["updated"], ["m1"])
        self.assertEqual(body["not_found"], ["ghost"])


if __name__ == "__main__":
    unittest.main()
