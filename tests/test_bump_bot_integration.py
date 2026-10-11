import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from wanbot.bot import WanbotClient
from wanbot.bump.feature import BumpFeature
from wanbot.config import parse_config


def make_config(*, enabled=True):
    return parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "PROD_ENABLED_FEATURES": "",
        "TEST_GUILD_ID": "100",
        "TEST_ENABLED_FEATURES": "bump" if enabled else "",
        "TEST_BUMP_CHANNEL_ID": "200",
        "TEST_DISBOARD_BOT_ID": "300",
    })


def make_message(
    *,
    guild_id=100,
    channel_id=200,
    author_id=300,
    bot=True,
):
    return SimpleNamespace(
        id=500,
        guild=(
            SimpleNamespace(id=guild_id)
            if guild_id is not None
            else None
        ),
        channel=SimpleNamespace(id=channel_id),
        author=SimpleNamespace(id=author_id, bot=bot),
    )


def attach_fake_feature(client):
    feature = Mock(spec=BumpFeature)
    feature.handle_message = AsyncMock(return_value=True)

    client.feature_available = Mock(return_value=True)
    client.feature_manager.get_active = Mock(return_value=feature)

    return feature


def make_payload():
    return SimpleNamespace(
        guild_id=100,
        channel_id=200,
        message_id=500,
        data={"embeds": []},
    )


def make_channel():
    channel = Mock(spec=discord.TextChannel)
    channel.id = 200
    channel.guild = SimpleNamespace(id=100)
    channel.fetch_message = AsyncMock(return_value=make_message())
    return channel


@pytest.mark.parametrize("enabled", [False, True])
def test_message_intents_follow_bump_setting(enabled):
    async def run():
        client = WanbotClient(make_config(enabled=enabled))
        try:
            assert client.intents.guilds is True
            assert client.intents.guild_messages is enabled
            assert client.intents.message_content is enabled
            assert client.intents.dm_messages is False
            assert client.intents.members is False
        finally:
            await client.close()

    asyncio.run(run())


def test_manager_starts_bump_only_for_enabled_guild():
    async def run():
        config = make_config()
        feature = SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
        )

        with patch(
            "wanbot.bot.BumpFeature",
            return_value=feature,
        ) as factory:
            client = WanbotClient(config)
            try:
                await client.feature_manager.start()

                factory.assert_called_once_with(
                    client,
                    config,
                    config.guild_for(100),
                )
                feature.start.assert_awaited_once()

                assert client.feature_manager.get_active(
                    100, "bump"
                ) is feature
                assert client.feature_manager.get_active(
                    101, "bump"
                ) is None
            finally:
                await client.close()

            feature.stop.assert_awaited_once()
            assert client.feature_manager.get_active(100, "bump") is None

    asyncio.run(run())


def test_disboard_message_is_forwarded():
    async def run():
        client = WanbotClient(make_config())
        feature = attach_fake_feature(client)
        message = make_message()

        try:
            await client.on_message(message)
            feature.handle_message.assert_awaited_once_with(message)
        finally:
            await client.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"guild_id": None},
        {"guild_id": 999},
        {"guild_id": 101},
        {"channel_id": 999},
        {"author_id": 999},
        {"bot": False},
    ],
)
def test_unrelated_messages_are_ignored(changes):
    async def run():
        client = WanbotClient(make_config())
        feature = attach_fake_feature(client)

        try:
            await client.on_message(make_message(**changes))
            feature.handle_message.assert_not_awaited()
        finally:
            await client.close()

    asyncio.run(run())


def test_inactive_feature_does_not_receive_message():
    async def run():
        client = WanbotClient(make_config())
        feature = attach_fake_feature(client)
        client.feature_available.return_value = False

        try:
            await client.on_message(make_message())
            feature.handle_message.assert_not_awaited()
        finally:
            await client.close()

    asyncio.run(run())


def test_raw_edit_fetches_current_message():
    async def run():
        client = WanbotClient(make_config())
        feature = attach_fake_feature(client)
        channel = make_channel()
        client.get_channel = Mock(return_value=channel)

        try:
            await client.on_raw_message_edit(make_payload())

            channel.fetch_message.assert_awaited_once_with(500)
            feature.handle_message.assert_awaited_once_with(
                channel.fetch_message.return_value
            )
        finally:
            await client.close()

    asyncio.run(run())


def test_raw_edit_in_other_channel_does_not_fetch():
    async def run():
        client = WanbotClient(make_config())
        feature = attach_fake_feature(client)
        channel = make_channel()
        client.get_channel = Mock(return_value=channel)
        payload = make_payload()
        payload.channel_id = 999

        try:
            await client.on_raw_message_edit(payload)

            channel.fetch_message.assert_not_awaited()
            feature.handle_message.assert_not_awaited()
        finally:
            await client.close()

    asyncio.run(run())


def test_raw_edit_fetch_failure_is_logged_without_propagating():
    async def run():
        client = WanbotClient(make_config())
        feature = attach_fake_feature(client)
        channel = make_channel()
        client.get_channel = Mock(return_value=channel)

        response = SimpleNamespace(status=403, reason="Forbidden")
        channel.fetch_message.side_effect = discord.Forbidden(
            response,
            {"code": 50013, "message": "Missing Permissions"},
        )

        try:
            with patch("wanbot.bump.events.log_event") as logger:
                await client.on_raw_message_edit(make_payload())

                logger.assert_called_once()
                assert logger.call_args.args == (
                    "bump",
                    "edited_message_fetch_failed",
                )

            feature.handle_message.assert_not_awaited()
        finally:
            await client.close()

    asyncio.run(run())
