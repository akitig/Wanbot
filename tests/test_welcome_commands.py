import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest
from discord import app_commands

from wanbot.config import ID_FIELDS, parse_config
from wanbot.welcome.commands import register_welcome_command
from wanbot.welcome.repository import WelcomeSession


def setup_command():
    values = {
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "TEST_GUILD_ID": "100",
        "TEST_ENABLED_FEATURES": "welcome,valorant_diagnosis",
        "TEST_ADMIN_ROLE_IDS": "400",
        "TEST_RULES_EMOJI": "✅",
    }
    for index, name in enumerate(ID_FIELDS):
        values[f"TEST_{name}"] = str(1000 + index)

    config = parse_config(values)
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)

    now = datetime.now(timezone.utc)
    session = WelcomeSession(
        session_id="a" * 32,
        user_id=300,
        channel_id=500,
        state="active",
        created_at=now,
        updated_at=now,
    )
    create = AsyncMock(return_value=session)
    available = Mock(return_value=True)

    register_welcome_command(
        tree,
        config,
        100,
        is_available=available,
        create_welcome=create,
    )

    operator = Mock(spec=discord.Member)
    operator.id = 900
    operator.roles = [SimpleNamespace(id=400)]

    member = Mock(spec=discord.Member)
    member.id = 300
    member.guild = SimpleNamespace(id=100)
    member.bot = False

    response = SimpleNamespace(
        is_done=Mock(return_value=False),
        send_message=AsyncMock(),
    )

    async def defer(**kwargs):
        response.is_done.return_value = True

    response.defer = AsyncMock(side_effect=defer)

    interaction = SimpleNamespace(
        guild_id=100,
        user=operator,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )

    command = tree.get_command("welcome", guild=discord.Object(id=100))

    return SimpleNamespace(
        client=client,
        command=command,
        interaction=interaction,
        member=member,
        create=create,
        available=available,
    )


def test_manager_receives_private_channel_link():
    async def run():
        item = setup_command()
        try:
            await item.command.callback(item.interaction, item.member)

            item.create.assert_awaited_once_with(item.member)
            item.interaction.response.defer.assert_awaited_once_with(
                ephemeral=True,
                thinking=True,
            )
            call = item.interaction.followup.send.call_args
            assert "<#500>" in call.args[0]
            assert call.kwargs["ephemeral"] is True
            assert call.kwargs["allowed_mentions"].everyone is False
        finally:
            await item.client.close()

    asyncio.run(run())


@pytest.mark.parametrize("guild_id", [None, 101])
def test_other_context_cannot_create_welcome(guild_id):
    async def run():
        item = setup_command()
        item.interaction.guild_id = guild_id
        try:
            await item.command.callback(item.interaction, item.member)
            item.create.assert_not_awaited()
        finally:
            await item.client.close()

    asyncio.run(run())


def test_nonmanager_cannot_create_welcome():
    async def run():
        item = setup_command()
        item.interaction.user.roles = []
        try:
            await item.command.callback(item.interaction, item.member)

            item.create.assert_not_awaited()
            item.interaction.response.send_message.assert_awaited_once()
        finally:
            await item.client.close()

    asyncio.run(run())


def test_unavailable_feature_cannot_create_welcome():
    async def run():
        item = setup_command()
        item.available.return_value = False
        try:
            await item.command.callback(item.interaction, item.member)
            item.create.assert_not_awaited()
        finally:
            await item.client.close()

    asyncio.run(run())


def test_target_from_other_guild_is_rejected():
    async def run():
        item = setup_command()
        item.member.guild.id = 999
        try:
            await item.command.callback(item.interaction, item.member)
            item.create.assert_not_awaited()
        finally:
            await item.client.close()

    asyncio.run(run())


def test_bot_target_is_rejected():
    async def run():
        item = setup_command()
        item.member.bot = True
        try:
            await item.command.callback(item.interaction, item.member)
            item.create.assert_not_awaited()
        finally:
            await item.client.close()

    asyncio.run(run())


def test_creation_failure_is_reported_without_channel_link():
    async def run():
        item = setup_command()
        item.create.side_effect = RuntimeError("Create failed")
        try:
            with patch("wanbot.welcome.commands.log_event") as logger:
                await item.command.callback(item.interaction, item.member)
                logger.assert_called_once()

            text = item.interaction.followup.send.call_args.args[0]
            assert "完了できませんでした" in text
            assert "<#" not in text
        finally:
            await item.client.close()

    asyncio.run(run())


def test_invalid_result_is_not_presented_as_success():
    async def run():
        item = setup_command()
        item.create.return_value = SimpleNamespace(
            user_id=999,
            state="active",
            channel_id=500,
        )
        try:
            with patch("wanbot.welcome.commands.log_event"):
                await item.command.callback(item.interaction, item.member)

            text = item.interaction.followup.send.call_args.args[0]
            assert "<#500>" not in text
        finally:
            await item.client.close()

    asyncio.run(run())


def test_role_removed_during_defer_prevents_creation():
    async def run():
        item = setup_command()

        async def defer(**kwargs):
            item.interaction.response.is_done.return_value = True
            item.interaction.user.roles = []

        item.interaction.response.defer.side_effect = defer

        try:
            await item.command.callback(item.interaction, item.member)
            item.create.assert_not_awaited()
            item.interaction.followup.send.assert_awaited_once()
        finally:
            await item.client.close()

    asyncio.run(run())
