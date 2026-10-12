import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from wanbot.welcome.meeting_voice_cleanup import MeetingVoiceCleanup


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
    now = [0.0]
    session = SimpleNamespace(
        session_id="a" * 32, channel_id=500, state="closed"
    )
    record = {
        "proposal_id": "b" * 32,
        "channel_id": 700,
        "identity": {
            "session_id": session.session_id,
            "participants": [300, 200],
            "welcome_channel_id": 500,
            "category_id": 600,
        },
    }
    channel = Mock(spec=discord.VoiceChannel)
    channel.id = 700
    channel.guild = SimpleNamespace(id=100)
    channel.category_id = 600
    channel.voice_states = {}
    channel.delete = AsyncMock()

    client = SimpleNamespace(
        fetch_channel=AsyncMock(return_value=channel)
    )
    store = MemoryStore()
    cleaner = MeetingVoiceCleanup(
        client=client,
        config=None,
        guild_id=100,
        voices=SimpleNamespace(
            cleanup_records=AsyncMock(return_value=(record,))
        ),
        welcome_repository=SimpleNamespace(
            history=AsyncMock(return_value=(session,))
        ),
        is_available=lambda: True,
        store=store,
        clock=lambda: now[0],
    )
    return cleaner, now, session, channel, store, client


def test_delete_after_five_minutes_and_only_once():
    async def scenario():
        cleaner, now, _, channel, store, _ = setup()
        await cleaner.tick()
        now[0] = 299
        await cleaner.tick()
        channel.delete.assert_not_awaited()

        now[0] = 300
        await cleaner.tick()
        await cleaner.tick()
        channel.delete.assert_awaited_once()
        assert "700" in store.data["deleted"]

    asyncio.run(scenario())


def test_active_welcome_is_not_deleted():
    async def scenario():
        cleaner, now, session, channel, _, _ = setup()
        session.state = "active"
        await cleaner.tick()
        now[0] = 1000
        await cleaner.tick()
        channel.delete.assert_not_awaited()

    asyncio.run(scenario())


def test_occupied_channel_is_not_deleted():
    async def scenario():
        cleaner, now, _, channel, _, _ = setup()
        channel.voice_states = {300: object()}
        await cleaner.tick()
        now[0] = 1000
        await cleaner.tick()
        channel.delete.assert_not_awaited()

    asyncio.run(scenario())


def test_entering_and_leaving_restarts_countdown():
    async def scenario():
        cleaner, now, _, channel, _, _ = setup()
        await cleaner.tick()
        now[0] = 250
        empty = SimpleNamespace(channel=None)
        inside = SimpleNamespace(channel=channel)
        cleaner.voice_changed(empty, inside)
        cleaner.voice_changed(inside, empty)
        await cleaner.tick()

        now[0] = 300
        await cleaner.tick()
        channel.delete.assert_not_awaited()

        now[0] = 550
        await cleaner.tick()
        channel.delete.assert_awaited_once()

    asyncio.run(scenario())


def test_disconnect_reset_requires_another_five_minutes():
    async def scenario():
        cleaner, now, _, channel, _, _ = setup()
        await cleaner.tick()
        now[0] = 290
        cleaner.reset()
        await cleaner.tick()
        now[0] = 300
        await cleaner.tick()
        channel.delete.assert_not_awaited()
        now[0] = 590
        await cleaner.tick()
        channel.delete.assert_awaited_once()

    asyncio.run(scenario())


def test_activity_during_final_check_prevents_delete():
    async def scenario():
        cleaner, now, _, channel, _, client = setup()
        await cleaner.tick()
        now[0] = 300
        calls = 0

        async def fetch(channel_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                cleaner.voice_changed(
                    SimpleNamespace(channel=None),
                    SimpleNamespace(channel=channel),
                )
            return channel

        client.fetch_channel.side_effect = fetch
        await cleaner.tick()
        channel.delete.assert_not_awaited()

    asyncio.run(scenario())
