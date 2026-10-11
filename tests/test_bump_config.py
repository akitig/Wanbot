import pytest

from wanbot.config import ConfigError, load_config, parse_config


def base_values():
    return {
        "DISCORD_TOKEN": "test-token",
        "TEST_GUILD_ID": "100",
        "TEST_ENABLED_FEATURES": "bump",
        "TEST_BUMP_CHANNEL_ID": "200",
        "TEST_DISBOARD_BOT_ID": "300",
    }


def test_bump_defaults():
    config = parse_config(base_values())
    guild = config.guild_for(100)

    assert guild.feature_enabled("bump")
    assert guild.ids["BUMP_CHANNEL_ID"] == 200
    assert guild.disboard_bot_id == 300
    assert guild.disboard_bump_command_id is None
    assert guild.bump_cooldown_seconds == 7200


def test_explicit_bump_settings():
    values = base_values()
    values.update({
        "TEST_DISBOARD_BUMP_COMMAND_ID": "400",
        "TEST_BUMP_COOLDOWN_SECONDS": "60",
    })

    guild = parse_config(values).guild_for(100)

    assert guild.disboard_bump_command_id == 400
    assert guild.bump_cooldown_seconds == 60


def test_blank_cooldown_uses_default():
    values = base_values()
    values["TEST_BUMP_COOLDOWN_SECONDS"] = " "

    guild = parse_config(values).guild_for(100)

    assert guild.bump_cooldown_seconds == 7200


def test_enabled_bump_requires_disboard_bot_id():
    values = base_values()
    del values["TEST_DISBOARD_BOT_ID"]

    with pytest.raises(ConfigError, match="TEST_DISBOARD_BOT_ID"):
        parse_config(values)


def test_enabled_bump_still_requires_channel_id():
    values = base_values()
    del values["TEST_BUMP_CHANNEL_ID"]

    with pytest.raises(ConfigError, match="TEST_BUMP_CHANNEL_ID"):
        parse_config(values)


@pytest.mark.parametrize(
    "raw",
    [
        "0",
        "-1",
        "1.5",
        "abc",
        "７２００",
        "2147483648",
        "9" * 100,
    ],
)
def test_invalid_cooldown_is_rejected(raw):
    values = base_values()
    values["TEST_BUMP_COOLDOWN_SECONDS"] = raw

    with pytest.raises(ConfigError, match="TEST_BUMP_COOLDOWN_SECONDS"):
        parse_config(values)


@pytest.mark.parametrize(
    "key",
    [
        "TEST_DISBOARD_BOT_ID",
        "TEST_DISBOARD_BUMP_COMMAND_ID",
    ],
)
@pytest.mark.parametrize("raw", ["0", "abc", str(1 << 64)])
def test_invalid_disboard_ids_are_rejected(key, raw):
    values = base_values()
    values[key] = raw

    with pytest.raises(ConfigError, match=key):
        parse_config(values)


def test_disabled_bump_ignores_its_new_settings():
    values = base_values()
    values.update({
        "TEST_ENABLED_FEATURES": "",
        "TEST_DISBOARD_BOT_ID": "invalid",
        "TEST_DISBOARD_BUMP_COMMAND_ID": "invalid",
        "TEST_BUMP_COOLDOWN_SECONDS": "invalid",
    })

    guild = parse_config(values).guild_for(100)

    assert not guild.feature_enabled("bump")
    assert guild.disboard_bot_id is None
    assert guild.disboard_bump_command_id is None
    assert guild.bump_cooldown_seconds == 7200


def test_prod_and_test_settings_are_separate():
    values = base_values()
    values.update({
        "TEST_DISBOARD_BUMP_COMMAND_ID": "400",
        "TEST_BUMP_COOLDOWN_SECONDS": "60",
        "PROD_GUILD_ID": "101",
        "PROD_ENABLED_FEATURES": "bump",
        "PROD_BUMP_CHANNEL_ID": "201",
        "PROD_DISBOARD_BOT_ID": "301",
        "PROD_DISBOARD_BUMP_COMMAND_ID": "401",
        "PROD_BUMP_COOLDOWN_SECONDS": "7200",
    })

    config = parse_config(values)
    prod = config.guild_for(101)
    test = config.guild_for(100)

    assert prod.disboard_bot_id == 301
    assert test.disboard_bot_id == 300
    assert prod.disboard_bump_command_id == 401
    assert test.disboard_bump_command_id == 400
    assert prod.bump_cooldown_seconds == 7200
    assert test.bump_cooldown_seconds == 60


def test_bump_settings_are_loaded_from_env_file(tmp_path):
    values = base_values()
    values["TEST_BUMP_COOLDOWN_SECONDS"] = "90"
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()),
        encoding="utf-8",
    )

    config = load_config(path, environ={})

    assert config.guild_for(100).bump_cooldown_seconds == 90
