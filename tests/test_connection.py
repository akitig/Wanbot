import pytest

from wanbot.config import ConfigError, parse_config
from wanbot.connection import (
    missing_guild_ids,
    validate_connection_config,
)


def make_config(**overrides):
    values = {
        "DISCORD_TOKEN": "fake-token",
        "PROD_GUILD_ID": "100",
        "TEST_GUILD_ID": "200",
    }
    values.update(overrides)
    return parse_config(values)


def test_connection_config_allows_disabled_features():
    validate_connection_config(make_config())


def test_connection_config_rejects_enabled_feature():
    config = make_config(
        TEST_ENABLED_FEATURES="bump",
        TEST_BUMP_CHANNEL_ID="210",
    )

    with pytest.raises(ConfigError, match="ENABLED_FEATURES"):
        validate_connection_config(config)


def test_connection_config_rejects_dm_forward():
    config = make_config(
        DM_FORWARD_ENABLED="true",
        DM_FORWARD_USER_ID="300",
    )

    with pytest.raises(ConfigError, match="DM_FORWARD_ENABLED"):
        validate_connection_config(config)


def test_both_guilds_are_available():
    assert missing_guild_ids(make_config(), [100, 200]) == frozenset()


def test_missing_test_guild_is_reported():
    assert missing_guild_ids(make_config(), [100]) == frozenset({200})


def test_unrelated_guild_does_not_replace_missing_guild():
    assert missing_guild_ids(make_config(), [100, 999]) == frozenset({200})


def test_extra_guild_does_not_count_as_configured():
    assert missing_guild_ids(make_config(), [100, 200, 999]) == frozenset()
