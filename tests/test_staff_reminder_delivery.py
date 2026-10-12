import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.welcome.staff_reminder_delivery import StaffReminderDelivery
from wanbot.welcome.staff_reminder_target import (
    StaffReminderTarget,
    StaffReminderTargetError,
)


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def setup_delivery(*, kind="date_selection"):
    target = StaffReminderTarget(
        key="meeting:test",
        kind=kind,
        session_id="a" * 32,
        welcome_channel_id=500,
    )
    repository = SimpleNamespace(
        ensure=AsyncMock(return_value={
            "active": True,
            "next_at": NOW.isoformat(),
        }),
        claim=AsyncMock(return_value="b" * 32),
        mark_sent=AsyncMock(),
    )
    channel = Mock(spec=discord.TextChannel)
    channel.id = 600 if kind == "kick_review" else 500
    channel.guild = SimpleNamespace(id=100)
    channel.send = AsyncMock()
    load_target = AsyncMock(return_value=target)
    resolve_channel = AsyncMock(return_value=(channel, (10, 20)))
    available = Mock(return_value=True)

    service = StaffReminderDelivery(
        repository=repository,
        guild_id=100,
        kick_channel_id=600,
        lock=asyncio.Lock(),
        is_available=available,
        clock=lambda: NOW,
        load_target=load_target,
        resolve_channel=resolve_channel,
    )
    return SimpleNamespace(
        service=service,
        repository=repository,
        channel=channel,
        target=target,
        load_target=load_target,
        resolve_channel=resolve_channel,
        available=available,
    )


def test_sends_only_configured_role_mentions():
    async def run():
        item = setup_delivery()
        await item.service.refresh()

        options = item.channel.send.await_args.kwargs
        assert options["content"] == "<@&10> <@&20>"
        mentions = options["allowed_mentions"]
        assert mentions.everyone is False
        assert mentions.users is False
        assert [role.id for role in mentions.roles] == [10, 20]
        item.repository.mark_sent.assert_awaited_once()

    asyncio.run(run())


def test_before_due_time_does_not_fetch_or_send():
    async def run():
        item = setup_delivery()
        item.repository.ensure.return_value["next_at"] = (
            NOW + timedelta(seconds=1)
        ).isoformat()
        await item.service.refresh()
        item.resolve_channel.assert_not_awaited()
        item.repository.claim.assert_not_awaited()
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_completed_wait_does_not_send():
    async def run():
        item = setup_delivery()
        item.load_target.side_effect = [item.target, None]
        await item.service.refresh()
        item.repository.claim.assert_not_awaited()
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_claim_is_saved_before_sending():
    async def run():
        item = setup_delivery()

        async def send(**kwargs):
            item.repository.claim.assert_awaited_once()
            item.repository.mark_sent.assert_not_awaited()

        item.channel.send.side_effect = send
        await item.service.refresh()
        item.repository.mark_sent.assert_awaited_once()

    asyncio.run(run())


def test_send_failure_is_not_recorded_as_success():
    async def run():
        item = setup_delivery()
        item.channel.send.side_effect = RuntimeError("send failed")
        with pytest.raises(RuntimeError):
            await item.service.refresh()
        item.repository.claim.assert_awaited_once()
        item.repository.mark_sent.assert_not_awaited()

    asyncio.run(run())


def test_another_worker_claim_prevents_send():
    async def run():
        item = setup_delivery()
        item.repository.claim.return_value = None
        await item.service.refresh()
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_kick_reminder_uses_private_review_channel():
    async def run():
        item = setup_delivery(kind="kick_review")
        await item.service.refresh()
        assert (
            item.repository.ensure.await_args.kwargs["channel_id"] == 600
        )
        item.channel.send.assert_awaited_once()

    asyncio.run(run())


def test_kick_reminder_cannot_be_sent_to_welcome():
    async def run():
        item = setup_delivery(kind="kick_review")
        item.channel.id = 500
        with pytest.raises(StaffReminderTargetError):
            await item.service.refresh()
        item.repository.claim.assert_not_awaited()
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_other_guild_channel_is_rejected():
    async def run():
        item = setup_delivery()
        item.channel.guild.id = 999
        with pytest.raises(StaffReminderTargetError):
            await item.service.refresh()
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_unavailable_feature_does_nothing():
    async def run():
        item = setup_delivery()
        item.available.return_value = False
        await item.service.refresh()
        item.load_target.assert_not_awaited()
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_no_pending_target_does_nothing():
    async def run():
        item = setup_delivery()
        item.load_target.return_value = None
        await item.service.refresh()
        item.repository.ensure.assert_not_awaited()
        item.channel.send.assert_not_awaited()

    asyncio.run(run())
