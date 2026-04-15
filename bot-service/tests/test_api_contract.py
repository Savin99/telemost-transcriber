import asyncio
import importlib
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _clear_app_modules():
    for module_name in [
        "app.main",
        "app.database",
        "app.models",
        "app.audio_capture",
        "app.telemost",
    ]:
        sys.modules.pop(module_name, None)


class ControlledTelemostSession:
    instances = []
    _allow_join = threading.Event()
    _allow_leave = threading.Event()

    @classmethod
    def reset(cls, *, join_immediately=True, leave_immediately=True):
        cls.instances = []
        cls._allow_join = threading.Event()
        cls._allow_leave = threading.Event()
        if join_immediately:
            cls._allow_join.set()
        if leave_immediately:
            cls._allow_leave.set()

    def __init__(self, meeting_url: str, bot_name: str):
        self.meeting_url = meeting_url
        self.bot_name = bot_name
        self._meeting_ended = asyncio.Event()
        self.leave_calls = 0
        ControlledTelemostSession.instances.append(self)

    async def join(self):
        while not self._allow_join.is_set():
            await asyncio.sleep(0.01)

    async def wait_for_end(self):
        await self._meeting_ended.wait()

    async def leave(self):
        self.leave_calls += 1
        while not self._allow_leave.is_set():
            await asyncio.sleep(0.01)
        self._meeting_ended.set()


class FakeAudioCapture:
    final_duration = 321.5

    def __init__(self, output_path: str, session_id: str = "default"):
        self.output_path = output_path
        self.session_id = session_id
        self.started = False
        self.stopped = False

    @property
    def duration_seconds(self):
        if self.started and not self.stopped:
            return self.final_duration
        if self.stopped:
            return self.final_duration
        return None

    async def start(self, page=None):
        self.started = True

    async def stop(self):
        if not self.started and not self.stopped:
            return None
        self.started = False
        self.stopped = True
        return self.final_duration


class ControlledTranscribe:
    def __init__(self, segments: list[dict], *, immediate=True):
        self._segments = [dict(segment) for segment in segments]
        self._allow = threading.Event()
        if immediate:
            self._allow.set()

    def allow(self):
        self._allow.set()

    async def __call__(self, recording_path: str, num_speakers: int | None = None):
        while not self._allow.is_set():
            await asyncio.sleep(0.01)
        return [dict(segment) for segment in self._segments]


class ControlledDriveUpload:
    def __init__(
        self,
        result: dict[str, str] | None = None,
        *,
        immediate: bool = True,
        error: Exception | None = None,
    ):
        self.result = dict(
            result
            or {
                "file_id": "1AbCdEf",
                "folder_id": "1jwDy7XAtvX327nf0MJWZHzFERBwkbjvR",
                "filename": "Interview_Ilya_2026-04-15.md",
                "web_view_link": "https://drive.google.com/file/d/1AbCdEf/view",
            }
        )
        self.error = error
        self.calls: list[dict] = []
        self._allow = threading.Event()
        if immediate:
            self._allow.set()

    def allow(self):
        self._allow.set()

    async def __call__(self, transcript: dict, *, source_filename: str | None = None):
        while not self._allow.is_set():
            await asyncio.sleep(0.01)
        self.calls.append({"transcript": dict(transcript), "source_filename": source_filename})
        if self.error is not None:
            raise self.error
        return dict(self.result)


