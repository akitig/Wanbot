import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.kick_review import check_review_channel


def setup():
    guild = SimpleNamespace(
        id=100,
        default_role=discord.Object(id=100, type=discord.Role),
        fetch_member=AsyncMock(return_value=SimpleNamespace(id=900)),
    )
    channel = Mock(spec=discord.TextChannel)
    channel.id = 500
    channel.guild = guild
    channel.overwrites = {}
    channel.overwrites_for.return_value = discord.PermissionOverwrite(
        view_channel=False
    )
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        read_message_history=True,
    )
    client = SimpleNamespace(user=SimpleNamespace(id=900))
    config = SimpleNamespace(
        guild_id=100,
        ids={"KICK_APPROVAL_CHANNEL_ID": 500},
        admin_role_ids=frozenset({400}),
    )
    return channel, client, config


def test_public_channel_is_rejected():
    async def scenario():
        channel, client, config = setup()
        channel.overwrites_for.return_value = discord.PermissionOverwrite(
            view_channel=True
        )
        with pytest.raises(InterviewNotReady):
            await check_review_channel(
                channel, client=client, config=None, guild_config=config
            )

    asyncio.run(scenario())


def test_unrelated_role_with_view_permission_is_rejected():
    async def scenario():
        channel, client, config = setup()
        channel.overwrites = {
            discord.Object(id=999, type=discord.Role):
                discord.PermissionOverwrite(view_channel=True)
        }
        with pytest.raises(InterviewNotReady):
            await check_review_channel(
                channel, client=client, config=None, guild_config=config
            )

    asyncio.run(scenario())


def test_configured_admin_role_is_allowed():
    async def scenario():
        channel, client, config = setup()
        channel.overwrites = {
            discord.Object(id=400, type=discord.Role):
                discord.PermissionOverwrite(view_channel=True)
        }
        await check_review_channel(
            channel, client=client, config=None, guild_config=config
        )

    asyncio.run(scenario())
