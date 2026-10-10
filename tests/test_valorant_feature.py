import asyncio
from unittest.mock import Mock

import discord
import pytest
from discord import app_commands

from wanbot.config import ConfigError, parse_config
from wanbot.valorant.feature import ValorantDiagnosisFeature


def values(tmp_path):
    return {
        "DISCORD_TOKEN": "unit-test-token",
        "PROD_GUILD_ID": "100",
        "PROD_ADMIN_ROLE_IDS": "1000",
        "PROD_ENABLED_FEATURES": "valorant_diagnosis",
        "TEST_GUILD_ID": "200",
        "TEST_ADMIN_ROLE_IDS": "2000",
        "TEST_ENABLED_FEATURES": "valorant_diagnosis",
        "RUNTIME_DATA_DIR": str(tmp_path),
    }


def test_timeout_defaults_to_thirty_minutes(tmp_path):
    config = parse_config(values(tmp_path))

    assert config.guild_for(100).valorant_timeout_seconds == 1800
    assert config.guild_for(200).valorant_timeout_seconds == 1800


def test_guilds_can_use_different_timeouts(tmp_path):
    settings = values(tmp_path)
    settings["PROD_VALO_DIAGNOSIS_TIMEOUT_SECONDS"] = "1800"
    settings["TEST_VALO_DIAGNOSIS_TIMEOUT_SECONDS"] = "60"

    config = parse_config(settings)

    assert config.guild_for(100).valorant_timeout_seconds == 1800
    assert config.guild_for(200).valorant_timeout_seconds == 60


@pytest.mark.parametrize("invalid", ["0", "-1", "1.5", "abc", "2147483648"])
def test_enabled_diagnosis_rejects_invalid_timeout(tmp_path, invalid):
    settings = values(tmp_path)
    settings["TEST_VALO_DIAGNOSIS_TIMEOUT_SECONDS"] = invalid

    with pytest.raises(ConfigError):
        parse_config(settings)


def test_disabled_diagnosis_ignores_unused_timeout(tmp_path):
    settings = values(tmp_path)
    settings["TEST_ENABLED_FEATURES"] = ""
    settings["TEST_VALO_DIAGNOSIS_TIMEOUT_SECONDS"] = "unused"

    config = parse_config(settings)

    assert config.guild_for(200).valorant_timeout_seconds == 1800


def test_start_and_stop_affect_only_target_guild(tmp_path):
    async def scenario():
        config = parse_config(values(tmp_path))
        client = discord.Client(intents=discord.Intents.none())
        client.tree = app_commands.CommandTree(client)
        client.feature_available = Mock(return_value=True)

        prod = ValorantDiagnosisFeature(client, config, config.guild_for(100))
        test = ValorantDiagnosisFeature(client, config, config.guild_for(200))

        try:
            await prod.start()
            await test.start()
            await test.start()

            assert client.tree.get_command(
                "valo_role", guild=discord.Object(id=100)
            ) is not None
            assert client.tree.get_command(
                "valo_role", guild=discord.Object(id=200)
            ) is not None
            assert client.tree.get_command("valo_role") is None

            await test.stop()

            assert client.tree.get_command(
                "valo_role", guild=discord.Object(id=200)
            ) is None
            assert client.tree.get_command(
                "valo_role", guild=discord.Object(id=100)
            ) is not None
        finally:
            await test.stop()
            await prod.stop()
            await client.close()

    asyncio.run(scenario())


def test_feature_requires_runtime_availability(tmp_path):
    async def scenario():
        config = parse_config(values(tmp_path))
        client = discord.Client(intents=discord.Intents.none())
        client.tree = app_commands.CommandTree(client)
        client.feature_available = Mock(return_value=False)
        feature = ValorantDiagnosisFeature(client, config, config.guild_for(200))

        try:
            assert not feature._available()

            await feature.start()
            assert not feature._available()

            client.feature_available.return_value = True
            assert feature._available()

            await feature.stop()
            assert not feature._available()
        finally:
            await feature.stop()
            await client.close()

    asyncio.run(scenario())


def test_stopped_feature_cannot_restart(tmp_path):
    async def scenario():
        config = parse_config(values(tmp_path))
        client = discord.Client(intents=discord.Intents.none())
        client.tree = app_commands.CommandTree(client)
        client.feature_available = Mock(return_value=True)
        feature = ValorantDiagnosisFeature(client, config, config.guild_for(200))

        try:
            await feature.stop()
            await feature.stop()

            with pytest.raises(RuntimeError):
                await feature.start()
        finally:
            await client.close()

    asyncio.run(scenario())
