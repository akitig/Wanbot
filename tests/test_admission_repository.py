import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from wanbot.welcome.admission_repository import (
    AdmissionConflict,
    AdmissionRepository,
)


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, change):
        self.data = deepcopy(change(deepcopy(self.data)))

    async def close(self):
        pass


def setup():
    store = MemoryStore()
    repository = AdmissionRepository(None, 100, store=store)
    context = SimpleNamespace(
        guild_id=100, user_id=300, channel_id=500, session_id="a" * 32
    )
    plan = SimpleNamespace(
        status="confirmed",
        guild_id=100,
        user_id=300,
        channel_id=500,
        welcome_session_id=context.session_id,
        proposal_id="b" * 32,
    )
    return repository, store, context, plan


async def approve(repository, context, plan, actor_id=200, roles=(10, 20)):
    return await repository.begin(
        context=context,
        plan=plan,
        actor_id=actor_id,
        role_ids=roles,
    )


def test_repeated_approval_preserves_original_approver():
    async def scenario():
        repository, _, context, plan = setup()
        first = await approve(repository, context, plan)
        second = await approve(repository, context, plan, actor_id=201)
        assert second == first
        assert second["approved_by"] == 200

    asyncio.run(scenario())


def test_progress_can_be_loaded_by_new_repository():
    async def scenario():
        repository, store, context, plan = setup()
        await approve(repository, context, plan)
        await repository.mark_roles_verified(context.session_id, plan.proposal_id)

        reopened = AdmissionRepository(None, 100, store=store)
        saved = await reopened.get(context.session_id)
        assert saved["phase"] == "roles_verified"

        completed = await reopened.mark_completed(
            context.session_id, plan.proposal_id
        )
        assert completed["phase"] == "completed"

        # 古い処理が再実行されても状態を巻き戻さない。
        repeated = await reopened.mark_roles_verified(
            context.session_id, plan.proposal_id
        )
        assert repeated["phase"] == "completed"

    asyncio.run(scenario())


def test_cannot_complete_before_role_verification():
    async def scenario():
        repository, _, context, plan = setup()
        await approve(repository, context, plan)
        with pytest.raises(AdmissionConflict):
            await repository.mark_completed(context.session_id, plan.proposal_id)
        assert (await repository.get(context.session_id))["phase"] == "approved"

    asyncio.run(scenario())


def test_cannot_change_roles_after_approval():
    async def scenario():
        repository, _, context, plan = setup()
        await approve(repository, context, plan)
        with pytest.raises(AdmissionConflict):
            await approve(repository, context, plan, roles=(10, 30))
        assert (await repository.get(context.session_id))["role_ids"] == [10, 20]

    asyncio.run(scenario())


def test_applicant_cannot_be_recorded_as_approver():
    async def scenario():
        repository, _, context, plan = setup()
        with pytest.raises(AdmissionConflict):
            await approve(repository, context, plan, actor_id=300)
        assert await repository.get(context.session_id) is None

    asyncio.run(scenario())


def test_unconfirmed_meeting_is_rejected():
    async def scenario():
        repository, _, context, plan = setup()
        plan.status = "waiting_second"
        with pytest.raises(AdmissionConflict):
            await approve(repository, context, plan)

    asyncio.run(scenario())


def test_different_proposal_cannot_advance():
    async def scenario():
        repository, _, context, plan = setup()
        await approve(repository, context, plan)
        with pytest.raises(AdmissionConflict):
            await repository.mark_roles_verified(
                context.session_id, "c" * 32
            )

    asyncio.run(scenario())
