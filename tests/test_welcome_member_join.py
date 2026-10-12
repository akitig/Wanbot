import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wanbot.bot import WanbotClient
from wanbot.welcome.feature import WelcomeFeature
from wanbot.welcome.naming import welcome_channel_name


def make_case():
    feature = Mock(spec=WelcomeFeature)
    feature.create = AsyncMock()

    guild_config = SimpleNamespace(
        feature_enabled=lambda name: name == "welcome",
    )
    config = SimpleNamespace(
        guild_for=lambda guild_id: (
            guild_config if guild_id == 100 else None
        ),
    )
    manager = SimpleNamespace(
        get_active=Mock(return_value=feature),
    )
    ready = asyncio.Event()
    ready.set()

    client = SimpleNamespace(
        config=config,
        feature_manager=manager,
        _welcome_startup_complete=ready,
        feature_available=Mock(return_value=True),
        is_closed=Mock(return_value=False),
    )
    member = SimpleNamespace(
        id=300,
        guild=SimpleNamespace(id=100),
        bot=False,
    )
    return client, member, feature, guild_config


def test_join_creates_welcome():
    async def run():
        client, member, feature, _ = make_case()

        await WanbotClient.on_member_join(client, member)

        feature.create.assert_awaited_once_with(member)
        client.feature_manager.get_active.assert_called_once_with(
            100, "welcome"
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    ["bot", "unregistered", "disabled", "closed", "unavailable"],
)
def test_join_ignores_ineligible_member(case):
    async def run():
        client, member, feature, guild_config = make_case()

        if case == "bot":
            member.bot = True
        elif case == "unregistered":
            member.guild.id = 999
        elif case == "disabled":
            guild_config.feature_enabled = lambda name: False
        elif case == "closed":
            client.is_closed.return_value = True
        elif case == "unavailable":
            client.feature_available.return_value = False

        await WanbotClient.on_member_join(client, member)

        feature.create.assert_not_awaited()

    asyncio.run(run())


def test_join_waits_for_startup():
    async def run():
        client, member, feature, _ = make_case()
        client._welcome_startup_complete.clear()

        task = asyncio.create_task(
            WanbotClient.on_member_join(client, member)
        )
        try:
            await asyncio.sleep(0)
            feature.create.assert_not_awaited()

            client._welcome_startup_complete.set()
            await task

            feature.create.assert_awaited_once_with(member)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_join_rechecks_availability_after_wait():
    async def run():
        client, member, feature, _ = make_case()
        client._welcome_startup_complete.clear()

        task = asyncio.create_task(
            WanbotClient.on_member_join(client, member)
        )
        try:
            await asyncio.sleep(0)
            client.feature_available.return_value = False
            client._welcome_startup_complete.set()
            await task

            feature.create.assert_not_awaited()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_creation_failure_does_not_escape_event_handler():
    async def run():
        client, member, feature, _ = make_case()
        feature.create.side_effect = RuntimeError("test failure")

        await WanbotClient.on_member_join(client, member)

        feature.create.assert_awaited_once_with(member)

    asyncio.run(run())


def test_channel_name_uses_display_name_and_username():
    member = SimpleNamespace(display_name="おおかみ", name="wawolf_")

    assert welcome_channel_name(member) == "おおかみ-wawolf_"


def test_channel_name_normalizes_whitespace():
    member = SimpleNamespace(display_name="おお かみ\n", name="wawolf_")

    assert welcome_channel_name(member) == "おお-かみ--wawolf_"


def test_channel_name_removes_control_characters():
    member = SimpleNamespace(display_name="おお\u200bかみ", name="wawolf_")

    assert welcome_channel_name(member) == "おおかみ-wawolf_"


def test_channel_name_has_length_limit():
    member = SimpleNamespace(display_name="あ" * 120, name="wawolf_")

    assert len(welcome_channel_name(member)) == 100
