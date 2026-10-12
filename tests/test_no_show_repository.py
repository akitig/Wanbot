import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wanbot.welcome.no_show_repository import NoShowConflict, NoShowRepository


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


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
    repository = NoShowRepository(None, 100, store=store)
    context = SimpleNamespace(
        guild_id=100, session_id="a" * 32, user_id=300, channel_id=500
    )
    plan = SimpleNamespace(
        status="confirmed",
        guild_id=100,
        welcome_session_id=context.session_id,
        user_id=300,
        channel_id=500,
        proposal_id="b" * 32,
        meeting_at=NOW,
    )
    return repository, store, context, plan


async def begin(repository, context, plan, now=NOW):
    return await repository.begin(
        context=context, plan=plan, actor_id=200, now=now
    )


def test_reopening_and_repeated_click_do_not_extend_deadline():
    async def scenario():
        repository, store, context, plan = setup()
        first = await begin(repository, context, plan)
        reopened = NoShowRepository(None, 100, store=store)
        repeated = await begin(
            reopened, context, plan, NOW + timedelta(minutes=20)
        )
        assert repeated == first
        assert datetime.fromisoformat(first["deadline"]) == NOW + timedelta(hours=1)

    asyncio.run(scenario())


def test_yes_before_deadline_requests_rescheduling():
    async def scenario():
        repository, _, context, plan = setup()
        await begin(repository, context, plan)
        record = await repository.respond(
            plan.proposal_id,
            actor_id=300,
            reschedule=True,
            now=NOW + timedelta(minutes=59),
        )
        assert record["status"] == "reschedule_requested"

    asyncio.run(scenario())


def test_yes_at_deadline_is_timeout():
    async def scenario():
        repository, _, context, plan = setup()
        await begin(repository, context, plan)
        record = await repository.respond(
            plan.proposal_id,
            actor_id=300,
            reschedule=True,
            now=NOW + timedelta(hours=1),
        )
        assert record["status"] == "kick_required"
        assert record["reason"] == "timeout"

    asyncio.run(scenario())


def test_no_requests_kick_approval():
    async def scenario():
        repository, _, context, plan = setup()
        await begin(repository, context, plan)
        record = await repository.respond(
            plan.proposal_id, actor_id=300, reschedule=False, now=NOW
        )
        assert record["status"] == "kick_required"
        assert record["reason"] == "declined"

    asyncio.run(scenario())


def test_other_member_cannot_answer():
    async def scenario():
        repository, _, context, plan = setup()
        await begin(repository, context, plan)
        with pytest.raises(NoShowConflict):
            await repository.respond(
                plan.proposal_id, actor_id=999, reschedule=True, now=NOW
            )
        assert (await repository.get(plan.proposal_id))["status"] == "waiting"

    asyncio.run(scenario())


def test_second_no_show_requires_admin_confirmation():
    async def scenario():
        repository, _, context, plan = setup()
        await begin(repository, context, plan)
        await repository.respond(
            plan.proposal_id, actor_id=300, reschedule=True, now=NOW
        )

        plan.proposal_id = "c" * 32
        second = await begin(repository, context, plan)
        assert second["status"] == "second_confirmation"
        assert second["deadline"] is None

        with pytest.raises(NoShowConflict):
            await repository.respond(
                plan.proposal_id, actor_id=300, reschedule=True, now=NOW
            )

        confirmed = await repository.confirm_second(
            plan.proposal_id, actor_id=200
        )
        assert confirmed["reason"] == "second_no_show"
        assert confirmed["status"] == "kick_required"

    asyncio.run(scenario())


def test_timeout_processing_is_idempotent():
    async def scenario():
        repository, _, context, plan = setup()
        await begin(repository, context, plan)
        before = await repository.expire(
            plan.proposal_id, now=NOW + timedelta(minutes=59)
        )
        assert before["status"] == "waiting"
        first = await repository.expire(
            plan.proposal_id, now=NOW + timedelta(hours=1)
        )
        second = await repository.expire(
            plan.proposal_id, now=NOW + timedelta(hours=2)
        )
        assert first == second
        assert first["reason"] == "timeout"

    asyncio.run(scenario())


def test_cannot_report_before_meeting_start():
    async def scenario():
        repository, _, context, plan = setup()
        with pytest.raises(NoShowConflict):
            await begin(
                repository, context, plan, NOW - timedelta(minutes=1)
            )

    asyncio.run(scenario())
