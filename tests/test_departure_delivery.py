import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.leave_log.audit_match import AuditMatch
from wanbot.leave_log.delivery import DepartureDelivery, DepartureDeliveryError
from wanbot.leave_log.models import DepartureSnapshot, encode_snapshot


def setup_delivery():
    snapshot = DepartureSnapshot(
        event_id="a" * 32, guild_id=100, user_id=300,
        username="test_user", display_name=None, roles=None,
        joined_at=None,
        observed_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
    )
    record = {
        "snapshot": encode_snapshot(snapshot),
        "channel_id": None,
        "message_id": None,
    }
    repository = SimpleNamespace(
        get=AsyncMock(return_value=record),
        mark_sent=AsyncMock(),
    )
    results = SimpleNamespace(
        get=AsyncMock(return_value=(
            snapshot, AuditMatch("unknown", None, "no_matching_audit")
        )),
    )
    messages = []
    permissions = SimpleNamespace(
        view_channel=True, send_messages=True,
        embed_links=True, read_message_history=True,
    )
    channel = Mock(spec=discord.TextChannel)
    channel.id = 500
    channel.guild = SimpleNamespace(
        id=100, unavailable=False, fetch_member=AsyncMock(),
    )
    channel.permissions_for.return_value = permissions

    async def history(**kwargs):
        for message in messages:
            yield message

    async def send(**kwargs):
        message = SimpleNamespace(
            id=600, author=SimpleNamespace(id=900),
            webhook_id=None, embeds=[kwargs["embed"]],
        )
        messages.append(message)
        return message

    channel.history = history
    channel.send = AsyncMock(side_effect=send)
    client = SimpleNamespace(
        user=SimpleNamespace(id=900),
        fetch_channel=AsyncMock(return_value=channel),
    )
    config = SimpleNamespace(
        guild_for=lambda guild_id: SimpleNamespace(
            feature_enabled=lambda name: name == "leave_log",
            ids={"LEAVE_LOG_CHANNEL_ID": 500},
        ),
    )
    available = Mock(return_value=True)

    def service():
        return DepartureDelivery(
            client=client, config=config, guild_id=100,
            repository=repository, results=results,
            is_available=available,
        )

    return SimpleNamespace(
        service=service, snapshot=snapshot, record=record,
        repository=repository, results=results, channel=channel,
        client=client, messages=messages, permissions=permissions,
        available=available,
    )


def test_sends_and_records_message_without_mentions():
    async def run():
        item = setup_delivery()
        result = await item.service().send(item.snapshot.event_id)
        assert result == 600
        item.repository.mark_sent.assert_awaited_once_with(
            item.snapshot.event_id, channel_id=500, message_id=600
        )
        mentions = item.channel.send.await_args.kwargs["allowed_mentions"]
        assert mentions.everyone is False
        assert mentions.users is False
        assert mentions.roles is False

    asyncio.run(run())


def test_restart_recovers_post_after_save_failure():
    async def run():
        item = setup_delivery()
        item.repository.mark_sent.side_effect = RuntimeError("save failed")
        with pytest.raises(RuntimeError):
            await item.service().send(item.snapshot.event_id)

        item.repository.mark_sent.side_effect = None
        assert await item.service().send(item.snapshot.event_id) == 600
        assert item.channel.send.await_count == 1

    asyncio.run(run())


def test_sent_record_does_not_repost():
    async def run():
        item = setup_delivery()
        item.record.update(channel_id=500, message_id=600)
        assert await item.service().send(item.snapshot.event_id) == 600
        item.client.fetch_channel.assert_not_awaited()

    asyncio.run(run())


def test_unfinished_classification_does_not_send():
    async def run():
        item = setup_delivery()
        item.results.get.return_value = None
        assert await item.service().send(item.snapshot.event_id) is None
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_other_guild_channel_is_rejected():
    async def run():
        item = setup_delivery()
        item.channel.guild.id = 999
        with pytest.raises(DepartureDeliveryError):
            await item.service().send(item.snapshot.event_id)
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_missing_history_permission_is_rejected():
    async def run():
        item = setup_delivery()
        item.permissions.read_message_history = False
        with pytest.raises(DepartureDeliveryError):
            await item.service().send(item.snapshot.event_id)
        item.channel.send.assert_not_awaited()

    asyncio.run(run())


def test_disabled_service_does_nothing():
    async def run():
        item = setup_delivery()
        item.available.return_value = False
        assert await item.service().send(item.snapshot.event_id) is None
        item.repository.get.assert_not_awaited()

    asyncio.run(run())


def test_other_users_marker_does_not_suppress_post():
    async def run():
        item = setup_delivery()
        embed = discord.Embed()
        embed.set_footer(text=f"wanbot:leave-log:{item.snapshot.event_id}")
        item.messages.append(SimpleNamespace(
            id=700, author=SimpleNamespace(id=999),
            webhook_id=None, embeds=[embed],
        ))
        await item.service().send(item.snapshot.event_id)
        item.channel.send.assert_awaited_once()

    asyncio.run(run())
