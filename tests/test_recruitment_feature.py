import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from wanbot.bot import WanbotClient
from wanbot.config import parse_config
from wanbot.recruitment.feature import RecruitmentFeature


def make_config():
    return parse_config({
        "DISCORD_TOKEN": "test-token",
        "PROD_GUILD_ID": "101",
        "PROD_ENABLED_FEATURES": "",
        "TEST_GUILD_ID": "100",
        "TEST_ENABLED_FEATURES": "recruitment",
        "TEST_VALO_RECRUIT_CHANNEL_ID": "200",
        "TEST_GACHI_ROLE_ID": "400",
        "TEST_ENJOY_ROLE_ID": "500",
    })


@contextmanager
def setup_feature():
    config = make_config()
    guild = config.guild_for(100)
    client = Mock()
    client.feature_available.return_value = True

    repository = SimpleNamespace(close=AsyncMock())
    publisher = SimpleNamespace(publish=AsyncMock())
    controller = SimpleNamespace(
        begin=AsyncMock(),
        close=Mock(),
    )
    panel = SimpleNamespace(
        ensure=AsyncMock(),
        move_to_bottom=AsyncMock(),
        close=AsyncMock(),
    )

    with (
        patch(
            "wanbot.recruitment.feature.RecruitmentPanelRepository",
            return_value=repository,
        ) as repository_factory,
        patch(
            "wanbot.recruitment.feature.RecruitmentPublisher",
            return_value=publisher,
        ) as publisher_factory,
        patch(
            "wanbot.recruitment.feature.RecruitmentController",
            return_value=controller,
        ) as controller_factory,
        patch(
            "wanbot.recruitment.feature.DiscordRecruitmentPanel",
            return_value=panel,
        ) as panel_factory,
    ):
        feature = RecruitmentFeature(client, config, guild)
        yield SimpleNamespace(
            feature=feature,
            client=client,
            config=config,
            guild=guild,
            repository=repository,
            publisher=publisher,
            controller=controller,
            panel=panel,
            repository_factory=repository_factory,
            publisher_factory=publisher_factory,
            controller_factory=controller_factory,
            panel_factory=panel_factory,
        )


def test_components_are_connected_to_same_guild():
    async def run():
        with setup_feature() as item:
            try:
                item.repository_factory.assert_called_once_with(
                    item.config,
                    100,
                )

                publisher = item.publisher_factory.call_args.kwargs
                controller = item.controller_factory.call_args.kwargs
                panel = item.panel_factory.call_args.kwargs

                assert publisher["client"] is item.client
                assert publisher["guild"] is item.guild
                assert controller["guild"] is item.guild
                assert controller["publisher"] is item.publisher

                assert panel["guild_id"] == 100
                assert panel["channel_id"] == 200
                assert panel["repository"] is item.repository
                assert panel["on_begin"] is item.controller.begin
            finally:
                await item.feature.stop()

    asyncio.run(run())


def test_start_and_stop_are_not_duplicated():
    async def run():
        with setup_feature() as item:
            await item.feature.start()
            await item.feature.start()

            item.panel.ensure.assert_awaited_once()

            await item.feature.stop()
            await item.feature.stop()

            item.controller.close.assert_called_once()
            item.panel.close.assert_awaited_once()
            item.repository.close.assert_awaited_once()

    asyncio.run(run())


def test_availability_follows_lifecycle_and_client_state():
    async def run():
        with setup_feature() as item:
            available = (
                item.publisher_factory.call_args.kwargs["is_available"]
            )

            assert available() is False

            await item.feature.start()
            assert available() is True

            item.client.feature_available.return_value = False
            assert available() is False

            item.client.feature_available.return_value = True
            await item.feature.stop()
            assert available() is False

    asyncio.run(run())


def test_failed_start_cleans_up_resources():
    async def run():
        with setup_feature() as item:
            item.panel.ensure.side_effect = RuntimeError("Restore failed")

            with pytest.raises(RuntimeError, match="Restore failed"):
                await item.feature.start()

            item.controller.close.assert_called_once()
            item.panel.close.assert_awaited_once()
            item.repository.close.assert_awaited_once()

    asyncio.run(run())


def test_repository_closes_even_if_panel_close_fails():
    async def run():
        with setup_feature() as item:
            await item.feature.start()
            item.panel.close.side_effect = RuntimeError("Close failed")

            with pytest.raises(RuntimeError, match="Close failed"):
                await item.feature.stop()

            item.repository.close.assert_awaited_once()

    asyncio.run(run())


def test_stopped_feature_cannot_restart():
    async def run():
        with setup_feature() as item:
            await item.feature.stop()

            with pytest.raises(RuntimeError):
                await item.feature.start()

            item.panel.ensure.assert_not_awaited()

    asyncio.run(run())


def test_default_registry_starts_recruitment_only_in_test_guild():
    async def run():
        config = make_config()
        feature = SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
        )

        with patch(
            "wanbot.bot.RecruitmentFeature",
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

                assert client.feature_manager.is_active(
                    100,
                    "recruitment",
                )
                assert not client.feature_manager.is_active(
                    101,
                    "recruitment",
                )
            finally:
                await client.close()

            feature.stop.assert_awaited_once()

    asyncio.run(run())
