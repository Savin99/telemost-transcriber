import asyncio
import importlib
import os
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
        "app.api_v1_models",
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
        # «Записываем» файл, чтобы аудио-endpoint мог его отдать.
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.output_path).write_bytes(b"RIFF-fake-wav-header")

    async def stop(self):
        if not self.started and not self.stopped:
            return None
        self.started = False
        self.stopped = True
        return self.final_duration


class ControlledV1Transcribe:
    """Stub для `_transcribe`: принимает любые kwargs, пишет их в self.calls."""

    def __init__(
        self,
        segments: list[dict] | None = None,
        *,
        enrolled_voiceprints: list[dict] | None = None,
        immediate: bool = True,
        ai_status: dict | None = None,
    ):
        self._segments = [dict(seg) for seg in (segments or [])]
        self._enrolled = [dict(entry) for entry in (enrolled_voiceprints or [])]
        self._ai_status = dict(ai_status or {})
        self._allow = threading.Event()
        self.calls: list[dict] = []
        if immediate:
            self._allow.set()

    def allow(self):
        self._allow.set()

    async def __call__(self, recording_path: str, num_speakers=None, **kwargs):
        while not self._allow.is_set():
            await asyncio.sleep(0.01)
        self.calls.append(
            {"recording_path": recording_path, "num_speakers": num_speakers, **kwargs}
        )
        return {
            "segments": [dict(seg) for seg in self._segments],
            "ai_status": {
                "speaker_refinement": self._ai_status.get(
                    "speaker_refinement", "disabled"
                ),
                "transcript_refinement": self._ai_status.get(
                    "transcript_refinement", "disabled"
                ),
            },
            "enrolled_voiceprints": [dict(entry) for entry in self._enrolled],
        }


class ControlledDriveUpload:
    """Stub Google-Drive upload; по умолчанию имитирует успех."""

    def __init__(
        self,
        result: dict[str, str] | None = None,
        *,
        error: Exception | None = None,
    ):
        self.result = dict(
            result
            or {
                "file_id": "1AbCdEf",
                "folder_id": "1jwDy7XAtvX327nf0MJWZHzFERBwkbjvR",
                "filename": "Interview_2026-04-17.md",
                "web_view_link": "https://drive.google.com/file/d/1AbCdEf/view",
            }
        )
        self.error = error
        self.calls: list[dict] = []

    async def __call__(self, transcript: dict, *, source_filename: str | None = None):
        self.calls.append(
            {"transcript": dict(transcript), "source_filename": source_filename}
        )
        if self.error is not None:
            raise self.error
        return dict(self.result)


