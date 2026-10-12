import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.bot import WanbotClient
from wanbot.config import parse_config
from wanbot.connection import ConnectionCheckClient
from wanbot.dm_forward import DMForwarder, MAX_FILE_BYTES, split_text


def config(enabled=True):
    return SimpleNamespace(
        dm_forward_enabled=enabled,
        dm_forward_user_id=400,
    )


def message(**overrides):
    values = {
        "id": 500,
        "guild": None,
        "channel": SimpleNamespace(type=discord.ChannelType.private),
        "author": SimpleNamespace(
            id=300,
            bot=False,
            name="wawolf",
            display_name="おおかみ",
        ),
        "webhook_id": None,
        "created_at": datetime(2026, 10, 12, tzinfo=timezone.utc),
        "content": "こんにちは @everyone",
        "attachments": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def case(*, enabled=True, available=True):
    destination = SimpleNamespace(
        id=400, bot=False, send=AsyncMock()
    )
    client = SimpleNamespace(
        user=SimpleNamespace(id=200),
        fetch_user=AsyncMock(return_value=destination),
    )
    forwarder = DMForwarder(
        client,
        config(enabled),
        is_available=lambda: available,
    )
    return forwarder, client, destination


def test_forwards_identity_body_without_mentions():
    async def scenario():
        forwarder, client, destination = case()
        assert await forwarder.handle(message())
        client.fetch_user.assert_awaited_once_with(400)
        calls = destination.send.await_args_list
        assert "おおかみ（wawolf）" in calls[0].args[0]
        assert calls[1].args[0] == "こんにちは @everyone"
        for call in calls:
            mentions = call.kwargs["allowed_mentions"]
            assert mentions.everyone is False
            assert mentions.users is False
            assert mentions.roles is False
            assert call.kwargs["suppress_embeds"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["guild", "bot", "webhook", "group"])
def test_ignores_non_personal_human_dm(kind):
    async def scenario():
        forwarder, client, destination = case()
        value = message()
        if kind == "guild":
            value.guild = SimpleNamespace(id=100)
        elif kind == "bot":
            value.author.bot = True
        elif kind == "webhook":
            value.webhook_id = 600
        else:
            value.channel.type = discord.ChannelType.group
        assert not await forwarder.handle(value)
        client.fetch_user.assert_not_awaited()
        destination.send.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("enabled", "available"),
    [(False, True), (True, False)],
)
def test_disabled_or_unavailable_does_not_send(enabled, available):
    async def scenario():
        forwarder, client, _ = case(
            enabled=enabled, available=available,
        )
        assert not await forwarder.handle(message())
        client.fetch_user.assert_not_awaited()

    asyncio.run(scenario())


def test_recent_completed_message_is_not_sent_twice():
    async def scenario():
        forwarder, _, destination = case()
        assert await forwarder.handle(message())
        assert not await forwarder.handle(message())
        assert destination.send.await_count == 2

    asyncio.run(scenario())


def test_split_preserves_long_unicode_text():
    text = "あ😀\n" * 3000
    parts = split_text(text)
    assert "".join(parts) == text
    assert all(
        len(part.encode("utf-16-le")) // 2 <= 1900
        for part in parts
    )


def test_attachment_is_uploaded_and_closed():
    async def scenario():
        forwarder, _, destination = case()
        file = Mock(spec=discord.File)
        attachment = SimpleNamespace(
            size=7,
            filename="example.txt",
            url="https://example.invalid/example.txt",
            to_file=AsyncMock(return_value=file),
            is_spoiler=lambda: True,
        )
        assert await forwarder.handle(message(attachments=[attachment]))
        attachment.to_file.assert_awaited_once_with(
            use_cached=True, spoiler=True,
        )
        assert destination.send.await_args.kwargs["file"] is file
        file.close.assert_called_once_with()

    asyncio.run(scenario())


def test_large_attachment_uses_link_without_download():
    async def scenario():
        forwarder, _, destination = case()
        attachment = SimpleNamespace(
            size=MAX_FILE_BYTES + 1,
            filename="large.zip",
            url="https://example.invalid/large.zip",
            to_file=AsyncMock(),
        )
        assert await forwarder.handle(message(attachments=[attachment]))
        attachment.to_file.assert_not_awaited()
        assert attachment.url in destination.send.await_args.args[0]

    asyncio.run(scenario())


def test_failed_attachment_download_uses_link():
    async def scenario():
        forwarder, _, destination = case()
        error = discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"),
            {"code": 0, "message": "missing"},
        )
        attachment = SimpleNamespace(
            size=7,
            filename="missing.txt",
            url="https://example.invalid/missing.txt",
            to_file=AsyncMock(side_effect=error),
            is_spoiler=lambda: False,
        )
        assert await forwarder.handle(message(attachments=[attachment]))
        assert attachment.url in destination.send.await_args.args[0]

    asyncio.run(scenario())


def test_send_failure_does_not_block_later_message():
    async def scenario():
        forwarder, _, destination = case()
        destination.send.side_effect = RuntimeError("send failed")
        assert not await forwarder.handle(message())
        destination.send.side_effect = None
        assert await forwarder.handle(message(id=501))

    asyncio.run(scenario())


def test_bot_destination_is_rejected():
    async def scenario():
        forwarder, _, destination = case()
        destination.bot = True
        assert not await forwarder.handle(message())
        destination.send.assert_not_awaited()

    asyncio.run(scenario())


def test_self_destination_is_rejected():
    async def scenario():
        forwarder, client, destination = case()
        client.user.id = 400
        assert not await forwarder.handle(message())
        client.fetch_user.assert_not_awaited()
        destination.send.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("enabled", [False, True])
def test_dm_intent_follows_configuration(enabled):
    async def scenario():
        settings = parse_config({
            "DISCORD_TOKEN": "fake-token",
            "TEST_GUILD_ID": "100",
            "DM_FORWARD_ENABLED": str(enabled).lower(),
            "DM_FORWARD_USER_ID": "400",
        })
        client = ConnectionCheckClient(settings)
        try:
            assert client.intents.dm_messages is enabled
        finally:
            await client.close()

    asyncio.run(scenario())


def test_bot_routes_dm_to_forwarder():
    async def scenario():
        ready = asyncio.Event()
        ready.set()
        client = SimpleNamespace(
            config=config(),
            _welcome_startup_complete=ready,
            _dm_forwarder=SimpleNamespace(handle=AsyncMock()),
            _bump_events=SimpleNamespace(on_message=AsyncMock()),
        )
        value = message()
        await WanbotClient.on_message(client, value)
        client._dm_forwarder.handle.assert_awaited_once_with(value)
        client._bump_events.on_message.assert_not_awaited()

    asyncio.run(scenario())
