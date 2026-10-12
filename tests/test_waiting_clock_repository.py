"""本人期限の保存・再開・通知時刻を検証する。"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from wanbot.welcome.waiting_clock_repository import (
    WaitingClockConflict,
    WaitingClockRepository,
    due_action,
)
from wanbot.welcome.waiting_policy import WaitingDecision


SID = "a" * 32
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
APPLICANT = WaitingDecision("applicant", "introduction")
STAFF = WaitingDecision("staff", "meeting_selection")
UNKNOWN = WaitingDecision("unknown", "rules_status_unavailable")


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, change):
        updated = change(deepcopy(self.data))
        self.data = deepcopy(updated)


def repository(store=None):
    return WaitingClockRepository(
        None, 100, store=store if store is not None else MemoryStore()
    )


async def observe(repo, hours=0, *, decision=APPLICANT, key="question:1"):
    return await repo.observe(
        SID,
        decision=decision,
        progress_key=key,
        now=NOW + timedelta(hours=hours),
    )


def test_reminder_and_review_boundaries():
    async def scenario():
        repo = repository()
        record = await observe(repo)
        assert due_action(
            record, now=NOW + timedelta(hours=48, microseconds=-1)
        ) is None
        assert due_action(
            record, now=NOW + timedelta(hours=48)
        ) == "remind"
        assert due_action(
            record, now=NOW + timedelta(hours=72)
        ) == "review"

    asyncio.run(scenario())


def test_restart_and_repeated_observation_do_not_extend_deadline():
    async def scenario():
        repo = repository()
        first = await observe(repo)
        reloaded = repository(repo._store)
        latest = await observe(reloaded, 50)

        assert latest["epoch"] == first["epoch"]
        assert latest["started_at"] == first["started_at"]
        assert due_action(
            latest, now=NOW + timedelta(hours=72)
        ) == "review"

    asyncio.run(scenario())


def test_saved_progress_starts_new_deadline():
    async def scenario():
        repo = repository()
        original = await observe(repo)
        updated = await observe(repo, 47, key="question:2")

        assert updated["epoch"] != original["epoch"]
        assert due_action(
            updated, now=NOW + timedelta(hours=72)
        ) is None
        assert due_action(
            updated, now=NOW + timedelta(hours=95)
        ) == "remind"

    asyncio.run(scenario())


def test_staff_waiting_ends_timer():
    async def scenario():
        repo = repository()
        await observe(repo)
        record = await observe(repo, 47, decision=STAFF)

        assert record["started_at"] is None
        assert due_action(
            record, now=NOW + timedelta(days=30)
        ) is None

        resumed = await observe(repo, 100, key="interview:2")
        assert due_action(
            resumed, now=NOW + timedelta(hours=101)
        ) is None

    asyncio.run(scenario())


def test_unknown_state_pauses_without_counting_pause():
    async def scenario():
        repo = repository()
        original = await observe(repo)
        paused = await observe(repo, 24, decision=UNKNOWN)
        assert due_action(
            paused, now=NOW + timedelta(hours=100)
        ) is None

        await observe(repo, 80, decision=UNKNOWN)
        resumed = await observe(repo, 100)
        assert resumed["epoch"] == original["epoch"]

        # 本人待ち24時間 + 再開後24時間で48時間になる。
        assert due_action(
            resumed, now=NOW + timedelta(hours=123)
        ) is None
        assert due_action(
            resumed, now=NOW + timedelta(hours=124)
        ) == "remind"

    asyncio.run(scenario())


def test_reminder_is_recorded_and_not_repeated_after_reload():
    async def scenario():
        repo = repository()
        first = await observe(repo)
        await repo.mark_reminded(
            SID, expected_epoch=first["epoch"],
            now=NOW + timedelta(hours=48),
        )

        reloaded = repository(repo._store)
        record = await reloaded.get(SID)
        assert due_action(
            record, now=NOW + timedelta(hours=49)
        ) is None
        assert due_action(
            record, now=NOW + timedelta(hours=72)
        ) == "review"

    asyncio.run(scenario())


def test_old_reminder_cannot_mark_new_timer():
    async def scenario():
        repo = repository()
        old = await observe(repo)
        await observe(repo, 47, key="question:2")
        before = await repo._store.read()

        with pytest.raises(WaitingClockConflict):
            await repo.mark_reminded(
                SID, expected_epoch=old["epoch"],
                now=NOW + timedelta(hours=96),
            )

        assert await repo._store.read() == before

    asyncio.run(scenario())


def test_backwards_time_does_not_modify_storage():
    async def scenario():
        repo = repository()
        await observe(repo, 10)
        before = await repo._store.read()

        with pytest.raises(WaitingClockConflict):
            await observe(repo, 9)

        assert await repo._store.read() == before

    asyncio.run(scenario())


def test_reminder_cannot_be_marked_before_48_hours():
    async def scenario():
        repo = repository()
        record = await observe(repo)

        with pytest.raises(WaitingClockConflict):
            await repo.mark_reminded(
                SID, expected_epoch=record["epoch"],
                now=NOW + timedelta(hours=47),
            )

        assert not (await repo.get(SID))["reminded"]

    asyncio.run(scenario())
