"""進行状態の読み取りと、本人期限の保存の接続。"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wanbot.welcome.waiting_clock_repository import (
    WaitingClockRepository,
    due_action,
)
from wanbot.welcome.waiting_monitor import WaitingMonitor
from wanbot.welcome.waiting_policy import WaitingDecision


SID = "a" * 32
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, change):
        updated = change(deepcopy(self.data))
        self.data = deepcopy(updated)


def setup():
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
            decision=WaitingDecision("applicant", "introduction"),
            progress_key="question:1",
        ))
    )
    repository = WaitingClockRepository(None, 100, store=MemoryStore())
    monitor = WaitingMonitor(
        controller=controller, reader=reader, repository=repository
    )
    return monitor, controller, reader, repository, time


def test_refresh_saves_clock_without_extending_same_progress():
    async def scenario():
        monitor, controller, reader, repository, time = setup()
        first = await monitor.refresh()
        time[0] = NOW + timedelta(hours=10)
        second = await monitor.refresh()

        assert second["started_at"] == first["started_at"]
        assert second["epoch"] == first["epoch"]
        assert await repository.get(SID) == second

    asyncio.run(scenario())


def test_read_failure_pauses_and_recovery_excludes_paused_time():
    async def scenario():
        monitor, controller, reader, repository, time = setup()
        await monitor.refresh()

        time[0] = NOW + timedelta(hours=24)
        reader.read_locked.side_effect = RuntimeError("unavailable")
        paused = await monitor.refresh()
        assert paused["owner"] == "unknown"
        assert due_action(
            paused, now=NOW + timedelta(hours=100)
        ) is None

        time[0] = NOW + timedelta(hours=100)
        reader.read_locked.side_effect = None
        resumed = await monitor.refresh()
        assert resumed["owner"] == "applicant"
        assert due_action(
            resumed, now=NOW + timedelta(hours=123)
        ) is None
        assert due_action(
            resumed, now=NOW + timedelta(hours=124)
        ) == "remind"

    asyncio.run(scenario())


def test_staff_waiting_clears_applicant_timer():
    async def scenario():
        monitor, controller, reader, repository, time = setup()
        await monitor.refresh()
        time[0] = NOW + timedelta(hours=71)
        reader.read_locked.return_value = SimpleNamespace(
            decision=WaitingDecision("staff", "meeting_selection"),
            progress_key="submitted:1",
        )

        record = await monitor.refresh()

        assert record["owner"] == "staff"
        assert record["started_at"] is None

    asyncio.run(scenario())


def test_candidate_rechecks_state_before_reporting_deadline():
    async def scenario():
        monitor, controller, reader, repository, time = setup()
        await monitor.refresh()
        time[0] = NOW + timedelta(hours=73)

        # 期限は過ぎていても、現在は提出済みなら承認依頼候補にしない。
        reader.read_locked.return_value = SimpleNamespace(
            decision=WaitingDecision("staff", "meeting_selection"),
            progress_key="submitted:1",
        )
        async with controller._lock:
            record, action = await monitor.candidate_locked()

        assert record["owner"] == "staff"
        assert action is None

    asyncio.run(scenario())


def test_candidate_is_suppressed_when_state_lookup_fails():
    async def scenario():
        monitor, controller, reader, repository, time = setup()
        await monitor.refresh()
        time[0] = NOW + timedelta(hours=73)
        reader.read_locked.side_effect = RuntimeError("unavailable")

        async with controller._lock:
            record, action = await monitor.candidate_locked()

        assert record["owner"] == "unknown"
        assert action is None

    asyncio.run(scenario())


def test_closed_controller_does_not_create_clock():
    async def scenario():
        monitor, controller, reader, repository, time = setup()
        controller._closed = True

        assert await monitor.refresh() is None
        reader.read_locked.assert_not_awaited()
        assert await repository.get(SID) is None

    asyncio.run(scenario())


def test_shutdown_during_lookup_does_not_save_observation():
    async def scenario():
        monitor, controller, reader, repository, time = setup()
        observation = reader.read_locked.return_value

        async def read():
            controller._closed = True
            return observation

        reader.read_locked.side_effect = read

        assert await monitor.refresh() is None
        assert await repository.get(SID) is None

    asyncio.run(scenario())
