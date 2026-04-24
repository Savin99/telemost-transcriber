"""Тесты /admin/api/metrics/* — агрегация поверх admin_meta.metrics."""

import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class AdminMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tempdir.name) / "admin-metrics.db"
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
                "ADMIN_SETTINGS_PATH": str(
                    Path(self.tempdir.name) / "admin_settings.json"
                ),
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
        from app.database import (
            Meeting,
            TranscriptSegmentDB,
            async_session,
            init_db,
        )

        await init_db()
        async with async_session() as session:
            # Две встречи сегодня + одна вчера + одна 10 дней назад + soft-deleted
            today = _iso(0)
            yesterday = _iso(1)
            ten_days_ago = _iso(10)
            deleted = _iso(2)

            m1 = Meeting(
                id="m1",
                meeting_url="https://x/1",
                bot_name="B",
                status="done",
                created_at=today,
                admin_meta=json.dumps(
                    {
                        "ai_status": {"speaker_refinement": "applied (3 changes)"},
                        "metrics": {
                            "modal_cost_usd": 0.10,
                            "claude_cost_usd": 0.02,
                            "pyannote_confidence": 0.9,
                        },
                    }
                ),
            )
            m2 = Meeting(
                id="m2",
                meeting_url="https://x/2",
                bot_name="B",
                status="done",
                created_at=today,
                admin_meta=json.dumps(
                    {
                        "ai_status": {"transcript_refinement": "applied (5 changes)"},
                        "metrics": {
                            "modal_cost_usd": 0.05,
                            "claude_cost_usd": 0.03,
                            "pyannote_confidence": 0.7,
                        },
                    }
                ),
            )
            m3 = Meeting(
                id="m3",
                meeting_url="https://x/3",
                bot_name="B",
                status="done",
                created_at=yesterday,
                admin_meta=json.dumps(
                    {
                        "ai_status": {
                            "speaker_refinement": "disabled",
                            "transcript_refinement": "disabled",
                        },
                        "metrics": {
                            "modal_cost_usd": 0.20,
                            "claude_cost_usd": 0.00,
                        },
                    }
                ),
            )
            m4 = Meeting(
                id="m4",
                meeting_url="https://x/4",
                bot_name="B",
                status="done",
                created_at=ten_days_ago,
                admin_meta=json.dumps(
                    {
                        "metrics": {
                            "modal_cost_usd": 1.00,
                            "claude_cost_usd": 0.50,
                        },
                    }
                ),
            )
            m_del = Meeting(
                id="m_del",
                meeting_url="https://x/del",
                bot_name="B",
                status="done",
                created_at=deleted,
                admin_meta=json.dumps(
                    {
                        "deleted_at": deleted,
                        "metrics": {
                            "modal_cost_usd": 999.0,
                            "claude_cost_usd": 999.0,
                        },
                    }
                ),
            )
            session.add_all([m1, m2, m3, m4, m_del])

            # Сегменты: m1 — 1 unknown из 2, m2 — 0 unknown, m3 — всё speaker_*
            segments = [
                ("m1", "Илья", 0, 5),
                ("m1", "SPEAKER_02", 5, 10),
                ("m2", "Илья", 0, 10),
                ("m3", "SPEAKER_01", 0, 5),
                ("m3", "SPEAKER_02", 5, 10),
            ]
            for meeting_id, speaker, s, e in segments:
                session.add(
                    TranscriptSegmentDB(
                        meeting_id=meeting_id,
                        speaker=speaker,
                        start_time=float(s),
                        end_time=float(e),
                        text="…",
                    )
                )
            await session.commit()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_daily_requires_auth(self):
        with TestClient(self.main.app) as client:
            response = client.get("/admin/api/metrics/daily")
        self.assertEqual(response.status_code, 401)

    def test_daily_aggregates_last_7_days(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/metrics/daily?range=7", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        items = response.json()
        self.assertIsInstance(items, list)
        # m4 (10 дней назад) и m_del (soft-deleted) не должны попасть.
        dates = [it["date"] for it in items]
        today_date = _iso(0)[:10]
        yesterday_date = _iso(1)[:10]
        self.assertIn(today_date, dates)
        self.assertIn(yesterday_date, dates)
        today_row = next(it for it in items if it["date"] == today_date)
        # m1 + m2: modal=0.15, claude=0.05
        self.assertAlmostEqual(today_row["modal_cost_usd"], 0.15, places=4)
        self.assertAlmostEqual(today_row["claude_cost_usd"], 0.05, places=4)
        self.assertEqual(today_row["meetings"], 2)
        # avg_unknown: m1=50%, m2=0% → среднее 25%
        self.assertAlmostEqual(today_row["avg_unknown_pct"], 25.0, places=1)

    def test_daily_30d_includes_older_meetings(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/metrics/daily?range=30", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        items = response.json()
        ten_days_ago_date = _iso(10)[:10]
        dates = [it["date"] for it in items]
        self.assertIn(ten_days_ago_date, dates)

    def test_projection_exp_avg(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/metrics/projection", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # 7-day: m1+m2+m3 = 0.40 → 0.40/7 ≈ 0.0571
        self.assertGreater(body["daily_7d"], 0.0)
        self.assertGreater(body["monthly"], 0.0)
        self.assertGreater(body["yearly"], body["monthly"])
        # daily_30d включает m4 → выше чем только 7d (m4 большая стоимость 1.5 USD
        # делится на 30, что больше чем 0.40/7)
        self.assertGreater(body["daily_30d"], 0.0)

    def test_quality(self):
        with TestClient(self.main.app) as client:
            self._run(self._seed())
            response = client.get("/admin/api/metrics/quality", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # m1, m2 — applied; m3 — disabled; m4 — без ai_status → refiner_pct = 2/4 = 50
        # (m_del исключён через deleted_at)
        self.assertAlmostEqual(body["refiner_applied_pct"], 50.0, places=1)
        # median confidence из m1(0.9), m2(0.7) → 0.8
        self.assertAlmostEqual(body["pyannote_median_confidence"], 0.8, places=2)
        # unknown_speaker_pct: m1=50%, m2=0%, m3=100% → среднее ~50%
        self.assertGreater(body["unknown_speaker_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
