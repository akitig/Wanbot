"""運営催促の間隔・再起動・重複操作を検証する。"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from wanbot.welcome.staff_reminder_repository import (
    StaffReminderConflict,
    StaffReminderRepository,
)


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
TARGET = "meeting:" + "a" * 32


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, change):
        updated = change(deepcopy(self.data))
        self.data = deepcopy(updated)


async def setup():
    store = MemoryStore()
    repo = StaffReminderRepository(None, 100, store=store)
    await repo.ensure(
        TARGET, session_id="b" * 32, channel_id=500, now=NOW
    )
    return repo, store


def test_first_reminder_becomes_due_after_one_hour():
    async def scenario():
        repo, store = await setup()
        assert await repo.claim(
            TARGET, now=NOW + timedelta(minutes=59)
        ) is None
        assert await repo.claim(
            TARGET, now=NOW + timedelta(hours=1)
        ) is not None

    asyncio.run(scenario())


def test_restart_does_not_reset_due_time():
    async def scenario():
        repo, store = await setup()
        restarted = StaffReminderRepository(None, 100, store=store)
        await restarted.ensure(
            TARGET,
            session_id="b" * 32,
            channel_id=500,
            now=NOW + timedelta(minutes=50),
        )

        assert await restarted.claim(
            TARGET, now=NOW + timedelta(hours=1)
        ) is not None

    asyncio.run(scenario())


def test_same_time_cannot_claim_twice():
    async def scenario():
        repo, store = await setup()
        now = NOW + timedelta(hours=1)
        first = await repo.claim(TARGET, now=now)
        second = await repo.claim(TARGET, now=now)

        assert first is not None
        assert second is None

    asyncio.run(scenario())


def test_unfinished_send_does_not_cause_immediate_retry_after_restart():
    async def scenario():
        repo, store = await setup()
        await repo.claim(TARGET, now=NOW + timedelta(hours=1))
        restarted = StaffReminderRepository(None, 100, store=store)

        assert await restarted.claim(
            TARGET, now=NOW + timedelta(hours=1, minutes=1)
        ) is None
        assert await restarted.claim(
            TARGET, now=NOW + timedelta(hours=2)
        ) is not None

    asyncio.run(scenario())


def test_finished_target_stays_finished():
    async def scenario():
        repo, store = await setup()
        await repo.finish(TARGET, now=NOW + timedelta(minutes=30))
        await repo.ensure(
            TARGET,
            session_id="b" * 32,
            channel_id=500,
            now=NOW + timedelta(hours=2),
        )

        assert await repo.claim(
            TARGET, now=NOW + timedelta(hours=3)
        ) is None

    asyncio.run(scenario())


def test_old_attempt_cannot_mark_new_attempt_sent():
    async def scenario():
        repo, store = await setup()
        first = await repo.claim(TARGET, now=NOW + timedelta(hours=1))
        await repo.claim(TARGET, now=NOW + timedelta(hours=2))

        with pytest.raises(StaffReminderConflict):
            await repo.mark_sent(
                TARGET,
                attempt_id=first,
                now=NOW + timedelta(hours=2),
            )

    asyncio.run(scenario())


def test_successful_send_is_recorded():
    async def scenario():
        repo, store = await setup()
        now = NOW + timedelta(hours=1)
        attempt = await repo.claim(TARGET, now=now)
        await repo.mark_sent(TARGET, attempt_id=attempt, now=now)

        record = await repo.get(TARGET)
        assert record["sent_id"] == attempt

    asyncio.run(scenario())


def test_same_target_cannot_be_reused_for_another_channel():
    async def scenario():
        repo, store = await setup()

        with pytest.raises(StaffReminderConflict):
            await repo.ensure(
                TARGET, session_id="b" * 32, channel_id=999, now=NOW
            )

        assert (await repo.get(TARGET))["channel_id"] == 500

    asyncio.run(scenario())