class BotServiceContractTests(unittest.TestCase):
    maxDiff = None

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
            },
            clear=False,
        )
        self.env.start()
        self.main = self._reload_main()
        self.headers = {"X-API-Key": "supersecret"}

    def tearDown(self):
        self.env.stop()
        _clear_app_modules()
        self.tempdir.cleanup()

    def _reload_main(self):
        _clear_app_modules()
        importlib.invalidate_caches()
        return importlib.import_module("app.main")

    def _wait_for_status(self, client: TestClient, meeting_id: str, expected_status: str) -> dict:
        deadline = time.time() + 5
        last_payload = None
        while time.time() < deadline:
            response = client.get(f"/status/{meeting_id}", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            last_payload = response.json()
            if last_payload["status"] == expected_status:
                return last_payload
            time.sleep(0.05)
        self.fail(f"Timed out waiting for status {expected_status}; last payload: {last_payload}")

    def test_service_refuses_to_start_without_api_key(self):
        with patch.dict(os.environ, {"TELEMOST_SERVICE_API_KEY": ""}, clear=False):
            module = self._reload_main()
            with self.assertRaises(RuntimeError):
                with TestClient(module.app):
                    pass
        self.main = self._reload_main()

    def test_all_public_endpoints_require_api_key(self):
        with TestClient(self.main.app) as client:
            responses = [
                client.get("/health"),
                client.post("/join", json={"meeting_url": "https://telemost.yandex.ru/j/1", "bot_name": "Bot"}),
                client.post("/leave/fake-meeting-id"),
                client.get("/status/fake-meeting-id"),
                client.get("/transcripts/fake-meeting-id"),
            ]
            for response in responses:
                self.assertEqual(response.status_code, 403)

            wrong_key_headers = {"X-API-Key": "wrong"}
            responses = [
                client.get("/health", headers=wrong_key_headers),
                client.post(
                    "/join",
                    headers=wrong_key_headers,
                    json={"meeting_url": "https://telemost.yandex.ru/j/1", "bot_name": "Bot"},
                ),
                client.post("/leave/fake-meeting-id", headers=wrong_key_headers),
                client.get("/status/fake-meeting-id", headers=wrong_key_headers),
                client.get("/transcripts/fake-meeting-id", headers=wrong_key_headers),
            ]
            for response in responses:
                self.assertEqual(response.status_code, 403)

    def test_join_status_and_transcripts_match_contract(self):
        ControlledTelemostSession.reset(join_immediately=True, leave_immediately=True)
        transcribe = ControlledTranscribe(
            [
                {"speaker": None, "start": 0.0, "end": 4.2, "text": "Добрый день"},
                {"speaker": "Кандидат", "start": 4.3, "end": 9.1, "text": "Здравствуйте"},
            ]
        )
        drive_upload = ControlledDriveUpload()

        with patch.object(self.main, "TelemostSession", ControlledTelemostSession), patch.object(
            self.main, "AudioCapture", FakeAudioCapture
        ), patch.object(self.main, "_transcribe", new=transcribe), patch.object(
            self.main, "_upload_transcript_to_drive", new=drive_upload
        ):
            with TestClient(self.main.app) as client:
                join_response = client.post(
                    "/join",
                    headers=self.headers,
                    json={
                        "meeting_url": "https://telemost.yandex.ru/j/abc",
                        "bot_name": "Интервью-бот",
                    },
                )
                self.assertEqual(join_response.status_code, 200)
                join_payload = join_response.json()
                self.assertEqual(join_payload["status"], "pending")
                self.assertEqual(join_payload["meeting_url"], "https://telemost.yandex.ru/j/abc")
                self.assertRegex(join_payload["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
                self.assertNotIn("duration_seconds", join_payload)
                self.assertNotIn("error_message", join_payload)

                meeting_id = join_payload["meeting_id"]
                recording_payload = self._wait_for_status(client, meeting_id, "recording")
                self.assertAlmostEqual(recording_payload["duration_seconds"], 321.5)
                self.assertIsNone(recording_payload["error_message"])
                self.assertIsNone(recording_payload["transcript_url"])
                self.assertIsNone(recording_payload["drive_file"])
                self.assertEqual(recording_payload["created_at"], join_payload["created_at"])

                transcript_not_ready = client.get(
                    f"/transcripts/{meeting_id}",
                    headers=self.headers,
                )
                self.assertEqual(transcript_not_ready.status_code, 409)

                leave_response = client.post(f"/leave/{meeting_id}", headers=self.headers)
                self.assertEqual(leave_response.status_code, 200)
                self.assertIn(leave_response.json()["status"], {"recording", "leaving", "transcribing", "done"})

                done_payload = self._wait_for_status(client, meeting_id, "done")
                self.assertEqual(done_payload["meeting_id"], meeting_id)
                self.assertEqual(done_payload["meeting_url"], "https://telemost.yandex.ru/j/abc")
                self.assertAlmostEqual(done_payload["duration_seconds"], 321.5)
                self.assertIsNone(done_payload["error_message"])
                self.assertEqual(done_payload["created_at"], join_payload["created_at"])
                self.assertEqual(
                    done_payload["transcript_url"],
                    "https://drive.google.com/file/d/1AbCdEf/view",
                )
                self.assertEqual(
                    done_payload["drive_file"],
                    {
                        "file_id": "1AbCdEf",
                        "folder_id": "1jwDy7XAtvX327nf0MJWZHzFERBwkbjvR",
                        "filename": "Interview_Ilya_2026-04-15.md",
                        "web_view_link": "https://drive.google.com/file/d/1AbCdEf/view",
                    },
                )

                transcript_response = client.get(f"/transcripts/{meeting_id}", headers=self.headers)
                self.assertEqual(transcript_response.status_code, 200)
                self.assertEqual(
                    transcript_response.json(),
                    {
                        "meeting_id": meeting_id,
                        "meeting_url": "https://telemost.yandex.ru/j/abc",
                        "duration_seconds": 321.5,
                        "transcript_url": "https://drive.google.com/file/d/1AbCdEf/view",
                        "drive_file": {
                            "file_id": "1AbCdEf",
                            "folder_id": "1jwDy7XAtvX327nf0MJWZHzFERBwkbjvR",
                            "filename": "Interview_Ilya_2026-04-15.md",
                            "web_view_link": "https://drive.google.com/file/d/1AbCdEf/view",
                        },
                        "segments": [
                            {
                                "speaker": "Unknown Speaker 1",
                                "start": 0.0,
                                "end": 4.2,
                                "text": "Добрый день",
                            },
                            {
                                "speaker": "Кандидат",
                                "start": 4.3,
                                "end": 9.1,
                                "text": "Здравствуйте",
                            },
                        ],
                    },
                )

    def test_leave_before_recording_finishes_as_error_and_is_idempotent(self):
        ControlledTelemostSession.reset(join_immediately=False, leave_immediately=True)
        transcribe = ControlledTranscribe([], immediate=True)
        drive_upload = ControlledDriveUpload()

        with patch.object(self.main, "TelemostSession", ControlledTelemostSession), patch.object(
            self.main, "AudioCapture", FakeAudioCapture
        ), patch.object(self.main, "_transcribe", new=transcribe), patch.object(
            self.main, "_upload_transcript_to_drive", new=drive_upload
        ):
            with TestClient(self.main.app) as client:
                join_response = client.post(
                    "/join",
                    headers=self.headers,
                    json={
                        "meeting_url": "https://telemost.yandex.ru/j/pending",
                        "bot_name": "Интервью-бот",
                    },
                )
                self.assertEqual(join_response.status_code, 200)
                meeting_id = join_response.json()["meeting_id"]

                pending_status = client.get(f"/status/{meeting_id}", headers=self.headers)
                self.assertEqual(pending_status.status_code, 200)
                self.assertEqual(pending_status.json()["status"], "pending")

                leave_response = client.post(f"/leave/{meeting_id}", headers=self.headers)
                self.assertEqual(leave_response.status_code, 200)
                self.assertEqual(leave_response.json()["status"], "error")
                self.assertEqual(
                    leave_response.json()["error_message"],
                    "Meeting was stopped before recording started",
                )

                second_leave = client.post(f"/leave/{meeting_id}", headers=self.headers)
                self.assertEqual(second_leave.status_code, 200)
                self.assertEqual(second_leave.json()["status"], "error")
                self.assertEqual(
                    second_leave.json()["error_message"],
                    "Meeting was stopped before recording started",
                )

                transcript_response = client.get(f"/transcripts/{meeting_id}", headers=self.headers)
                self.assertEqual(transcript_response.status_code, 409)

    def test_leave_is_idempotent_across_recording_leaving_transcribing_and_done(self):
        ControlledTelemostSession.reset(join_immediately=True, leave_immediately=False)
        transcribe = ControlledTranscribe(
            [
                {"speaker": "Илья", "start": 0.0, "end": 1.0, "text": "Привет"},
            ],
            immediate=False,
        )
        drive_upload = ControlledDriveUpload()

        with patch.object(self.main, "TelemostSession", ControlledTelemostSession), patch.object(
            self.main, "AudioCapture", FakeAudioCapture
        ), patch.object(self.main, "_transcribe", new=transcribe), patch.object(
            self.main, "_upload_transcript_to_drive", new=drive_upload
        ):
            with TestClient(self.main.app) as client:
                join_response = client.post(
                    "/join",
                    headers=self.headers,
                    json={
                        "meeting_url": "https://telemost.yandex.ru/j/stages",
                        "bot_name": "Интервью-бот",
                    },
                )
                meeting_id = join_response.json()["meeting_id"]

                self._wait_for_status(client, meeting_id, "recording")
                recording_leave = client.post(f"/leave/{meeting_id}", headers=self.headers)
                self.assertEqual(recording_leave.status_code, 200)
                self.assertIn(recording_leave.json()["status"], {"recording", "leaving"})

                leaving_payload = self._wait_for_status(client, meeting_id, "leaving")
                self.assertEqual(leaving_payload["status"], "leaving")
                leaving_leave = client.post(f"/leave/{meeting_id}", headers=self.headers)
                self.assertEqual(leaving_leave.status_code, 200)
                self.assertEqual(leaving_leave.json()["status"], "leaving")

                ControlledTelemostSession._allow_leave.set()
                transcribing_payload = self._wait_for_status(client, meeting_id, "transcribing")
                self.assertEqual(transcribing_payload["status"], "transcribing")

                transcribing_leave = client.post(f"/leave/{meeting_id}", headers=self.headers)
                self.assertEqual(transcribing_leave.status_code, 200)
                self.assertEqual(transcribing_leave.json()["status"], "transcribing")

                transcript_not_ready = client.get(f"/transcripts/{meeting_id}", headers=self.headers)
                self.assertEqual(transcript_not_ready.status_code, 409)

                transcribe.allow()
                done_payload = self._wait_for_status(client, meeting_id, "done")
                self.assertEqual(done_payload["status"], "done")

                done_leave = client.post(f"/leave/{meeting_id}", headers=self.headers)
                self.assertEqual(done_leave.status_code, 200)
                self.assertEqual(done_leave.json()["status"], "done")

    def test_drive_upload_failure_sets_error_instead_of_done(self):
        ControlledTelemostSession.reset(join_immediately=True, leave_immediately=True)
        transcribe = ControlledTranscribe(
            [
                {"speaker": "Илья", "start": 0.0, "end": 1.0, "text": "Привет"},
            ]
        )
        drive_upload = ControlledDriveUpload(
            error=RuntimeError("Google Drive upload failed or returned incomplete metadata")
        )

        with patch.object(self.main, "TelemostSession", ControlledTelemostSession), patch.object(
            self.main, "AudioCapture", FakeAudioCapture
        ), patch.object(self.main, "_transcribe", new=transcribe), patch.object(
            self.main, "_upload_transcript_to_drive", new=drive_upload
        ):
            with TestClient(self.main.app) as client:
                join_response = client.post(
                    "/join",
                    headers=self.headers,
                    json={
                        "meeting_url": "https://telemost.yandex.ru/j/upload-fail",
                        "bot_name": "Интервью-бот",
                    },
                )
                meeting_id = join_response.json()["meeting_id"]

                self._wait_for_status(client, meeting_id, "recording")
                leave_response = client.post(f"/leave/{meeting_id}", headers=self.headers)
                self.assertEqual(leave_response.status_code, 200)

                error_payload = self._wait_for_status(client, meeting_id, "error")
                self.assertEqual(
                    error_payload["error_message"],
                    "Google Drive upload failed or returned incomplete metadata",
                )
                self.assertIsNone(error_payload["transcript_url"])
                self.assertIsNone(error_payload["drive_file"])

                transcript_response = client.get(f"/transcripts/{meeting_id}", headers=self.headers)
                self.assertEqual(transcript_response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
