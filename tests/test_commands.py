import asyncio
from unittest.mock import AsyncMock

import discord

from wanbot.bot import WanbotClient
from wanbot.commands import status_access_error
from wanbot.config import parse_config
from wanbot.features.registry import FeatureRegistry


def make_config():
    return parse_config({
        "DISCORD_TOKEN": "fake-token",
        "PROD_GUILD_ID": "100",
        "PROD_ADMIN_ROLE_IDS": "101",
        "TEST_GUILD_ID": "200",
        "TEST_ADMIN_ROLE_IDS": "201",
    })


def test_test_manager_is_allowed():
    assert status_access_error(make_config(), 200, [201]) is None


def test_test_non_manager_is_denied():
    assert status_access_error(make_config(), 200, [999]) is not None


def test_production_manager_is_denied():
    assert status_access_error(make_config(), 100, [101]) is not None


def test_dm_is_denied():
    assert status_access_error(make_config(), None, [201]) is not None


def test_unregistered_guild_is_denied():
    assert status_access_error(make_config(), 999, [201]) is not None


def test_status_command_is_registered_only_in_test():
    async def scenario():
        async with WanbotClient(make_config(), FeatureRegistry()) as client:
            test_commands = client.tree.get_commands(
                guild=discord.Object(id=200)
            )
            production_commands = client.tree.get_commands(
                guild=discord.Object(id=100)
            )

            assert [command.name for command in test_commands] == ["wanbot_status"]
            assert production_commands == []
            assert client.tree.get_commands() == []

    asyncio.run(scenario())


def test_sync_targets_only_test_guild():
    async def scenario():
        async with WanbotClient(make_config(), FeatureRegistry()) as client:
            client.tree.sync = AsyncMock(return_value=[])

            await client._sync_test_commands()

            client.tree.sync.assert_awaited_once()
            target = client.tree.sync.await_args.kwargs["guild"]
            assert target.id == 200

    asyncio.run(scenario())
