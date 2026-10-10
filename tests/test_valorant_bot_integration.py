import asyncio
from unittest.mock import AsyncMock

import discord
import pytest

from wanbot.bot import WanbotClient
from wanbot.config import parse_config
from wanbot.connection import ConnectionCheckClient


def make_config(tmp_path, prod_enabled=False, test_enabled=True):
    return parse_config(
        {
            "DISCORD_TOKEN": "unit-test-token",
            "PROD_GUILD_ID": "100",
            "PROD_ADMIN_ROLE_IDS": "101",
            "PROD_ENABLED_FEATURES": (
                "valorant_diagnosis" if prod_enabled else ""
            ),
            "TEST_GUILD_ID": "200",
            "TEST_ADMIN_ROLE_IDS": "201",
            "TEST_ENABLED_FEATURES": (
                "valorant_diagnosis" if test_enabled else ""
            ),
            "RUNTIME_DATA_DIR": str(tmp_path),
        }
    )


def command_names(client, guild_id):
    return {
        command.name
        for command in client.tree.get_commands(
            guild=discord.Object(id=guild_id)
        )
    }


@pytest.mark.parametrize(
    ("prod_enabled", "test_enabled"),
    [
        (True, False),
        (False, True),
        (True, True),
        (False, False),
    ],
)
def test_diagnosis_starts_only_in_enabled_guilds(
    tmp_path,
    monkeypatch,
    prod_enabled,
    test_enabled,
):
    monkeypatch.setattr(ConnectionCheckClient, "on_ready", AsyncMock())

    async def scenario():
        config = make_config(tmp_path, prod_enabled, test_enabled)
        async with WanbotClient(config) as client:
            client.tree.sync = AsyncMock(return_value=[])

            await client.on_ready()

            expected_prod = (
                {"valo_role", "valo_role_log"} if prod_enabled else set()
            )
            expected_test = {"wanbot_status"}
            if test_enabled:
                expected_test.update({"valo_role", "valo_role_log"})

            assert command_names(client, 100) == expected_prod
            assert command_names(client, 200) == expected_test
            assert client.tree.get_commands() == []

            assert client.feature_manager.is_active(
                100, "valorant_diagnosis"
            ) is prod_enabled
            assert client.feature_manager.is_active(
                200, "valorant_diagnosis"
            ) is test_enabled

            targets = [
                call.kwargs["guild"].id
                for call in client.tree.sync.await_args_list
            ]
            assert targets == [100, 200]
            assert client._features_started

    asyncio.run(scenario())


def test_sync_failure_stops_started_features(tmp_path, monkeypatch):
    monkeypatch.setattr(ConnectionCheckClient, "on_ready", AsyncMock())

    async def scenario():
        async with WanbotClient(make_config(tmp_path)) as client:
            client.tree.sync = AsyncMock(
                side_effect=RuntimeError("simulated sync failure")
            )

            await client.on_ready()

            assert client.connection_check_failed
            assert client.is_closed()
            assert not client._features_started
            assert not client.feature_manager.is_active(
                200, "valorant_diagnosis"
            )

    asyncio.run(scenario())
