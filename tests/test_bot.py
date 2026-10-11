import asyncio
from unittest.mock import AsyncMock

import pytest

from wanbot.bot import WanbotClient, validate_implementations
from wanbot.config import ConfigError, parse_config
from wanbot.connection import ConnectionCheckClient
from wanbot.features.registry import FeatureRegistry


@pytest.fixture(autouse=True)
def prevent_command_sync_network(monkeypatch):
    monkeypatch.setattr(
        WanbotClient,
        "_sync_guild_commands",
        AsyncMock(),
    )


def make_config(**overrides):
    values = {
        "DISCORD_TOKEN": "fake-token",
        "TEST_GUILD_ID": "200",
    }
    values.update(overrides)
    return parse_config(values)


def test_empty_registry_accepts_disabled_features():
    validate_implementations(make_config(), FeatureRegistry())


def test_unimplemented_enabled_feature_is_rejected():
    config = make_config(
        TEST_ENABLED_FEATURES="leave_log",
        TEST_LEAVE_LOG_CHANNEL_ID="210",
        TEST_DISBOARD_BOT_ID="300",
    )

    with pytest.raises(ConfigError, match="leave_log"):
        validate_implementations(config, FeatureRegistry())


def test_unimplemented_dm_forward_is_rejected():
    config = make_config(
        DM_FORWARD_ENABLED="true",
        DM_FORWARD_USER_ID="300",
    )

    with pytest.raises(ConfigError, match="DM_FORWARD_ENABLED"):
        validate_implementations(config, FeatureRegistry())


def test_repeated_ready_does_not_start_features_twice(monkeypatch):
    async def scenario():
        monkeypatch.setattr(
            ConnectionCheckClient,
            "on_ready",
            AsyncMock(),
        )
        base_close = AsyncMock()
        monkeypatch.setattr(ConnectionCheckClient, "close", base_close)

        client = WanbotClient(make_config(), FeatureRegistry())
        client.feature_manager.start = AsyncMock()
        client.feature_manager.stop = AsyncMock()

        await client.on_ready()
        await client.on_ready()

        client.feature_manager.start.assert_awaited_once()

        await client.close()

        client.feature_manager.stop.assert_awaited_once()
        base_close.assert_awaited_once()

    asyncio.run(scenario())


def test_failed_validation_does_not_start_features(monkeypatch):
    async def scenario():
        async def failed_ready(self):
            self.connection_check_failed = True

        monkeypatch.setattr(
            ConnectionCheckClient,
            "on_ready",
            failed_ready,
        )
        monkeypatch.setattr(
            ConnectionCheckClient,
            "close",
            AsyncMock(),
        )

        client = WanbotClient(make_config(), FeatureRegistry())
        client.feature_manager.start = AsyncMock()

        await client.on_ready()

        client.feature_manager.start.assert_not_awaited()
        assert not client.feature_available(200, "bump")
        await client.close()

    asyncio.run(scenario())
