"""キック前DMの結果保存と、再送防止。"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from wanbot.welcome.timeout_notice import TimeoutNotice, TimeoutNoticeError


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, change):
        updated = change(deepcopy(self.data))
        self.data = deepcopy(updated)


def setup():
    store = MemoryStore()
    service = TimeoutNotice(None, 100, store=store)
    request = {
        "request_id": "a" * 32,
        "guild_id": 100,
        "session_id": "b" * 32,
        "user_id": 300,
        "channel_id": 500,
        "status": "approved",
        "waiting_timeout": {
            "epoch": "c" * 32,
            "progress_key": "d" * 64,
        },
    }
    member = SimpleNamespace(
        id=300,
        guild=SimpleNamespace(id=100),
        send=AsyncMock(),
    )
    return service, store, request, member


def test_success_is_saved_and_not_resent_after_reload():
    async def scenario():
        service, store, request, member = setup()
        result = await service.notify(request, member)
        assert result["status"] == "sent"

        reloaded = TimeoutNotice(None, 100, store=store)
        restored = await reloaded.notify(request, member)

        assert restored["status"] == "sent"
        member.send.assert_awaited_once()
        assert "再度サーバーへ参加" in member.send.call_args.args[0]

    asyncio.run(scenario())


def test_dm_forbidden_is_saved_without_raising():
    async def scenario():
        service, store, request, member = setup()
        response = SimpleNamespace(status=403, reason="Forbidden")
        member.send.side_effect = discord.Forbidden(
            response, {"code": 50007, "message": "Cannot send messages"}
        )

        result = await service.notify(request, member)

        assert result["status"] == "failed"
        assert result["error_type"] == "Forbidden"
        await service.notify(request, member)
        member.send.assert_awaited_once()

    asyncio.run(scenario())


def test_network_error_is_saved_as_unknown():
    async def scenario():
        service, store, request, member = setup()
        member.send.side_effect = OSError("connection lost")

        result = await service.notify(request, member)

        assert result["status"] == "unknown"
        assert result["error_type"] == "OSError"
        assert "connection lost" not in str(store.data)

    asyncio.run(scenario())


def test_interruption_after_reservation_does_not_resend():
    async def scenario():
        service, store, request, member = setup()
        member.send.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await service.notify(request, member)

        assert (await service.get(request["request_id"]))["status"] == "attempted"

        member.send.side_effect = None
        reloaded = TimeoutNotice(None, 100, store=store)
        result = await reloaded.notify(request, member)

        assert result["status"] == "attempted"
        member.send.assert_awaited_once()

    asyncio.run(scenario())


def test_result_save_failure_does_not_resend():
    async def scenario():
        service, store, request, member = setup()
        original_update = store.update
        calls = 0

        async def update(change):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("save failed")
            await original_update(change)

        store.update = update

        with pytest.raises(OSError):
            await service.notify(request, member)

        store.update = original_update
        result = await service.notify(request, member)

        assert result["status"] == "attempted"
        member.send.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["pending", "wrong_user", "wrong_guild"])
def test_unapproved_or_wrong_target_is_rejected(change):
    async def scenario():
        service, store, request, member = setup()
        if change == "pending":
            request["status"] = "pending"
        elif change == "wrong_user":
            member.id = 999
        else:
            member.guild.id = 999

        with pytest.raises(TimeoutNoticeError):
            await service.notify(request, member)

        member.send.assert_not_awaited()
        assert store.data == {}

    asyncio.run(scenario())
