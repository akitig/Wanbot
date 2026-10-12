import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.kick_execution import KickExecution, KickTargetChanged


def setup():
    record = {
        "request_id": "a" * 32,
        "session_id": "b" * 32,
        "guild_id": 100,
        "user_id": 300,
        "channel_id": 500,
        "status": "pending",
        "reason": "テスト理由",
    }
    events = []

    async def decide(request_id, **kwargs):
        events.append("approved")
        record["status"] = "approved"
        return dict(record)

    async def transition(request_id, *, expected, target):
        assert record["status"] == expected
        events.append(target)
        record["status"] = target
        return dict(record)

    repository = SimpleNamespace(
        decide=AsyncMock(side_effect=decide),
        transition=AsyncMock(side_effect=transition),
    )
    service = KickExecution(
        client=object(),
        guild_id=100,
        repository=repository,
        admissions=object(),
        welcome_repository=object(),
        archive=object(),
    )

    async def kick(**kwargs):
        events.append("kick")

    member = SimpleNamespace(kick=AsyncMock(side_effect=kick))
    service.validate_target = AsyncMock(return_value=member)
    service.reconcile = AsyncMock(
        side_effect=lambda current: dict(current)
    )
    return service, record, repository, member, events


def test_approval_and_executing_saved_before_kick():
    async def scenario():
        service, record, _, member, events = setup()
        await service.approve(dict(record), 200)
        assert events == ["approved", "executing", "kick"]
        member.kick.assert_awaited_once()
        assert service.validate_target.await_count == 2

    asyncio.run(scenario())


def test_changed_target_is_not_kicked():
    async def scenario():
        service, record, repository, member, _ = setup()
        service.validate_target.side_effect = KickTargetChanged("rejoined")
        with pytest.raises(KickTargetChanged):
            await service.approve(dict(record), 200)
        member.kick.assert_not_awaited()
        repository.decide.assert_not_awaited()
        assert record["status"] == "obsolete"

    asyncio.run(scenario())


def test_change_after_approval_also_prevents_kick():
    async def scenario():
        service, record, _, member, _ = setup()
        service.validate_target.side_effect = [
            member, KickTargetChanged("rejoined")
        ]
        with pytest.raises(KickTargetChanged):
            await service.approve(dict(record), 200)
        member.kick.assert_not_awaited()
        assert record["status"] == "obsolete"

    asyncio.run(scenario())


def test_uncertain_result_stays_executing_and_is_not_retried():
    async def scenario():
        service, record, _, member, _ = setup()
        member.kick.side_effect = TimeoutError
        with pytest.raises(TimeoutError):
            await service.approve(dict(record), 200)
        assert record["status"] == "executing"

        with pytest.raises(ValueError):
            await service.approve(dict(record), 200)
        member.kick.assert_awaited_once()

    asyncio.run(scenario())
