"""Тесты прокси /admin/api/voice-bank/* и /admin/api/review/* на transcriber.

Мокаем httpx через MockTransport: весь исходящий трафик на TRANSCRIBER_URL
перехватывается и отвечает детерминированно. Ничего наружу не идёт.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _clear_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


class _TranscriberStub:
    """Мини-state, в который пишут моки и читают ассерты."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}

    def set(self, method: str, path: str, response: httpx.Response):
        self.responses[(method.upper(), path)] = response

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = None
        if request.content:
            try:
                payload = json.loads(request.content)
            except ValueError:
                payload = None
        self.calls.append((request.method, request.url.path, payload))
        key = (request.method.upper(), request.url.path)
        if key in self.responses:
            return self.responses[key]
        return httpx.Response(500, json={"detail": f"Unexpected {key}"})


class AdminVoiceBankProxyTests(unittest.TestCase):
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
        self.auth = ("testadmin", "testpass")

        self.stub = _TranscriberStub()
        transport = httpx.MockTransport(self.stub.handler)

        # Подменяем httpx.AsyncClient — любой вызов идёт в MockTransport
        import httpx as _httpx_module

        original_async_client = _httpx_module.AsyncClient

        def _patched_async_client(*args, **kwargs):
            kwargs["transport"] = transport
            return original_async_client(*args, **kwargs)

        self._async_client_patch = patch.object(
            _httpx_module, "AsyncClient", _patched_async_client
        )
        self._async_client_patch.start()

    def tearDown(self):
        self._async_client_patch.stop()
        self.env.stop()
        _clear_app_modules()
        self.tempdir.cleanup()

    # --- voice-bank ---

    def test_list_speakers_requires_auth(self):
        with TestClient(self.main.app) as client:
            response = client.get("/admin/api/voice-bank/speakers")
        self.assertEqual(response.status_code, 401)

    def test_list_speakers_proxies_to_transcriber(self):
        self.stub.set(
            "GET",
            "/voice-bank/speakers",
            httpx.Response(
                200,
                json=[
                    {
                        "name": "Alice",
                        "n_samples": 3,
                        "is_known": True,
                        "confidence": None,
                        "last_seen": None,
                        "enrolled_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            ),
        )
        with TestClient(self.main.app) as client:
            response = client.get("/admin/api/voice-bank/speakers", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body[0]["name"], "Alice")
        self.assertEqual(body[0]["n_samples"], 3)
        # Проверим, что реально сходили в нужный URL
        self.assertIn(("GET", "/voice-bank/speakers", None), self.stub.calls)

    def test_delete_speaker_proxies(self):
        self.stub.set(
            "DELETE",
            "/voice-bank/Alice",
            httpx.Response(200, json={"status": "ok", "name": "Alice"}),
        )
        with TestClient(self.main.app) as client:
            response = client.delete("/admin/api/voice-bank/Alice", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_rename_speaker_proxies_payload(self):
        self.stub.set(
            "POST",
            "/voice-bank/Alice/rename",
            httpx.Response(200, json={"status": "ok", "name": "Алиса"}),
        )
        with TestClient(self.main.app) as client:
            response = client.post(
                "/admin/api/voice-bank/Alice/rename",
                json={"new_name": "Алиса"},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 200)
        # Убедимся, что мок получил корректный payload
        method, path, payload = self.stub.calls[-1]
        self.assertEqual((method, path), ("POST", "/voice-bank/Alice/rename"))
        self.assertEqual(payload, {"new_name": "Алиса"})

    def test_rename_conflict_forwards_409(self):
        self.stub.set(
            "POST",
            "/voice-bank/Alice/rename",
            httpx.Response(409, json={"detail": "Speaker 'Алиса' уже существует"}),
        )
        with TestClient(self.main.app) as client:
            response = client.post(
                "/admin/api/voice-bank/Alice/rename",
                json={"new_name": "Алиса"},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 409)

    def test_merge_proxies(self):
        self.stub.set(
            "POST",
            "/voice-bank/merge",
            httpx.Response(
                200,
                json={
                    "status": "ok",
                    "source": "AliceDup",
                    "target": "Alice",
                    "n_samples": 5,
                },
            ),
        )
        with TestClient(self.main.app) as client:
            response = client.post(
                "/admin/api/voice-bank/merge",
                json={"source": "AliceDup", "target": "Alice"},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["n_samples"], 5)

    def test_similarity_matrix_proxies(self):
        payload = {
            "Alice": {"Alice": 1.0, "Bob": 0.3},
            "Bob": {"Alice": 0.3, "Bob": 1.0},
        }
        self.stub.set(
            "GET",
            "/voice-bank/similarity-matrix",
            httpx.Response(200, json=payload),
        )
        with TestClient(self.main.app) as client:
            response = client.get(
                "/admin/api/voice-bank/similarity-matrix", auth=self.auth
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)

    # --- review ---

    def test_review_queue_requires_auth(self):
        with TestClient(self.main.app) as client:
            response = client.get("/admin/api/review")
        self.assertEqual(response.status_code, 401)

    def test_review_queue_proxies_to_transcriber(self):
        self.stub.set(
            "GET",
            "/review-queue",
            httpx.Response(
                200,
                json=[
                    {
                        "meeting_id": "m-1",
                        "cluster_label": "SPEAKER_00",
                        "confidence": 0.1,
                        "samples": [{"index": 0, "path": "/tmp/s.wav"}],
                        "candidates": [{"name": "Alice", "score": 0.92}],
                    }
                ],
            ),
        )
        with TestClient(self.main.app) as client:
            response = client.get("/admin/api/review", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["cluster_label"], "SPEAKER_00")

    def test_review_apply_proxies_to_speaker_review_label(self):
        self.stub.set(
            "POST",
            "/speaker-review/m-1/SPEAKER_00/label",
            httpx.Response(
                200,
                json={
                    "meeting_key": "m-1",
                    "speaker_label": "SPEAKER_00",
                    "previous_name": "Unknown Speaker 1",
                    "name": "Alice",
                    "merged_labels": [],
                },
            ),
        )
        with TestClient(self.main.app) as client:
            response = client.post(
                "/admin/api/review/m-1/SPEAKER_00/apply",
                json={"name": "Alice"},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 200)
        method, path, payload = self.stub.calls[-1]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/speaker-review/m-1/SPEAKER_00/label")
        self.assertEqual(payload, {"name": "Alice", "alpha": 0.05})


if __name__ == "__main__":
    unittest.main()
