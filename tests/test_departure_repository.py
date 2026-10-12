import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from wanbot.leave_log.models import DepartureSnapshot, RoleSnapshot
from wanbot.leave_log.repository import (
    DepartureConflict,
    DepartureRepository,
)


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, transform):
        self.data = deepcopy(transform(deepcopy(self.data)))

    async def close(self):
        pass


def snapshot():
    return DepartureSnapshot(
        event_id="a" * 32,
        guild_id=100,
        user_id=300,
        username="test_user",
        display_name="テスト",
        roles=(RoleSnapshot(400, "構成員"),),
        joined_at=None,
        observed_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
    )


def test_pending_record_survives_restart():
    async def run():
        store = MemoryStore()
        repository = DepartureRepository(None, 100, store=store)
        saved = await repository.add(snapshot())

        restored = DepartureRepository(None, 100, store=store)
        assert await restored.pending() == (saved,)
        assert await restored.get(snapshot().event_id) == saved

    asyncio.run(run())


def test_duplicate_add_does_not_duplicate_record():
    async def run():
        repository = DepartureRepository(None, 100, store=MemoryStore())
        first = await repository.add(snapshot())
        second = await repository.add(snapshot())
        assert first == second
        assert len(await repository.pending()) == 1

    asyncio.run(run())


def test_same_id_cannot_overwrite_snapshot():
    async def run():
        repository = DepartureRepository(None, 100, store=MemoryStore())
        first = await repository.add(snapshot())
        with pytest.raises(DepartureConflict):
            await repository.add(replace(snapshot(), username="different"))
        assert await repository.get(snapshot().event_id) == first

    asyncio.run(run())


def test_sent_record_is_not_pending_and_retry_keeps_delivery():
    async def run():
        repository = DepartureRepository(None, 100, store=MemoryStore())
        item = snapshot()
        await repository.add(item)
        sent = await repository.mark_sent(
            item.event_id, channel_id=500, message_id=600
        )
        assert await repository.pending() == ()
        assert await repository.add(item) == sent
        assert await repository.mark_sent(
            item.event_id, channel_id=500, message_id=600
        ) == sent

    asyncio.run(run())


def test_other_delivery_cannot_replace_saved_message():
    async def run():
        repository = DepartureRepository(None, 100, store=MemoryStore())
        item = snapshot()
        await repository.add(item)
        await repository.mark_sent(
            item.event_id, channel_id=500, message_id=600
        )
        with pytest.raises(DepartureConflict):
            await repository.mark_sent(
                item.event_id, channel_id=500, message_id=601
            )

    asyncio.run(run())


def test_other_guild_cannot_be_added():
    async def run():
        store = MemoryStore()
        repository = DepartureRepository(None, 100, store=store)
        with pytest.raises(DepartureConflict):
            await repository.add(replace(snapshot(), guild_id=999))
        assert store.data == {}

    asyncio.run(run())


def test_other_guild_storage_is_rejected_on_read():
    async def run():
        store = MemoryStore()
        repository = DepartureRepository(None, 100, store=store)
        await repository.add(snapshot())
        other = DepartureRepository(None, 999, store=store)
        with pytest.raises(DepartureConflict):
            await other.pending()

    asyncio.run(run())


def test_returned_record_does_not_modify_storage():
    async def run():
        repository = DepartureRepository(None, 100, store=MemoryStore())
        saved = await repository.add(snapshot())
        saved["snapshot"]["roles"][0]["name"] = "変更"
        restored = await repository.get(snapshot().event_id)
        assert restored["snapshot"]["roles"][0]["name"] == "構成員"

    asyncio.run(run())


def test_unknown_event_cannot_be_marked_sent():
    async def run():
        repository = DepartureRepository(None, 100, store=MemoryStore())
        with pytest.raises(DepartureConflict):
            await repository.mark_sent(
                "b" * 32, channel_id=500, message_id=600
            )

    asyncio.run(run())
