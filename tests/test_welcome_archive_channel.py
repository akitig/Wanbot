import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.config import GuildConfig
from wanbot.welcome.archive import WelcomeArchiveChannel
from wanbot.welcome.channels import WelcomeChannelError
from wanbot.welcome.repository import WelcomeSession


def make_case():
    guild = Mock(spec=discord.Guild)
    guild.id = 100
    guild.unavailable = False
    guild.default_role = Mock(spec=discord.Role)
    guild.default_role.id = 100
    guild.me = Mock(spec=discord.Member)
    guild.me.id = 900

    category = Mock(spec=discord.CategoryChannel)
    category.id = 201
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
        ids={"WELCOME_CATEGORY_ID": 200, "LOG_CATEGORY_ID": 201},
        rules_emoji="",
        reaction_role_message_ids=frozenset(),
    )

    now = datetime.now(timezone.utc)
    session = WelcomeSession(
        session_id="a" * 32,
        user_id=300,
        channel_id=500,
        state="archiving",
        created_at=now,
        updated_at=now,
        closed_reason="member_left",
    )

    channel = Mock(spec=discord.TextChannel)
    channel.id = 500
    channel.guild = guild
    channel.category_id = 200
    channel.topic = f"wanbot:welcome:100:{session.session_id}"
    channel.overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False
        ),
        discord.Object(id=300, type=discord.abc.User):
            discord.PermissionOverwrite(view_channel=True),
        discord.Object(id=301, type=discord.abc.User):
            discord.PermissionOverwrite(view_channel=True),
    }

    async def edit(**kwargs):
        channel.category_id = kwargs["category"].id
        channel.overwrites = kwargs["overwrites"]
        return channel

    channel.edit = AsyncMock(side_effect=edit)
    guild.fetch_channels = AsyncMock(return_value=[channel])

    client = Mock(spec=discord.Client)
    client.get_guild.return_value = guild
    client.fetch_channel = AsyncMock(return_value=channel)

    service = WelcomeArchiveChannel(
        client=client, guild_config=config
    )
    return SimpleNamespace(
        service=service,
        client=client,
        guild=guild,
        category=category,
        admin=admin,
        channel=channel,
        session=session,
    )


def test_archive_removes_member_access_and_moves_to_log():
    async def run():
        item = make_case()

        assert await item.service.archive(item.session) == 500

        assert item.channel.category_id == 201
        assert set(item.channel.overwrites) == {
            item.guild.default_role,
            item.guild.me,
            item.admin,
        }
        assert (
            item.channel.overwrites[item.guild.default_role].view_channel
            is False
        )
        assert item.channel.overwrites[item.admin].view_channel is True
        assert item.channel.overwrites[item.guild.me].view_channel is True
        assert item.channel.edit.call_args.kwargs["sync_permissions"] is False

    asyncio.run(run())


def test_retry_after_success_does_not_edit_again():
    async def run():
        item = make_case()

        await item.service.archive(item.session)
        await item.service.archive(item.session)

        item.channel.edit.assert_awaited_once()

    asyncio.run(run())


def test_retry_after_edit_failure():
    async def run():
        item = make_case()
        original = item.channel.edit.side_effect
        item.channel.edit.side_effect = RuntimeError("edit failed")

        with pytest.raises(RuntimeError):
            await item.service.archive(item.session)

        item.channel.edit.side_effect = original

        assert await item.service.archive(item.session) == 500

    asyncio.run(run())


@pytest.mark.parametrize("field", ["topic", "guild", "category"])
def test_wrong_channel_is_not_modified(field):
    async def run():
        item = make_case()

        if field == "topic":
            item.channel.topic = "unrelated"
        elif field == "guild":
            item.channel.guild = SimpleNamespace(id=999)
        else:
            item.channel.category_id = 999

        with pytest.raises(WelcomeChannelError):
            await item.service.archive(item.session)

        item.channel.edit.assert_not_awaited()

    asyncio.run(run())


def test_missing_manager_role_prevents_edit():
    async def run():
        item = make_case()
        item.guild.get_role.return_value = None

        with pytest.raises(WelcomeChannelError):
            await item.service.archive(item.session)

        item.channel.edit.assert_not_awaited()

    asyncio.run(run())


def test_permission_change_must_be_confirmed():
    async def run():
        item = make_case()

        async def incomplete_edit(**kwargs):
            item.channel.category_id = kwargs["category"].id
            return item.channel

        item.channel.edit.side_effect = incomplete_edit

        with pytest.raises(WelcomeChannelError):
            await item.service.archive(item.session)

    asyncio.run(run())


def test_channel_without_saved_id_is_found_by_topic():
    async def run():
        item = make_case()
        session = replace(item.session, channel_id=None)

        assert await item.service.archive(session) == 500
        item.guild.fetch_channels.assert_awaited_once()

    asyncio.run(run())


def test_no_channel_created_returns_none():
    async def run():
        item = make_case()
        session = replace(item.session, channel_id=None)
        item.guild.fetch_channels.return_value = []

        assert await item.service.archive(session) is None
        item.channel.edit.assert_not_awaited()

    asyncio.run(run())


def test_duplicate_topics_are_rejected():
    async def run():
        item = make_case()
        session = replace(item.session, channel_id=None)
        item.guild.fetch_channels.return_value = [
            item.channel, item.channel
        ]

        with pytest.raises(WelcomeChannelError):
            await item.service.archive(session)

        item.channel.edit.assert_not_awaited()

    asyncio.run(run())


def test_active_session_cannot_be_archived_directly():
    async def run():
        item = make_case()
        session = replace(
            item.session, state="active", closed_reason=None
        )

        with pytest.raises(WelcomeChannelError):
            await item.service.archive(session)

        item.client.fetch_channel.assert_not_awaited()

    asyncio.run(run())
