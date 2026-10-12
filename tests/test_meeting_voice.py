import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wanbot.welcome.interview_dates import JST
from wanbot.welcome.interview_gate import InterviewNotReady
from wanbot.welcome.meeting_voice import MeetingVoice


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def read(self):
        return deepcopy(self.data)

    async def update(self, change):
        self.data = deepcopy(change(deepcopy(self.data)))

    async def close(self):
        pass


def setup():
    guild = SimpleNamespace(id=100, unavailable=False)
    role = Mock(spec=discord.Role)
    role.id = 100
    guild.default_role = role

    admin_role = SimpleNamespace(id=400)
    members = {}
    for user_id in (200, 201, 300, 900):
        member = Mock(spec=discord.Member)
        member.id = user_id
        member.guild = guild
        member.bot = user_id == 900
        member.roles = [admin_role] if user_id in (200, 201) else []
        members[user_id] = member
    guild.fetch_member = AsyncMock(side_effect=lambda user_id: members[user_id])

    category = Mock(spec=discord.CategoryChannel)
    category.id = 600
    category.guild = guild
    category.permissions_for.return_value = SimpleNamespace(
        manage_channels=True, manage_roles=True
    )

    created = []

    async def create(**kwargs):
        channel = Mock(spec=discord.VoiceChannel)
        channel.id = 700
        channel.guild = guild
        channel.category_id = category.id
        channel.name = kwargs["name"]
        channel.overwrites = kwargs["overwrites"]

        async def edit(**options):
            channel.name = options["name"]
            return channel

        channel.edit = AsyncMock(side_effect=edit)
        created.append(channel)
        return channel

    guild.create_voice_channel = AsyncMock(side_effect=create)
    guild.fetch_channels = AsyncMock(side_effect=lambda: list(created))

    async def fetch_channel(channel_id):
        if channel_id == category.id:
            return category
        return next(item for item in created if item.id == channel_id)

    client = SimpleNamespace(
        user=SimpleNamespace(id=900),
        get_guild=lambda guild_id: guild if guild_id == 100 else None,
        fetch_channel=AsyncMock(side_effect=fetch_channel),
    )
    guild_config = SimpleNamespace(
        admin_role_ids=frozenset({400}),
        ids={"MEETING_CATEGORY_ID": 600},
        feature_enabled=lambda name: name == "welcome",
    )
    config = SimpleNamespace(
        guild_for=lambda guild_id: guild_config if guild_id == 100 else None
    )
    plan = SimpleNamespace(
        status="confirmed",
        proposal_id="a" * 32,
        welcome_session_id="b" * 32,
        guild_id=100,
        channel_id=500,
        user_id=300,
        first_manager_id=200,
        second_manager_id=201,
        meeting_at=datetime(2026, 10, 12, 21, 30, tzinfo=JST),
    )
    store = MemoryStore()
    service = MeetingVoice(
        client=client, config=config, guild_id=100, store=store
    )
    return service, guild, plan, store, client, config


def test_repeated_and_restarted_calls_reuse_same_channel():
    async def scenario():
        service, guild, plan, store, client, config = setup()
        first, second = await asyncio.gather(
            service.ensure(plan), service.ensure(plan)
        )
        restarted = MeetingVoice(
            client=client, config=config, guild_id=100, store=store
        )
        third = await restarted.ensure(plan)
        assert first.id == second.id == third.id == 700
        assert third.name == "2026-10-12 21時30分"
        guild.create_voice_channel.assert_awaited_once()

    asyncio.run(scenario())


def test_only_participants_and_bot_are_allowed():
    async def scenario():
        service, guild, plan, *_ = setup()
        channel = await service.ensure(plan)
        settings = {
            target.id: overwrite
            for target, overwrite in channel.overwrites.items()
        }
        assert set(settings) == {100, 200, 201, 300, 900}
        assert settings[100].view_channel is False
        assert settings[100].connect is False
        for user_id in (200, 201, 300, 900):
            assert settings[user_id].view_channel is True
            assert settings[user_id].connect is True
        assert guild.default_role.id == 100

    asyncio.run(scenario())


def test_unconfirmed_plan_does_not_create_channel():
    async def scenario():
        service, guild, plan, *_ = setup()
        plan.status = "waiting_second"
        with pytest.raises(InterviewNotReady):
            await service.ensure(plan)
        guild.create_voice_channel.assert_not_awaited()

    asyncio.run(scenario())


def test_uncertain_creation_does_not_repeat_request():
    async def scenario():
        service, guild, plan, *_ = setup()
        guild.create_voice_channel.side_effect = TimeoutError
        with pytest.raises(TimeoutError):
            await service.ensure(plan)
        with pytest.raises(InterviewNotReady):
            await service.ensure(plan)
        guild.create_voice_channel.assert_awaited_once()

    asyncio.run(scenario())


def test_changed_permissions_are_rejected():
    async def scenario():
        service, guild, plan, *_ = setup()
        channel = await service.ensure(plan)
        channel.overwrites[guild.default_role] = discord.PermissionOverwrite(
            view_channel=True, connect=True
        )
        with pytest.raises(InterviewNotReady):
            await service.ensure(plan)
        guild.create_voice_channel.assert_awaited_once()

    asyncio.run(scenario())
