"""面談予定の再調整履歴と重複処理の防止。"""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wanbot.storage.json_store import StorageError
from wanbot.welcome import meeting_repository as module
from wanbot.welcome.meeting_plan import MeetingPlan, MeetingPlanConflict
from wanbot.welcome.meeting_repository import MeetingPlanRepository


SID = "a" * 32
PROPOSAL = "b" * 32
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


class MemoryStore:
    def __init__(self, data=None):
        self.data = deepcopy(data) if data is not None else {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, change):
        updated = change(deepcopy(self.data))
        self.data = deepcopy(updated)


def plan():
    return MeetingPlan(
        proposal_id=PROPOSAL,
        welcome_session_id=SID,
        guild_id=100,
        user_id=300,
        channel_id=500,
        preference=1,
        meeting_at=NOW + timedelta(days=1),
        first_manager_id=400,
        second_manager_id=None,
        status="confirmed",
        proposed_at=NOW,
        decided_at=NOW,
    )


def repository(current=None, *, store=None):
    repo = MeetingPlanRepository.__new__(MeetingPlanRepository)
    repo._guild_id = 100
    repo._config = None
    if store is None:
        plans = {}
        if current is not None:
            plans[SID] = module._encode(current)
        store = MemoryStore({"version": 1, "plans": plans})
    repo._store = store
    return repo


def test_confirmed_plan_is_preserved_in_history():
    async def scenario():
        original = plan()
        repo = repository(original)

        await repo.archive_for_reschedule(original)

        assert await repo.get(SID) is None
        assert (
            await repo.archived_for_reschedule(PROPOSAL)
            == original
        )

    asyncio.run(scenario())


def test_new_proposal_preserves_history_and_survives_repeated_reset(
    monkeypatch,
):
    async def scenario():
        original = plan()
        repo = repository(original)
        await repo.archive_for_reschedule(original)

        replacement = replace(
            original,
            proposal_id="c" * 32,
            meeting_at=NOW + timedelta(days=2),
        )

        # このテストでは保存処理を検証する。
        # 日程・管理者の判定は既存のドメインテストが担当する。
        def propose(**kwargs):
            assert kwargs["previous"] is None
            return replacement

        monkeypatch.setattr(module, "propose_meeting", propose)

        saved = await repo.propose(
            request=SimpleNamespace(session_id=SID),
            actor_id=400,
            actor_role_ids=(),
            preference=1,
            second_manager_id=None,
            second_manager_role_ids=(),
            now=NOW,
        )
        assert saved == replacement
        assert (
            await repo.archived_for_reschedule(PROPOSAL)
            == original
        )

        reloaded = repository(store=repo._store)
        await reloaded.archive_for_reschedule(original)

        assert await reloaded.get(SID) == replacement
        assert (
            await reloaded.archived_for_reschedule(PROPOSAL)
            == original
        )

    asyncio.run(scenario())


def test_old_plan_cannot_remove_updated_plan():
    async def scenario():
        original = plan()
        updated = replace(original, first_manager_id=401)
        repo = repository(updated)
        before = await repo._store.read()

        with pytest.raises(MeetingPlanConflict):
            await repo.archive_for_reschedule(original)

        assert await repo._store.read() == before
        assert await repo.get(SID) == updated

    asyncio.run(scenario())


def test_same_proposal_cannot_archive_different_contents():
    async def scenario():
        original = plan()
        repo = repository(original)
        await repo.archive_for_reschedule(original)
        before = await repo._store.read()

        with pytest.raises(MeetingPlanConflict):
            await repo.archive_for_reschedule(
                replace(original, first_manager_id=401)
            )

        assert await repo._store.read() == before

    asyncio.run(scenario())


def test_waiting_plan_cannot_be_archived_for_no_show():
    async def scenario():
        waiting = replace(
            plan(),
            status="waiting_second",
            second_manager_id=401,
            decided_at=None,
        )
        repo = repository(waiting)
        before = await repo._store.read()

        with pytest.raises(MeetingPlanConflict):
            await repo.archive_for_reschedule(waiting)

        assert await repo._store.read() == before

    asyncio.run(scenario())


def test_invalid_history_prevents_changes():
    async def scenario():
        original = plan()
        repo = repository(original)
        repo._store.data["reschedule_history"] = {
            PROPOSAL: {"unexpected": True}
        }
        before = await repo._store.read()

        with pytest.raises(StorageError):
            await repo.archive_for_reschedule(original)

        assert await repo._store.read() == before

    asyncio.run(scenario())
