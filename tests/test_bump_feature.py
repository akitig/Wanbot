import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from wanbot.bump.feature import BumpFeature
from wanbot.config import parse_config


@contextmanager
def setup_feature():
    config = parse_config({
        "DISCORD_TOKEN": "test-token",
        "TEST_GUILD_ID": "100",
        "TEST_ENABLED_FEATURES": "bump",
        "TEST_BUMP_CHANNEL_ID": "200",
        "TEST_DISBOARD_BOT_ID": "300",
        "TEST_DISBOARD_BUMP_COMMAND_ID": "400",
        "TEST_BUMP_COOLDOWN_SECONDS": "60",
    })
    guild = config.guild_for(100)
    client = Mock()
    client.feature_available.return_value = True

    repository = Mock()
    guide = SimpleNamespace(handle=AsyncMock())
    panel = SimpleNamespace(
        render=AsyncMock(),
        close=AsyncMock(),
    )
    service = SimpleNamespace(
        start=AsyncMock(),
        close=AsyncMock(),
        handle_message=AsyncMock(return_value=True),
    )

    with (
        patch(
            "wanbot.bump.feature.BumpRepository",
            return_value=repository,
        ) as repository_factory,
        patch(
            "wanbot.bump.feature.BumpGuideController",
            return_value=guide,
        ) as guide_factory,
        patch(
            "wanbot.bump.feature.DiscordBumpPanel",
            return_value=panel,
        ) as panel_factory,
        patch(
            "wanbot.bump.feature.BumpService",
            return_value=service,
        ) as service_factory,
        patch("wanbot.bump.feature.log_event") as logger,
    ):
        feature = BumpFeature(client, config, guild)

        yield SimpleNamespace(
            feature=feature,
            client=client,
            config=config,
            repository=repository,
            guide=guide,
            panel=panel,
            service=service,
            repository_factory=repository_factory,
            guide_factory=guide_factory,
            panel_factory=panel_factory,
            service_factory=service_factory,
            logger=logger,
        )


def test_settings_and_shared_repository_are_connected():
    async def run():
        with setup_feature() as item:
            try:
                item.repository_factory.assert_called_once_with(
                    item.config,
                    100,
                )

                guide = item.guide_factory.call_args.kwargs
                panel = item.panel_factory.call_args.kwargs
                service = item.service_factory.call_args.kwargs

                assert guide["guild_id"] == 100
                assert guide["channel_id"] == 200
                assert guide["command_id"] == 400

                assert panel["guild_id"] == 100
                assert panel["channel_id"] == 200
                assert panel["on_guide"] is item.guide.handle

                assert service["disboard_bot_id"] == 300
                assert service["cooldown_seconds"] == 60
                assert service["render_panel"] is item.panel.render

                assert guide["repository"] is item.repository
                assert panel["repository"] is item.repository
                assert service["repository"] is item.repository
            finally:
                await item.feature.stop()

    asyncio.run(run())


def test_repeated_start_and_stop_do_not_duplicate_operations():
    async def run():
        with setup_feature() as item:
            await item.feature.start()
            await item.feature.start()

            item.service.start.assert_awaited_once()

            await item.feature.stop()
            await item.feature.stop()

            item.service.close.assert_awaited_once()
            item.panel.close.assert_awaited_once()

    asyncio.run(run())


def test_active_feature_forwards_message():
    async def run():
        with setup_feature() as item:
            message = Mock()

            try:
                await item.feature.start()

                result = await item.feature.handle_message(message)

                assert result is True
                item.service.handle_message.assert_awaited_once_with(
                    message
                )
                item.client.feature_available.assert_called_with(
                    100,
                    "bump",
                )
            finally:
                await item.feature.stop()

    asyncio.run(run())


def test_messages_before_start_and_after_stop_are_ignored():
    async def run():
        with setup_feature() as item:
            assert await item.feature.handle_message(Mock()) is False

            await item.feature.start()
            await item.feature.stop()

            assert await item.feature.handle_message(Mock()) is False
            item.service.handle_message.assert_not_awaited()

    asyncio.run(run())


def test_unavailable_feature_does_not_forward_message():
    async def run():
        with setup_feature() as item:
            try:
                await item.feature.start()
                item.client.feature_available.return_value = False

                assert await item.feature.handle_message(Mock()) is False
                item.service.handle_message.assert_not_awaited()
            finally:
                await item.feature.stop()

    asyncio.run(run())


def test_message_error_is_logged_without_propagating():
    async def run():
        with setup_feature() as item:
            error = RuntimeError("Processing failed")
            item.service.handle_message.side_effect = error

            try:
                await item.feature.start()

                assert await item.feature.handle_message(Mock()) is False

                item.logger.assert_called_once()
                args = item.logger.call_args.args
                kwargs = item.logger.call_args.kwargs
                assert args == ("bump", "message_processing_failed")
                assert kwargs["guild_id"] == 100
                assert kwargs["error"] is error
            finally:
                await item.feature.stop()

    asyncio.run(run())


def test_failed_start_closes_partial_resources():
    async def run():
        with setup_feature() as item:
            item.service.start.side_effect = RuntimeError("Start failed")

            with pytest.raises(RuntimeError, match="Start failed"):
                await item.feature.start()

            item.service.close.assert_awaited_once()
            item.panel.close.assert_awaited_once()
            assert await item.feature.handle_message(Mock()) is False

            await item.feature.stop()
            item.service.close.assert_awaited_once()

    asyncio.run(run())


def test_panel_is_closed_even_if_service_close_fails():
    async def run():
        with setup_feature() as item:
            await item.feature.start()
            item.service.close.side_effect = RuntimeError("Close failed")

            with pytest.raises(RuntimeError, match="Close failed"):
                await item.feature.stop()

            item.panel.close.assert_awaited_once()
            assert await item.feature.handle_message(Mock()) is False

    asyncio.run(run())