class V1ApiContractTests(unittest.TestCase):
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
                "PUBLIC_BASE_URL": "https://transcribe.test",
                "MAX_CONCURRENT_JOBS": "4",
                "AUDIO_RETENTION_DAYS": "30",
                "AUDIO_CLEANUP_INTERVAL_SEC": "3600",
            },
            clear=False,
        )
        self.env.start()
        self.main = self._reload_main()
        self.headers = {"Authorization": "Bearer supersecret"}

    def tearDown(self):
        self.env.stop()
        _clear_app_modules()
        self.tempdir.cleanup()

    def _reload_main(self):
        _clear_app_modules()
        importlib.invalidate_caches()
        return importlib.import_module("app.main")

    def _wait_for_v1_status(
        self,
        client: TestClient,
        job_id: str,
        expected_status: str,
        timeout: float = 5.0,
    ) -> dict:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            response = client.get(f"/v1/jobs/{job_id}", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            last = response.json()
            if last["status"] == expected_status:
                return last
            time.sleep(0.05)
        self.fail(f"Timed out waiting for {expected_status}; last payload: {last}")

    def _signal_meeting_ended(self, timeout: float = 5.0) -> None:
        """Ждём, пока workflow создаст TelemostSession, и дёргаем событие конца."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if ControlledTelemostSession.instances:
                session = ControlledTelemostSession.instances[-1]
                # В рабочем сервисе событие выставляется Telemost'ом при завершении
                # звонка — тут имитируем это.
                session._meeting_ended.set()
                return
            time.sleep(0.05)
        self.fail("TelemostSession never instantiated")

    def _body(
        self, session_id="s-1", url="https://telemost.yandex.ru/j/abc", **speakers
    ):
        return {
            "source": {"type": "telemost", "url": url},
            "metadata": {
                "session_id": session_id,
                "session_type": "interview",
                "language": "ru",
            },
            "speakers": speakers
            or {
                "recruiter": {
                    "first_name": "Илья",
                    "last_name": "Савин",
                    "tg_id": 835411533,
                    "voice_bank_id": "recruiter-ilya-savin",
                    "enrolled": True,
                },
                "candidate": {
                    "first_name": "Иван",
                    "last_name": "Петров",
                    "person_id": 456,
                    "voice_bank_id": None,
                    "enrolled": False,
                },
            },
            "options": {
                "diarize": True,
                "llm_refine": False,
                "initial_prompt": "Интервью.",
            },
            "callback_url": None,
        }

    # ------ auth ------

    def test_missing_bearer_returns_401(self):
        with TestClient(self.main.app) as client:
            response = client.post("/v1/jobs", json=self._body())
            self.assertEqual(response.status_code, 401)

    def test_wrong_bearer_returns_401(self):
        with TestClient(self.main.app) as client:
            response = client.post(
                "/v1/jobs",
                headers={"Authorization": "Bearer wrong"},
                json=self._body(),
            )
            self.assertEqual(response.status_code, 401)

    def test_x_api_key_is_rejected_on_v1(self):
        # /v1 требует строго Bearer.
        with TestClient(self.main.app) as client:
            response = client.post(
                "/v1/jobs",
                headers={"X-API-Key": "supersecret"},
                json=self._body(),
            )
            self.assertEqual(response.status_code, 401)

    def test_get_unknown_job_returns_404(self):
        with TestClient(self.main.app) as client:
            response = client.get("/v1/jobs/unknown", headers=self.headers)
            self.assertEqual(response.status_code, 404)

    # ------ happy path ------

    def test_create_then_get_status_progression(self):
        ControlledTelemostSession.reset(join_immediately=True, leave_immediately=True)
        transcribe = ControlledV1Transcribe(
            segments=[
                {
                    "speaker": "Илья Савин",
                    "start": 0.0,
                    "end": 3.2,
                    "text": "Здравствуйте, начнём интервью.",
                },
                {
                    "speaker": "Иван Петров",
                    "start": 3.5,
                    "end": 8.1,
                    "text": "Добрый день, готов.",
                },
            ],
            enrolled_voiceprints=[
                {
                    "voice_bank_id": "ivan-petrov-deadbeef",
                    "display_name": "Иван Петров",
                    "person_id": 456,
                    "role": "candidate",
                }
            ],
        )
        drive_upload = ControlledDriveUpload()

        with (
            patch.object(self.main, "TelemostSession", ControlledTelemostSession),
            patch.object(self.main, "AudioCapture", FakeAudioCapture),
            patch.object(self.main, "_transcribe", new=transcribe),
            patch.object(self.main, "_upload_transcript_to_drive", new=drive_upload),
        ):
            with TestClient(self.main.app) as client:
                create = client.post(
                    "/v1/jobs", headers=self.headers, json=self._body()
                )
                self.assertEqual(create.status_code, 202)
                created = create.json()
                self.assertEqual(created["status"], "queued")
                job_id = created["job_id"]
                self.assertRegex(
                    created["created_at"],
                    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
                )

                self._wait_for_v1_status(client, job_id, "recording")
                self._signal_meeting_ended()
                done = self._wait_for_v1_status(client, job_id, "done")
                self.assertEqual(done["status"], "done")
                self.assertIn("result", done)
                result = done["result"]
                self.assertAlmostEqual(result["duration_sec"], 321.5)
                self.assertEqual(result["language"], "ru")
                self.assertEqual(
                    result["audio_url"],
                    f"https://transcribe.test/audio/{job_id}.wav",
                )
                self.assertEqual(result["audio_retention_days"], 30)
                self.assertEqual(
                    result["enrolled_voiceprints"],
                    [{"voice_bank_id": "ivan-petrov-deadbeef", "person_id": 456}],
                )
                self.assertEqual(len(result["segments"]), 2)
                self.assertEqual(result["segments"][0]["speaker"], "Илья Савин")
                self.assertEqual(result["segments"][0]["speaker_role"], "recruiter")
                self.assertEqual(result["segments"][1]["speaker"], "Иван Петров")
                self.assertEqual(result["segments"][1]["speaker_role"], "candidate")

                # Проверяем, что в transcribe ушёл speakers_hint и initial_prompt.
                self.assertEqual(len(transcribe.calls), 1)
                call = transcribe.calls[0]
                self.assertEqual(call["initial_prompt"], "Интервью.")
                self.assertTrue(call["auto_enroll_unknown"])  # unenrolled candidate
                hints = call["speakers_hint"]
                self.assertEqual(len(hints), 2)
                by_role = {hint["role"]: hint for hint in hints}
                self.assertEqual(by_role["recruiter"]["display_name"], "Илья Савин")
                self.assertTrue(by_role["recruiter"]["enrolled"])
                self.assertEqual(
                    by_role["recruiter"]["voice_bank_id"], "recruiter-ilya-savin"
                )
                self.assertEqual(by_role["candidate"]["display_name"], "Иван Петров")
                self.assertFalse(by_role["candidate"]["enrolled"])

                # Drive-загрузка выполнилась — ровно один раз с корректным source_filename.
                self.assertEqual(len(drive_upload.calls), 1)
                self.assertEqual(
                    drive_upload.calls[0]["source_filename"], f"{job_id}.wav"
                )

    def test_drive_upload_failure_still_marks_done(self):
        """Drive — best-effort: его падение не ломает публичный статус."""
        ControlledTelemostSession.reset(join_immediately=True, leave_immediately=True)
        transcribe = ControlledV1Transcribe(
            segments=[
                {"speaker": "Илья Савин", "start": 0.0, "end": 1.0, "text": "Раз."},
            ],
        )
        drive_upload = ControlledDriveUpload(
            error=RuntimeError("Google Drive upload failed"),
        )

        with (
            patch.object(self.main, "TelemostSession", ControlledTelemostSession),
            patch.object(self.main, "AudioCapture", FakeAudioCapture),
            patch.object(self.main, "_transcribe", new=transcribe),
            patch.object(self.main, "_upload_transcript_to_drive", new=drive_upload),
        ):
            with TestClient(self.main.app) as client:
                create = client.post(
                    "/v1/jobs", headers=self.headers, json=self._body("drive-fail-1")
                )
                job_id = create.json()["job_id"]
                self._wait_for_v1_status(client, job_id, "recording")
                self._signal_meeting_ended()
                done = self._wait_for_v1_status(client, job_id, "done")

                self.assertEqual(done["status"], "done")
                # transcript_url в результат /v1 не входит, но сам Drive стаб
                # дёрнули ровно один раз.
                self.assertEqual(len(drive_upload.calls), 1)

    def test_audio_endpoint_serves_file_then_honours_retention(self):
        ControlledTelemostSession.reset(join_immediately=True, leave_immediately=True)
        transcribe = ControlledV1Transcribe(
            segments=[
                {"speaker": "Илья Савин", "start": 0.0, "end": 1.0, "text": "Раз."},
            ],
        )
        drive_upload = ControlledDriveUpload()

        with (
            patch.object(self.main, "TelemostSession", ControlledTelemostSession),
            patch.object(self.main, "AudioCapture", FakeAudioCapture),
            patch.object(self.main, "_transcribe", new=transcribe),
            patch.object(self.main, "_upload_transcript_to_drive", new=drive_upload),
        ):
            with TestClient(self.main.app) as client:
                create = client.post(
                    "/v1/jobs", headers=self.headers, json=self._body("audio-session")
                )
                job_id = create.json()["job_id"]
                self._wait_for_v1_status(client, job_id, "recording")
                self._signal_meeting_ended()
                self._wait_for_v1_status(client, job_id, "done")

                audio = client.get(f"/audio/{job_id}.wav", headers=self.headers)
                self.assertEqual(audio.status_code, 200)
                self.assertIn("audio/wav", audio.headers["content-type"])
                self.assertGreater(len(audio.content), 0)

                # Нет bearer → 401
                no_auth = client.get(f"/audio/{job_id}.wav")
                self.assertEqual(no_auth.status_code, 401)

                # Помечаем retention как истёкший — endpoint должен отдать 410
                import asyncio as _asyncio
                from sqlalchemy import update as _update

                async def _expire():
                    async with self.main.async_session() as session:
                        await session.execute(
                            _update(self.main.Meeting)
                            .where(self.main.Meeting.id == job_id)
                            .values(
                                audio_retention_expires_at="2000-01-01T00:00:00+00:00"
                            )
                        )
                        await session.commit()

                _asyncio.run(_expire())
                expired = client.get(f"/audio/{job_id}.wav", headers=self.headers)
                self.assertEqual(expired.status_code, 410)
                self.assertEqual(expired.json()["error"], "audio_expired")

    def test_idempotent_session_id_returns_409(self):
        ControlledTelemostSession.reset(join_immediately=False, leave_immediately=True)
        transcribe = ControlledV1Transcribe(segments=[], immediate=False)

        with (
            patch.object(self.main, "TelemostSession", ControlledTelemostSession),
            patch.object(self.main, "AudioCapture", FakeAudioCapture),
            patch.object(self.main, "_transcribe", new=transcribe),
        ):
            with TestClient(self.main.app) as client:
                first = client.post(
                    "/v1/jobs", headers=self.headers, json=self._body("dup-session")
                )
                self.assertEqual(first.status_code, 202)
                job_id = first.json()["job_id"]

                second = client.post(
                    "/v1/jobs", headers=self.headers, json=self._body("dup-session")
                )
                self.assertEqual(second.status_code, 409)
                payload = second.json()
                self.assertEqual(payload["job_id"], job_id)
                self.assertEqual(payload["error"], "session_already_processing")
                self.assertIn(
                    payload["status"],
                    {"queued", "connecting", "recording", "transcribing"},
                )

    def test_concurrency_limit_returns_429(self):
        ControlledTelemostSession.reset(join_immediately=False, leave_immediately=True)
        transcribe = ControlledV1Transcribe(segments=[], immediate=False)

        with (
            patch.object(self.main, "TelemostSession", ControlledTelemostSession),
            patch.object(self.main, "AudioCapture", FakeAudioCapture),
            patch.object(self.main, "_transcribe", new=transcribe),
            patch.object(self.main, "MAX_CONCURRENT_JOBS", 2),
        ):
            with TestClient(self.main.app) as client:
                first = client.post(
                    "/v1/jobs", headers=self.headers, json=self._body("limit-1")
                )
                second = client.post(
                    "/v1/jobs", headers=self.headers, json=self._body("limit-2")
                )
                third = client.post(
                    "/v1/jobs", headers=self.headers, json=self._body("limit-3")
                )
                self.assertEqual(first.status_code, 202)
                self.assertEqual(second.status_code, 202)
                self.assertEqual(third.status_code, 429)
                self.assertEqual(third.json()["error"], "rate_limited")
                self.assertEqual(third.json()["retry_after_sec"], 15)

    def test_delete_during_pending_marks_cancelled(self):
        ControlledTelemostSession.reset(join_immediately=False, leave_immediately=True)
        transcribe = ControlledV1Transcribe(segments=[], immediate=False)

        with (
            patch.object(self.main, "TelemostSession", ControlledTelemostSession),
            patch.object(self.main, "AudioCapture", FakeAudioCapture),
            patch.object(self.main, "_transcribe", new=transcribe),
        ):
            with TestClient(self.main.app) as client:
                create = client.post(
                    "/v1/jobs", headers=self.headers, json=self._body("cancel-1")
                )
                job_id = create.json()["job_id"]

                delete_response = client.delete(
                    f"/v1/jobs/{job_id}", headers=self.headers
                )
                self.assertEqual(delete_response.status_code, 204)

                cancelled = self._wait_for_v1_status(client, job_id, "cancelled")
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertNotIn("error", {k for k, v in cancelled.items() if v})


if __name__ == "__main__":
    unittest.main()
