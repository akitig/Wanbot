"""テスト用期限と、期限設定変更時の計測更新。"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wanbot.welcome.waiting_clock_repository import (
    WaitingClockRepository,
    due_action,
)
from wanbot.welcome.waiting_limits import DEFAULT_LIMITS, WaitingLimits
from wanbot.welcome.waiting_monitor import WaitingMonitor
from wanbot.welcome.waiting_policy import WaitingDecision


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
SID = "a" * 32
SHORT = WaitingLimits(120, 180)
DECISION = WaitingDecision("applicant", "introduction")


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, change):
        updated = change(deepcopy(self.data))
        self.data = deepcopy(updated)


async def observe(repo, seconds=0):
    return await repo.observe(
        SID,
        decision=DECISION,
        progress_key="question:1",
        now=NOW + timedelta(seconds=seconds),
    )


def test_two_and_three_minute_boundaries():
    async def scenario():
        repo = WaitingClockRepository(
            None, 100, store=MemoryStore(), limits=SHORT
        )
        record = await observe(repo)
        assert due_action(
            record, now=NOW + timedelta(seconds=119), limits=SHORT
        ) is None
        assert due_action(
            record, now=NOW + timedelta(seconds=120), limits=SHORT
        ) == "remind"
        assert due_action(
            record, now=NOW + timedelta(seconds=180), limits=SHORT
        ) == "review"

        reminded = await repo.mark_reminded(
            SID,
            expected_epoch=record["epoch"],
            now=NOW + timedelta(seconds=120),
        )
        assert due_action(
            reminded, now=NOW + timedelta(seconds=121), limits=SHORT
        ) is None

    asyncio.run(scenario())


def test_same_short_settings_survive_restart():
    async def scenario():
        store = MemoryStore()
        first_repo = WaitingClockRepository(
            None, 100, store=store, limits=SHORT
        )
        first = await observe(first_repo)
        restarted = WaitingClockRepository(
            None, 100, store=store, limits=SHORT
        )
        second = await observe(restarted, 100)

        assert second["epoch"] == first["epoch"]
        assert second["started_at"] == first["started_at"]

    asyncio.run(scenario())


def test_shortening_existing_timer_starts_from_change():
    async def scenario():
        store = MemoryStore()
        normal = WaitingClockRepository(None, 100, store=store)
        first = await observe(normal)
        short = WaitingClockRepository(None, 100, store=store, limits=SHORT)
        changed = await observe(short, 10000)

        assert changed["epoch"] != first["epoch"]
        assert due_action(
            changed, now=NOW + timedelta(seconds=10000), limits=SHORT
        ) is None
        assert due_action(
            changed, now=NOW + timedelta(seconds=10180), limits=SHORT
        ) == "review"

    asyncio.run(scenario())


def test_returning_to_normal_settings_starts_new_timer():
    async def scenario():
        store = MemoryStore()
        short = WaitingClockRepository(None, 100, store=store, limits=SHORT)
        first = await observe(short)
        normal = WaitingClockRepository(None, 100, store=store)
        changed = await observe(normal, 200)

        assert changed["epoch"] != first["epoch"]
        assert due_action(
            changed, now=NOW + timedelta(seconds=200),
            limits=DEFAULT_LIMITS,
        ) is None

    asyncio.run(scenario())


def test_monitor_uses_configured_deadlines():
    async def scenario():
        store = MemoryStore()
        repository = WaitingClockRepository(
            None, 100, store=store, limits=SHORT
        )
        time = [NOW]
        controller = SimpleNamespace(
            _closed=False,
            _context=SimpleNamespace(
                session_id=SID,
                guild_id=100,
                is_available=lambda: True,
            ),
            _clock=lambda: time[0],
            _lock=asyncio.Lock(),
        )
        reader = SimpleNamespace(
            read_locked=AsyncMock(return_value=SimpleNamespace(
                decision=DECISION,
                progress_key="question:1",
            ))
        )
        monitor = WaitingMonitor(
            controller=controller, reader=reader, repository=repository
        )
        await monitor.refresh()
        time[0] = NOW + timedelta(seconds=180)

        async with controller._lock:
            record, action = await monitor.candidate_locked()

        assert action == "review"

    asyncio.run(scenario())
