import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wanbot.welcome.kick_repository import (
    KickRequestConflict,
    KickRequestRepository,
    normalize_reason,
    same_membership,
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


NOW = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
JOINED = NOW - timedelta(days=1)


def setup():
    store = MemoryStore()
    repository = KickRequestRepository(None, 100, store=store)
    context = SimpleNamespace(
        guild_id=100, user_id=300,
        channel_id=500, session_id="a" * 32,
    )
    return repository, store, context


async def create(repository, context):
    return await repository.create(
        context=context,
        joined_at=JOINED,
        actor_id=200,
        reason="面談で参加条件が合わないことを確認しました。",
        now=NOW,
    )


def test_reason_and_target_survive_reopening():
    async def scenario():
        repository, store, context = setup()
        record = await create(repository, context)
        reopened = KickRequestRepository(None, 100, store=store)
        assert await reopened.get(record["request_id"]) == record
        assert record["status"] == "pending"
        assert record["decided_by"] is None

    asyncio.run(scenario())


def test_approval_records_admin_without_executing_kick():
    async def scenario():
        repository, _, context = setup()
        record = await create(repository, context)
        decided = await repository.decide(
            record["request_id"], actor_id=201, approve=True, now=NOW
        )
        assert decided["status"] == "approved"
        assert decided["decided_by"] == 201

    asyncio.run(scenario())


def test_cancelled_request_cannot_be_approved_later():
    async def scenario():
        repository, _, context = setup()
        record = await create(repository, context)
        await repository.decide(
            record["request_id"], actor_id=201, approve=False, now=NOW
        )
        with pytest.raises(KickRequestConflict):
            await repository.decide(
                record["request_id"], actor_id=201, approve=True, now=NOW
            )

    asyncio.run(scenario())


def test_duplicate_request_is_rejected():
    async def scenario():
        repository, _, context = setup()
        await create(repository, context)
        with pytest.raises(KickRequestConflict):
            await create(repository, context)

    asyncio.run(scenario())


def test_applicant_cannot_approve():
    async def scenario():
        repository, _, context = setup()
        record = await create(repository, context)
        with pytest.raises(KickRequestConflict):
            await repository.decide(
                record["request_id"], actor_id=300, approve=True, now=NOW
            )

    asyncio.run(scenario())


def test_rejoined_member_does_not_match_old_request():
    async def scenario():
        repository, _, context = setup()
        record = await create(repository, context)
        assert same_membership(
            record,
            guild_id=100,
            user_id=300,
            session_id=context.session_id,
            joined_at=JOINED,
        )
        assert not same_membership(
            record,
            guild_id=100,
            user_id=300,
            session_id=context.session_id,
            joined_at=NOW,
        )
        assert not same_membership(
            record,
            guild_id=100,
            user_id=300,
            session_id="b" * 32,
            joined_at=JOINED,
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ("", "   ", "\u200b", "あ" * 501))
def test_invalid_reason_is_rejected(reason):
    with pytest.raises(KickRequestConflict):
        normalize_reason(reason)
