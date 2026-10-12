"""管理者承認後のDM・再確認・キックの順序。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wanbot.welcome.kick_execution import KickExecution, KickTargetChanged


def setup():
    order = []
    request = {
        "request_id": "a" * 32,
        "status": "pending",
        "reason": "本人期限切れ",
        "waiting_timeout": {
            "epoch": "b" * 32,
            "progress_key": "c" * 64,
        },
    }

    async def kick(**kwargs):
        order.append("kick")

    member = SimpleNamespace(kick=AsyncMock(side_effect=kick))

    async def validate(record):
        order.append("validate")
        return member

    async def decide(request_id, *, actor_id, approve):
        order.append("approve")
        return {**request, "status": "approved"}

    async def transition(request_id, *, expected, target):
        order.append(target)
        return {**request, "status": target}

    async def notify(record, target):
        assert record["status"] == "approved"
        assert target is member
        order.append("dm")
        return {"status": "sent"}

    async def reconcile(record):
        order.append("reconcile")
        return {**record, "status": "completed"}

    executor = KickExecution.__new__(KickExecution)
    executor._guild_id = 100
    executor._repository = SimpleNamespace(
        decide=AsyncMock(side_effect=decide),
        transition=AsyncMock(side_effect=transition),
    )
    executor._timeout_notice = SimpleNamespace(
        notify=AsyncMock(side_effect=notify)
    )
    executor.validate_target = AsyncMock(side_effect=validate)
    executor.reconcile = AsyncMock(side_effect=reconcile)
    return executor, request, member, order


def test_dm_is_between_approval_and_final_validation():
    async def scenario():
        executor, request, member, order = setup()

        result = await executor.approve(request, 400)

        assert result["status"] == "completed"
        assert order == [
            "validate", "approve", "validate",
            "dm", "validate", "executing", "kick", "reconcile",
        ]
        member.kick.assert_awaited_once()

    asyncio.run(scenario())


def test_progress_during_dm_prevents_kick():
    async def scenario():
        executor, request, member, order = setup()
        executor.validate_target.side_effect = [
            member,
            member,
            KickTargetChanged("本人が回答しました。"),
        ]

        with pytest.raises(KickTargetChanged):
            await executor.approve(request, 400)

        member.kick.assert_not_awaited()
        executor._repository.transition.assert_awaited_once_with(
            request["request_id"],
            expected="approved",
            target="obsolete",
        )
        executor._timeout_notice.notify.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["failed", "unknown", "attempted"])
def test_recorded_dm_failure_does_not_prevent_approved_kick(status):
    async def scenario():
        executor, request, member, order = setup()
        executor._timeout_notice.notify.side_effect = None
        executor._timeout_notice.notify.return_value = {"status": status}

        await executor.approve(request, 400)

        member.kick.assert_awaited_once()

    asyncio.run(scenario())


def test_dm_record_save_failure_prevents_kick():
    async def scenario():
        executor, request, member, order = setup()
        executor._timeout_notice.notify.side_effect = OSError("save failed")

        with pytest.raises(OSError):
            await executor.approve(request, 400)

        member.kick.assert_not_awaited()
        executor._repository.transition.assert_not_awaited()

    asyncio.run(scenario())


def test_missing_notice_service_prevents_timeout_kick():
    async def scenario():
        executor, request, member, order = setup()
        executor._timeout_notice = None

        with pytest.raises(ValueError):
            await executor.approve(request, 400)

        member.kick.assert_not_awaited()
        executor._repository.transition.assert_not_awaited()

    asyncio.run(scenario())
