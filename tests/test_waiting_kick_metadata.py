"""本人期限の識別情報と、実行前チェック。"""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.kick_execution import KickExecution, KickTargetChanged
from wanbot.welcome.kick_repository import (
    KickRequestConflict,
    KickRequestRepository,
)


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
TIMEOUT = {"epoch": "b" * 32, "progress_key": "c" * 64}


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, change):
        updated = change(deepcopy(self.data))
        self.data = deepcopy(updated)


async def create(repo, **options):
    return await repo.create(
        context=SimpleNamespace(
            guild_id=100,
            session_id="a" * 32,
            user_id=300,
            channel_id=500,
        ),
        joined_at=NOW,
        actor_id=400,
        reason="本人待ちが72時間に達しました。",
        now=NOW,
        **options,
    )


def test_timeout_metadata_survives_reload_and_approval():
    async def scenario():
        store = MemoryStore()
        repo = KickRequestRepository(None, 100, store=store)
        record = await create(repo, waiting_timeout=TIMEOUT)

        reloaded = KickRequestRepository(None, 100, store=store)
        restored = await reloaded.get(record["request_id"])
        assert restored["waiting_timeout"] == TIMEOUT

        approved = await reloaded.decide(
            record["request_id"],
            actor_id=401,
            approve=True,
            now=NOW,
        )
        assert approved["waiting_timeout"] == TIMEOUT

    asyncio.run(scenario())


def test_existing_request_without_metadata_still_works():
    async def scenario():
        repo = KickRequestRepository(None, 100, store=MemoryStore())
        record = await create(repo)
        restored = await repo.get(record["request_id"])
        assert "waiting_timeout" not in restored

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"epoch": "invalid", "progress_key": "abc"},
        {"epoch": "b" * 32, "progress_key": ""},
        {"epoch": "b" * 32, "progress_key": "回答本文です"},
    ],
)
def test_invalid_metadata_is_not_saved(metadata):
    async def scenario():
        store = MemoryStore()
        repo = KickRequestRepository(None, 100, store=store)

        with pytest.raises(KickRequestConflict):
            await create(repo, waiting_timeout=metadata)

        assert store.data == {}

    asyncio.run(scenario())


def test_timeout_request_requires_configured_validator():
    async def scenario():
        executor = KickExecution.__new__(KickExecution)
        executor._timeout_validator = None

        with pytest.raises(InterviewNotReady):
            await executor._check_waiting_timeout(
                {"waiting_timeout": TIMEOUT}
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("result", [False, None])
def test_validator_must_explicitly_allow_execution(result):
    async def scenario():
        executor = KickExecution.__new__(KickExecution)
        executor.set_timeout_validator(AsyncMock(return_value=result))

        with pytest.raises(InterviewNotReady):
            await executor._check_waiting_timeout(
                {"waiting_timeout": TIMEOUT}
            )

    asyncio.run(scenario())


def test_validator_receives_request_and_allows_valid_timeout():
    async def scenario():
        executor = KickExecution.__new__(KickExecution)
        validator = AsyncMock(return_value=True)
        executor.set_timeout_validator(validator)
        record = {"waiting_timeout": TIMEOUT}

        await executor._check_waiting_timeout(record)

        validator.assert_awaited_once_with(record)

    asyncio.run(scenario())


def test_changed_target_is_propagated_for_obsolete_handling():
    async def scenario():
        executor = KickExecution.__new__(KickExecution)
        executor.set_timeout_validator(
            AsyncMock(side_effect=KickTargetChanged("手続きが進みました。"))
        )

        with pytest.raises(KickTargetChanged):
            await executor._check_waiting_timeout(
                {"waiting_timeout": TIMEOUT}
            )

    asyncio.run(scenario())


def test_non_timeout_request_does_not_call_extra_validator():
    async def scenario():
        executor = KickExecution.__new__(KickExecution)
        validator = AsyncMock(return_value=False)
        executor.set_timeout_validator(validator)

        await executor._check_waiting_timeout({"reason": "面談不承認"})

        validator.assert_not_awaited()

    asyncio.run(scenario())
