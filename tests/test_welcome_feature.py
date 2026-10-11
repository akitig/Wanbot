import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from wanbot.bot import WanbotClient
from wanbot.config import ID_FIELDS, parse_config
from wanbot.welcome.channels import WelcomeChannelError
from wanbot.welcome.feature import WelcomeFeature


def make_config():
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
    return parse_config(values)


@contextmanager
def setup_feature():
    config = make_config()
    guild = config.guild_for(100)
    client = Mock()
    client.feature_available.return_value = True

    repository = SimpleNamespace(close=AsyncMock())
    channels = SimpleNamespace(ensure=AsyncMock())

    with (
        patch(
            "wanbot.welcome.feature.WelcomeRepository",
            return_value=repository,
        ) as repository_factory,
        patch(
            "wanbot.welcome.feature.WelcomeChannels",
            return_value=channels,
        ) as channels_factory,
        patch(
            "wanbot.welcome.feature.WelcomeLifecycle",
            return_value=SimpleNamespace(
                ensure=channels.ensure,
                member_left=AsyncMock(),
                resume_pending=AsyncMock(return_value=(0, 0)),
            ),
        ),
        patch(
            "wanbot.welcome.feature.register_welcome_command"
        ) as register,
    ):
        feature = WelcomeFeature(client, config, guild)
        yield SimpleNamespace(
            feature=feature,
            client=client,
            config=config,
            guild=guild,
            repository=repository,
            channels=channels,
            repository_factory=repository_factory,
            channels_factory=channels_factory,
            register=register,
        )


def test_components_use_same_guild_and_repository():
    async def run():
        with setup_feature() as item:
            try:
                item.repository_factory.assert_called_once_with(
                    item.config,
                    100,
                )
                kwargs = item.channels_factory.call_args.kwargs
                assert kwargs["client"] is item.client
                assert kwargs["guild_config"] is item.guild
                assert kwargs["repository"] is item.repository
            finally:
                await item.feature.stop()

    asyncio.run(run())


def test_start_and_stop_are_not_duplicated():
    async def run():
        with setup_feature() as item:
            await item.feature.start()
            await item.feature.start()

            item.register.assert_called_once()
            assert item.register.call_args.args == (
                item.client.tree,
                item.config,
                100,
            )

            await item.feature.stop()
            await item.feature.stop()

            item.client.tree.remove_command.assert_called_once()
            item.repository.close.assert_awaited_once()

    asyncio.run(run())


def test_active_feature_creates_welcome():
    async def run():
        with setup_feature() as item:
            member = Mock()
            expected = object()
            item.channels.ensure.return_value = expected

            try:
                await item.feature.start()
                result = await item.feature.create(member)

                assert result is expected
                item.channels.ensure.assert_awaited_once_with(member)
            finally:
                await item.feature.stop()

    asyncio.run(run())


@pytest.mark.parametrize("state", ["not_started", "unavailable", "stopped"])
def test_unavailable_feature_cannot_create(state):
    async def run():
        with setup_feature() as item:
            try:
                if state != "not_started":
                    await item.feature.start()
                if state == "unavailable":
                    item.client.feature_available.return_value = False
                elif state == "stopped":
                    await item.feature.stop()

                with pytest.raises(WelcomeChannelError):
                    await item.feature.create(Mock())

                item.channels.ensure.assert_not_awaited()
            finally:
                await item.feature.stop()

    asyncio.run(run())


def test_stop_waits_for_creation_before_closing_repository():
    async def run():
        with setup_feature() as item:
            started = asyncio.Event()
            release = asyncio.Event()

            async def ensure(member):
                started.set()
                await release.wait()
                return "finished"

            item.channels.ensure.side_effect = ensure
            await item.feature.start()
            creating = asyncio.create_task(item.feature.create(Mock()))

            await asyncio.wait_for(started.wait(), timeout=2)
            stopping = asyncio.create_task(item.feature.stop())

            try:
                await asyncio.sleep(0)
                assert not stopping.done()
                item.repository.close.assert_not_awaited()

                release.set()

                assert await asyncio.wait_for(creating, timeout=2) == "finished"
                await asyncio.wait_for(stopping, timeout=2)
                item.repository.close.assert_awaited_once()
            finally:
                release.set()
                await creating
                await stopping

    asyncio.run(run())


def test_registry_starts_welcome_only_in_enabled_guild():
    async def run():
        config = make_config()
        welcome = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        diagnosis = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())

        with (
            patch(
                "wanbot.bot.WelcomeFeature",
                return_value=welcome,
            ) as factory,
            patch(
                "wanbot.bot.ValorantDiagnosisFeature",
                return_value=diagnosis,
            ),
        ):
            client = WanbotClient(config)
            try:
                await client.feature_manager.start()

                factory.assert_called_once_with(
                    client,
                    config,
                    config.guild_for(100),
                )
                assert client.feature_manager.is_active(100, "welcome")
                assert not client.feature_manager.is_active(101, "welcome")
            finally:
                await client.close()

            welcome.stop.assert_awaited_once()

    asyncio.run(run())
