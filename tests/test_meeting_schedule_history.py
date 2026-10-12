import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from wanbot.storage.json_store import StorageError
from wanbot.welcome.meeting_plan import MeetingPlan, MeetingPlanConflict
from wanbot.welcome.meeting_repository import MeetingPlanRepository, _encode


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
SID = "a" * 32
CHANGE = "c" * 32


class MemoryStore:
    def __init__(self, data):
        self.data = deepcopy(data)

    async def read(self):
        return deepcopy(self.data)

    async def update(self, transform):
        self.data = deepcopy(transform(deepcopy(self.data)))

    async def close(self):
        pass


def make_plan(status="confirmed"):
    return MeetingPlan(
        proposal_id="b" * 32,
        welcome_session_id=SID,
        guild_id=100,
        user_id=300,
        channel_id=500,
        preference=1,
        meeting_at=NOW + timedelta(days=1),
        first_manager_id=400,
        second_manager_id=401,
        status=status,
        proposed_at=NOW,
        decided_at=None if status == "waiting_second" else NOW,
    )


def make_repository(store):
    with patch(
        "wanbot.welcome.meeting_repository.AsyncGuildJsonStore",
        return_value=store,
    ):
        return MeetingPlanRepository(None, 100)


def setup_repository(plan):
    store = MemoryStore({
        "version": 1,
        "plans": {SID: _encode(plan)},
    })
    return make_repository(store), store


@pytest.mark.parametrize(
    "status", ["waiting_second", "declined", "confirmed"]
)
def test_each_state_can_be_archived_and_restored(status):
    async def run():
        plan = make_plan(status)
        repository, store = setup_repository(plan)

        await repository.archive_for_schedule_change(
            plan, change_id=CHANGE
        )
        assert await repository.get(SID) is None

        restored = make_repository(store)
        assert (
            await restored.archived_for_schedule_change(CHANGE)
        ) == plan

    asyncio.run(run())


def test_retry_does_not_remove_new_plan():
    async def run():
        old = make_plan()
        repository, store = setup_repository(old)
        await repository.archive_for_schedule_change(
            old, change_id=CHANGE
        )

        new = replace(old, proposal_id="d" * 32)
        store.data["plans"][SID] = _encode(new)

        await repository.archive_for_schedule_change(
            old, change_id=CHANGE
        )
        assert await repository.get(SID) == new

    asyncio.run(run())


def test_changed_current_plan_is_rejected_without_deleting_it():
    async def run():
        old = make_plan("waiting_second")
        current = replace(old, status="confirmed", decided_at=NOW)
        repository, store = setup_repository(current)
        before = deepcopy(store.data)

        with pytest.raises(MeetingPlanConflict):
            await repository.archive_for_schedule_change(
                old, change_id=CHANGE
            )

        assert store.data == before
        assert await repository.get(SID) == current

    asyncio.run(run())


def test_change_id_cannot_be_reused_for_different_plan():
    async def run():
        old = make_plan()
        repository, store = setup_repository(old)
        await repository.archive_for_schedule_change(
            old, change_id=CHANGE
        )

        new = replace(old, proposal_id="d" * 32)
        store.data["plans"][SID] = _encode(new)
        with pytest.raises(MeetingPlanConflict):
            await repository.archive_for_schedule_change(
                new, change_id=CHANGE
            )
        assert await repository.get(SID) == new

    asyncio.run(run())


def test_no_show_archive_preserves_normal_change_history():
    async def run():
        old = make_plan()
        repository, store = setup_repository(old)
        await repository.archive_for_schedule_change(
            old, change_id=CHANGE
        )

        new = replace(old, proposal_id="d" * 32)
        store.data["plans"][SID] = _encode(new)
        await repository.archive_for_reschedule(new)

        assert (
            await repository.archived_for_schedule_change(CHANGE)
        ) == old
        assert (
            await repository.archived_for_reschedule(new.proposal_id)
        ) == new

    asyncio.run(run())


def test_normal_change_preserves_no_show_history():
    async def run():
        old = make_plan()
        repository, store = setup_repository(old)
        await repository.archive_for_reschedule(old)

        new = replace(old, proposal_id="d" * 32)
        store.data["plans"][SID] = _encode(new)
        await repository.archive_for_schedule_change(
            new, change_id=CHANGE
        )

        assert (
            await repository.archived_for_reschedule(old.proposal_id)
        ) == old
        assert (
            await repository.archived_for_schedule_change(CHANGE)
        ) == new

    asyncio.run(run())


def test_other_guild_history_is_rejected():
    async def run():
        plan = make_plan()
        repository, store = setup_repository(plan)
        store.data["schedule_history"] = {
            CHANGE: _encode(replace(plan, guild_id=999)),
        }
        with pytest.raises(StorageError):
            await repository.get(SID)

    asyncio.run(run())
