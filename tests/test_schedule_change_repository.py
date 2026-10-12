import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wanbot.welcome.schedule_change_repository import (
    ScheduleChangeConflict,
    ScheduleChangeRepository,
)


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
SID = "a" * 32


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, transform):
        self.data = deepcopy(transform(deepcopy(self.data)))

    async def close(self):
        pass


def values():
    return {
        "context": SimpleNamespace(
            guild_id=100, session_id=SID, user_id=300, channel_id=500,
        ),
        "request": SimpleNamespace(
            session_id=SID, user_id=300, channel_id=500,
            confirmed_at=NOW,
        ),
        "proposal_id": None,
        "actor_id": 300,
        "source": "member",
        "now": NOW + timedelta(minutes=1),
    }


def test_duplicate_request_and_restart_keep_same_id():
    async def run():
        store = MemoryStore()
        repository = ScheduleChangeRepository(None, 100, store=store)
        first = await repository.begin(**values())
        second = await repository.begin(**values())

        restored = ScheduleChangeRepository(None, 100, store=store)
        third = await restored.begin(**values())
        assert first == second == third
        assert len(await restored.for_session(SID)) == 1

    asyncio.run(run())


def test_manager_can_request_resubmission():
    async def run():
        repository = ScheduleChangeRepository(None, 100, store=MemoryStore())
        args = values()
        args.update(actor_id=400, source="manager")
        record = await repository.begin(**args)
        assert record["source"] == "manager"
        assert record["requested_by"] == 400

    asyncio.run(run())


@pytest.mark.parametrize(
    ("source", "actor_id"),
    [("member", 400), ("manager", 300)],
)
def test_actor_kind_must_match(source, actor_id):
    async def run():
        repository = ScheduleChangeRepository(None, 100, store=MemoryStore())
        args = values()
        args.update(source=source, actor_id=actor_id)
        with pytest.raises(ScheduleChangeConflict):
            await repository.begin(**args)

    asyncio.run(run())


def test_other_guild_is_rejected():
    async def run():
        repository = ScheduleChangeRepository(None, 100, store=MemoryStore())
        args = values()
        args["context"].guild_id = 999
        with pytest.raises(ScheduleChangeConflict):
            await repository.begin(**args)

    asyncio.run(run())


def test_other_member_request_is_rejected():
    async def run():
        repository = ScheduleChangeRepository(None, 100, store=MemoryStore())
        args = values()
        args["request"].user_id = 999
        with pytest.raises(ScheduleChangeConflict):
            await repository.begin(**args)

    asyncio.run(run())


def test_new_submission_cannot_replace_pending_change():
    async def run():
        repository = ScheduleChangeRepository(None, 100, store=MemoryStore())
        await repository.begin(**values())
        args = values()
        args["request"].confirmed_at += timedelta(seconds=1)
        with pytest.raises(ScheduleChangeConflict):
            await repository.begin(**args)
        assert len(await repository.for_session(SID)) == 1

    asyncio.run(run())


def test_completed_change_allows_later_submission_change():
    async def run():
        repository = ScheduleChangeRepository(None, 100, store=MemoryStore())
        first = await repository.begin(**values())
        await repository.complete(first["change_id"])

        args = values()
        args["request"].confirmed_at += timedelta(seconds=1)
        second = await repository.begin(**args)
        assert second["change_id"] != first["change_id"]
        assert second["status"] == "pending"

        # 古い操作の再試行では、完了済みの記録を返す。
        old = await repository.begin(**values())
        assert old["change_id"] == first["change_id"]
        assert old["status"] == "completed"
        assert len(await repository.for_session(SID)) == 2

    asyncio.run(run())


def test_complete_is_idempotent():
    async def run():
        repository = ScheduleChangeRepository(None, 100, store=MemoryStore())
        record = await repository.begin(**values())
        first = await repository.complete(record["change_id"])
        second = await repository.complete(record["change_id"])
        assert first == second
        assert first["status"] == "completed"

    asyncio.run(run())
