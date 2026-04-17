"""Тесты Telegram-бота (ключевые race conditions и хендлеры)."""

import asyncio
import importlib
import itertools
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reload_bot_module():
    os.environ.setdefault("TG_BOT_TOKEN", "123456:test-token")
    os.environ.setdefault("BOT_API_URL", "http://localhost:8000")
    os.environ.setdefault("BOT_API_KEY", "test-key")
    sys.modules.pop("bot", None)
    return importlib.import_module("bot")


def _make_msg(chat_id: int = 42, text: str | None = None) -> MagicMock:
    msg = MagicMock()
    msg.chat.id = chat_id
    msg.text = text
    status = MagicMock()
    status.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=status)
    msg._status = status
    return msg


class JoinMeetingRaceTests(unittest.IsolatedAsyncioTestCase):
    """Покрывает реальный инцидент: три параллельных handle_meeting_url
    создавали три /join для одного и того же chat_id."""

    async def asyncSetUp(self):
        self.bot_module = _reload_bot_module()
        self.bot_module.active.clear()
        self.bot_module._join_locks.clear()

    async def test_parallel_calls_same_chat_create_exactly_one_join(self):
        ids = iter(["m-1", "m-2", "m-3"])
        join_calls = 0

        class FakeResp:
            def __init__(self, data):
                self._data = data

            def raise_for_status(self):
                return None

            def json(self):
                return self._data

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None, headers=None):
                nonlocal join_calls
                join_calls += 1
                await asyncio.sleep(0.01)
                return FakeResp({"meeting_id": next(ids)})

        url = "https://telemost.yandex.ru/j/100"
        msgs = [_make_msg(chat_id=42) for _ in range(3)]

        with (
            patch.object(self.bot_module.httpx, "AsyncClient", FakeClient),
            patch.object(self.bot_module, "_auto_wait", AsyncMock(return_value=None)),
        ):
            await asyncio.gather(*(self.bot_module._join_meeting(m, url) for m in msgs))

        self.assertEqual(join_calls, 1, "expected exactly one /join call")
        self.assertIn(42, self.bot_module.active)
        self.assertEqual(self.bot_module.active[42]["url"], url)
        self.assertIn(self.bot_module.active[42]["meeting_id"], {"m-1", "m-2", "m-3"})

    async def test_different_chats_not_blocked_by_each_other(self):
        ids = itertools.count(1)
        started = asyncio.Event()

        class FakeResp:
            def __init__(self, data):
                self._data = data

            def raise_for_status(self):
                return None

            def json(self):
                return self._data

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None, headers=None):
                # Первый вызов держим подольше, чтобы ребёнок из другого чата
                # точно не ждал нашего лока.
                started.set()
                await asyncio.sleep(0.05)
                return FakeResp({"meeting_id": f"m-{next(ids)}"})

        url_a = "https://telemost.yandex.ru/j/1"
        url_b = "https://telemost.yandex.ru/j/2"
        msg_a = _make_msg(chat_id=1)
        msg_b = _make_msg(chat_id=2)

        with (
            patch.object(self.bot_module.httpx, "AsyncClient", FakeClient),
            patch.object(self.bot_module, "_auto_wait", AsyncMock(return_value=None)),
        ):
            task_a = asyncio.create_task(self.bot_module._join_meeting(msg_a, url_a))
            await started.wait()
            # Локи per-chat, значит второй чат не должен ждать первого.
            task_b = asyncio.create_task(self.bot_module._join_meeting(msg_b, url_b))
            await asyncio.gather(task_a, task_b)

        self.assertEqual(set(self.bot_module.active), {1, 2})

    async def test_failed_join_releases_slot(self):
        class FailingClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None, headers=None):
                raise RuntimeError("boom")

        msg = _make_msg(chat_id=7)
        with patch.object(self.bot_module.httpx, "AsyncClient", FailingClient):
            await self.bot_module._join_meeting(msg, "https://telemost.yandex.ru/j/9")

        self.assertNotIn(7, self.bot_module.active)
        msg._status.edit_text.assert_awaited()


if __name__ == "__main__":
    unittest.main()
