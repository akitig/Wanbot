from pathlib import Path

import pytest

from wanbot.config import ConfigError, load_config, parse_config


def base_values():
    return {
        "DISCORD_TOKEN": "fake-token-for-offline-tests",
        "TEST_GUILD_ID": "200",
    }


def test_disabled_features_need_no_channel_or_role_settings():
    config = parse_config(base_values())
    guild = config.guild_for(200)

    assert guild is not None
    assert guild.environment == "test"
    assert not guild.enabled_features
    assert not guild.ids
    assert config.guild_for(999) is None
    assert config.guild_for(None) is None


def test_production_and_test_have_separate_features():
    values = base_values()
    values.update({
        "PROD_GUILD_ID": "100",
        "TEST_ENABLED_FEATURES": "bump",
        "TEST_BUMP_CHANNEL_ID": "201",
    })
    config = parse_config(values)

    assert not config.guild_for(100).feature_enabled("bump")
    assert config.guild_for(200).feature_enabled("bump")


def test_duplicate_guild_is_rejected():
    values = base_values()
    values["PROD_GUILD_ID"] = "200"

    with pytest.raises(ConfigError, match="重複"):
        parse_config(values)


def test_enabled_feature_requires_settings():
    values = base_values()
    values["TEST_ENABLED_FEATURES"] = "bump"

    with pytest.raises(ConfigError, match="TEST_BUMP_CHANNEL_ID"):
        parse_config(values)


def test_unknown_feature_is_rejected():
    values = base_values()
    values["TEST_ENABLED_FEATURES"] = "unknown_feature"

    with pytest.raises(ConfigError, match="TEST_ENABLED_FEATURES"):
        parse_config(values)


def test_feature_requires_guild_id():
    values = {
        "DISCORD_TOKEN": "fake",
        "TEST_ENABLED_FEATURES": "bump",
    }

    with pytest.raises(ConfigError, match="TEST_GUILD_ID"):
        parse_config(values)


@pytest.mark.parametrize("bad_id", ["abc", "-1", "0", "1.5", str(2**64)])
def test_invalid_id_is_rejected_without_echoing_value(bad_id):
    values = base_values()
    values["TEST_GUILD_ID"] = bad_id

    with pytest.raises(ConfigError) as error:
        parse_config(values)

    assert str(error.value).startswith("TEST_GUILD_ID:")


def test_multiple_admin_roles():
    values = base_values()
    values.update({
        "TEST_ENABLED_FEATURES": "valorant_diagnosis",
        "TEST_ADMIN_ROLE_IDS": "301, 302",
    })
    config = parse_config(values)

    assert config.guild_for(200).admin_role_ids == frozenset({301, 302})


def test_diagnosis_requires_admin_roles():
    values = base_values()
    values["TEST_ENABLED_FEATURES"] = "valorant_diagnosis"

    with pytest.raises(ConfigError, match="TEST_ADMIN_ROLE_IDS"):
        parse_config(values)


def test_missing_token_is_rejected():
    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        parse_config({"TEST_GUILD_ID": "200"})


def test_token_is_hidden_in_config_representation():
    values = base_values()
    config = parse_config(values)

    assert values["DISCORD_TOKEN"] not in repr(config)


def test_runtime_path_is_relative_to_base_directory(tmp_path):
    config = parse_config(base_values(), base_dir=tmp_path)

    assert config.runtime_data_dir == (tmp_path / "runtime").resolve()


def test_env_file_and_environment_override(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "DISCORD_TOKEN=fake-from-file\n"
        "TEST_GUILD_ID=200\n"
        "LOG_LEVEL=INFO\n",
        encoding="utf-8",
    )

    config = load_config(path, environ={"LOG_LEVEL": "WARNING"})

    assert config.log_level == "WARNING"
    assert config.guild_for(200) is not None
    assert config.runtime_data_dir == (tmp_path / "runtime").resolve()


def test_missing_file_does_not_hide_missing_settings(tmp_path):
    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        load_config(tmp_path / "missing.env", environ={})


def test_dm_forward_requires_target():
    values = base_values()
    values["DM_FORWARD_ENABLED"] = "true"

    with pytest.raises(ConfigError, match="DM_FORWARD_USER_ID"):
        parse_config(values)


def test_relative_paths_are_paths():
    config = parse_config(base_values())
    assert isinstance(config.runtime_data_dir, Path)
