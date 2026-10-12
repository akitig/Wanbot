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


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, transform):
        self.data = deepcopy(transform(deepcopy(self.data)))

    async def close(self):
        pass


def arguments():
    return {
        "context": SimpleNamespace(
            guild_id=100, session_id="a" * 32,
            user_id=300, channel_id=500,
        ),
        "joined_at": NOW,
        "actor_id": 900,
        "reason": "本人が25歳未満であることを再確認しました。",
        "now": NOW,
    }


def executor():
    return KickExecution(
        client=None,
        guild_id=100,
        repository=None,
        admissions=None,
        welcome_repository=None,
        archive=None,
    )


def test_age_marker_survives_reload():
    async def run():
        store = MemoryStore()
        repository = KickRequestRepository(None, 100, store=store)
        record = await repository.create(**arguments(), age_review=True)

        restored = KickRequestRepository(None, 100, store=store)
        saved = await restored.get(record["request_id"])
        assert saved["age_review"] is True
        assert saved["status"] == "pending"
        assert "waiting_timeout" not in saved

    asyncio.run(run())


def test_regular_request_keeps_existing_format():
    async def run():
        repository = KickRequestRepository(None, 100, store=MemoryStore())
        record = await repository.create(**arguments())
        assert "age_review" not in record

    asyncio.run(run())


def test_age_and_timeout_cannot_be_combined():
    async def run():
        store = MemoryStore()
        repository = KickRequestRepository(None, 100, store=store)
        with pytest.raises(KickRequestConflict):
            await repository.create(
                **arguments(),
                age_review=True,
                waiting_timeout={
                    "epoch": "b" * 32,
                    "progress_key": "test",
                },
            )
        assert store.data == {}

    asyncio.run(run())


@pytest.mark.parametrize("value", [1, "true", None])
def test_invalid_age_argument_is_rejected(value):
    async def run():
        repository = KickRequestRepository(None, 100, store=MemoryStore())
        with pytest.raises(KickRequestConflict):
            await repository.create(**arguments(), age_review=value)

    asyncio.run(run())


def test_missing_validator_blocks_age_request():
    async def run():
        with pytest.raises(InterviewNotReady):
            await executor()._check_age_review({"age_review": True})

    asyncio.run(run())


@pytest.mark.parametrize("result", [False, None, 1])
def test_only_true_is_accepted(result):
    async def run():
        service = executor()
        service.set_age_validator(AsyncMock(return_value=result))
        with pytest.raises(InterviewNotReady):
            await service._check_age_review({"age_review": True})

    asyncio.run(run())


def test_confirmed_age_passes_recheck():
    async def run():
        service = executor()
        validator = AsyncMock(return_value=True)
        service.set_age_validator(validator)
        record = {"age_review": True}
        await service._check_age_review(record)
        validator.assert_awaited_once_with(record)

    asyncio.run(run())


def test_changed_answer_is_propagated():
    async def run():
        service = executor()
        service.set_age_validator(
            AsyncMock(side_effect=KickTargetChanged("回答が変わっています。"))
        )
        with pytest.raises(KickTargetChanged):
            await service._check_age_review({"age_review": True})

    asyncio.run(run())


def test_other_kick_requests_do_not_use_age_validator():
    async def run():
        service = executor()
        validator = AsyncMock()
        service.set_age_validator(validator)
        await service._check_age_review({})
        validator.assert_not_awaited()

    asyncio.run(run())
