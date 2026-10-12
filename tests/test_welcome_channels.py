import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.config import GuildConfig
from wanbot.welcome.channels import WelcomeChannelError, WelcomeChannels
from wanbot.welcome.repository import WelcomeSession


def setup_channels():
    guild = Mock(spec=discord.Guild)
    guild.id = 100
    guild.unavailable = False
    guild.default_role = Mock(spec=discord.Role)
    guild.me = Mock(spec=discord.Member)

    member = Mock(spec=discord.Member)
    member.id = 300
    member.guild = guild
    member.bot = False

    category = Mock(spec=discord.CategoryChannel)
    category.id = 200
    category.guild = guild
    guild.get_channel.return_value = category

    admin = Mock(spec=discord.Role)
    admin.id = 400
    admin.guild = guild
    admin.is_default.return_value = False
    guild.get_role.return_value = admin

    config = GuildConfig(
        environment="test",
        guild_id=100,
        enabled_features=frozenset({"welcome"}),
        admin_role_ids=frozenset({400}),
        ids={"WELCOME_CATEGORY_ID": 200},
        rules_emoji="",
        reaction_role_message_ids=frozenset(),
    )

    now = datetime.now(timezone.utc)
    current = WelcomeSession(
        session_id="a" * 32,
        user_id=300,
        channel_id=None,
        state="preparing",
        created_at=now,
        updated_at=now,
    )
    created = []

    async def begin(user_id):
        assert user_id == 300
        return current

    async def attach(session_id, channel_id):
        nonlocal current
        assert session_id == current.session_id
        current = replace(
            current,
            state="active",
            channel_id=channel_id,
        )
        return current

    repository = SimpleNamespace(
        begin=AsyncMock(side_effect=begin),
        attach_channel=AsyncMock(side_effect=attach),
    )

    async def create(**kwargs):
        channel = Mock(spec=discord.TextChannel)
        channel.id = 500
        channel.guild = guild
        channel.category_id = kwargs["category"].id
        channel.topic = kwargs["topic"]
        channel.overwrites = kwargs["overwrites"]
        created.append(channel)
        return channel

    async def fetch_channels():
        return list(created)

    async def fetch_channel(channel_id):
        return next(channel for channel in created if channel.id == channel_id)

    guild.create_text_channel = AsyncMock(side_effect=create)
    guild.fetch_channels = AsyncMock(side_effect=fetch_channels)

    client = Mock(spec=discord.Client)
    client.get_guild.return_value = guild
    client.fetch_channel = AsyncMock(side_effect=fetch_channel)

    service = WelcomeChannels(
        client=client,
        guild_config=config,
        repository=repository,
    )

    return SimpleNamespace(
        service=service,
        client=client,
        guild=guild,
        member=member,
        category=category,
        admin=admin,
        repository=repository,
        created=created,
    )


def test_channel_is_private_from_creation():
    async def run():
        item = setup_channels()

        session = await item.service.ensure(item.member)

        assert session.state == "active"
        assert session.channel_id == 500

        kwargs = item.guild.create_text_channel.call_args.kwargs
        overwrites = kwargs["overwrites"]

        assert set(overwrites) == {
            item.guild.default_role,
            item.guild.me,
            item.member,
            item.admin,
        }
        assert overwrites[item.guild.default_role].view_channel is False
        assert overwrites[item.member].view_channel is True
        assert overwrites[item.admin].view_channel is True
        assert overwrites[item.guild.me].view_channel is True
        assert kwargs["category"] is item.category

    asyncio.run(run())


def test_existing_active_channel_is_reused():
    async def run():
        item = setup_channels()

        first = await item.service.ensure(item.member)
        second = await item.service.ensure(item.member)

        assert second == first
        item.guild.create_text_channel.assert_awaited_once()
        item.client.fetch_channel.assert_awaited_once_with(500)

    asyncio.run(run())


def test_channel_is_recovered_after_save_failure():
    async def run():
        item = setup_channels()
        attach = item.repository.attach_channel.side_effect
        item.repository.attach_channel.side_effect = RuntimeError(
            "Save failed"
        )

        with pytest.raises(RuntimeError, match="Save failed"):
            await item.service.ensure(item.member)

        assert len(item.created) == 1

        item.repository.attach_channel.side_effect = attach
        session = await item.service.ensure(item.member)

        assert session.channel_id == 500
        item.guild.create_text_channel.assert_awaited_once()

    asyncio.run(run())


def test_concurrent_requests_do_not_create_two_channels():
    async def run():
        item = setup_channels()

        first, second = await asyncio.gather(
            item.service.ensure(item.member),
            item.service.ensure(item.member),
        )

        assert first == second
        item.guild.create_text_channel.assert_awaited_once()

    asyncio.run(run())


def test_member_from_other_guild_is_rejected():
    async def run():
        item = setup_channels()
        item.member.guild = SimpleNamespace(id=999)

        with pytest.raises(WelcomeChannelError):
            await item.service.ensure(item.member)

        item.repository.begin.assert_not_awaited()
        item.guild.create_text_channel.assert_not_awaited()

    asyncio.run(run())


def test_missing_admin_role_prevents_creation():
    async def run():
        item = setup_channels()
        item.guild.get_role.return_value = None

        with pytest.raises(WelcomeChannelError):
            await item.service.ensure(item.member)

        item.guild.create_text_channel.assert_not_awaited()

    asyncio.run(run())


def test_wrong_category_prevents_creation():
    async def run():
        item = setup_channels()
        item.category.guild = SimpleNamespace(id=999)

        with pytest.raises(WelcomeChannelError):
            await item.service.ensure(item.member)

        item.guild.create_text_channel.assert_not_awaited()

    asyncio.run(run())


def test_changed_permissions_are_not_silently_accepted():
    async def run():
        item = setup_channels()
        await item.service.ensure(item.member)

        item.created[0].overwrites = {}

        with pytest.raises(WelcomeChannelError):
            await item.service.ensure(item.member)

        item.guild.create_text_channel.assert_awaited_once()

    asyncio.run(run())
